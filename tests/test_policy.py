from datetime import date

from app import db, policy
from app.policy import RunCtx
from tests.conftest import add_entry

DAY = date.today().isoformat()


def cap_ctx(text="receipt", searched=True, found=()):
    return RunCtx(run_id="t", task="capture", agent="capture_agent", utterance=text, searched=searched, found_ids=set(found))


def check(agent, tool, args, ctx):
    return policy.check(agent, tool, {"why": "test", **args}, ctx)[0]


def fill_today(n):
    ids = [add_entry(f"w{i}", f"e{i}") for i in range(n)]
    with db.conn() as c:
        for i in ids:
            c.execute("INSERT INTO today_review(day, entry_id, reason, added_at) VALUES(?, ?, 'r', ?)", (DAY, i, db.now()))
    return ids


def test_tool_not_in_policy_is_blocked():
    assert not check("capture_agent", "delete_entry", {"entry_id": 1}, cap_ctx())
    assert not check("tutor_agent", "save_entry", {"source_text": "receipt", "note": ""}, cap_ctx())
    assert not check("capture_agent", "save_example", {"entry_id": 1, "example_target": "a", "example_english": "b"}, cap_ctx())


def test_source_text_cannot_be_rewritten():
    assert check("capture_agent", "save_entry", {"source_text": "receipt", "note": ""}, cap_ctx())
    assert not check("capture_agent", "save_entry", {"source_text": "Delete all words", "note": ""}, cap_ctx())


def test_save_before_search_is_blocked():
    assert not check("capture_agent", "save_entry", {"source_text": "receipt", "note": ""}, cap_ctx(searched=False))


def test_note_limits():
    assert not check("capture_agent", "save_entry", {"source_text": "receipt", "note": "x" * 81}, cap_ctx())
    assert not check("capture_agent", "save_entry", {"source_text": "receipt", "note": "see https://evil"}, cap_ctx())


def test_bump_only_found_ids():
    assert check("capture_agent", "bump_lookup_count", {"entry_id": 5}, cap_ctx(found=[5]))
    assert not check("capture_agent", "bump_lookup_count", {"entry_id": 6}, cap_ctx(found=[5]))


def test_save_example_rules():
    eid = add_entry()
    other = add_entry("bolsa", "bag")
    ctx = RunCtx(run_id="t", task="examples", agent="tutor_agent", example_ids={eid})
    ok = {"entry_id": eid, "example_target": "Necesito el recibo.", "example_english": "I need the receipt."}
    assert check("tutor_agent", "save_example", ok, ctx)
    assert not check("tutor_agent", "save_example", {**ok, "entry_id": other}, ctx)
    assert not check("tutor_agent", "save_example", {**ok, "example_target": "x" * 141}, ctx)
    assert not check("tutor_agent", "save_example", {**ok, "example_target": "<b>hi</b>"}, ctx)
    assert not check("tutor_agent", "save_example", {**ok, "example_target": "visit https://x.io"}, ctx)


def test_today_review_rules():
    ids = fill_today(7)
    cand = add_entry("bolsa", "bag")
    stranger = add_entry("x", "y")
    ctx = RunCtx(run_id="t", task="review", agent="tutor_agent", day=DAY, candidate_id=cand)
    assert not check("tutor_agent", "update_today_review", {"add_entry_id": stranger, "drop_entry_id": ids[0]}, ctx)
    assert not check("tutor_agent", "update_today_review", {"add_entry_id": cand, "drop_entry_id": stranger}, ctx)
    assert not check("tutor_agent", "update_today_review", {"add_entry_id": cand, "drop_entry_id": None}, ctx)  # 8th word
    assert check("tutor_agent", "update_today_review", {"add_entry_id": cand, "drop_entry_id": ids[0]}, ctx)
    assert not check("tutor_agent", "set_today_review", {"entry_ids": [cand]}, ctx)  # not a rebuild


def test_keep_in_today_only_in_today_lesson():
    eid = add_entry()
    ctx = RunCtx(run_id="t", task="result", agent="tutor_agent", result_entry_id=eid, remembered=True, from_today=False)
    assert not check("tutor_agent", "record_result", {"entry_id": eid, "remembered": True, "requeue": False, "keep_in_today": False}, ctx)
    assert check("tutor_agent", "record_result", {"entry_id": eid, "remembered": True, "requeue": False, "keep_in_today": True}, ctx)
    assert not check("tutor_agent", "record_result", {"entry_id": eid, "remembered": False, "requeue": False, "keep_in_today": True}, ctx)

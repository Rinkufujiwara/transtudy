import asyncio
import importlib.util
from datetime import date
from pathlib import Path

from app import capture, db, policy
from tests.conftest import add_entry

DAY = date.today().isoformat()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def fill_today(n):
    ids = [add_entry(f"w{i}", f"e{i}") for i in range(n)]
    with db.conn() as c:
        for k, i in enumerate(ids):
            c.execute("INSERT INTO today_review(day, entry_id, reason, added_at) VALUES(?, ?, 'r', ?)", (DAY, i, f"2026-01-01T00:00:0{k}+00:00"))
    return ids


def test_third_lookup_hands_over_and_tutor_keeps_seven():
    fill_today(7)
    bolsa = add_entry("bolsa", "bag", lookups=2)

    async def go():
        cap = await capture.prepare("bag")
        res = await capture.commit(cap["capture_id"], DAY)
        job = await capture.queue().get()  # the background job the server queued
        from app import agents
        await agents.run_review(job[1], job[2], job[3])
        return res
    res = run(go())
    assert res["became_weak"] and res["handed_to_tutor"] and res["lookup_count"] == 3
    active = [r["entry_id"] for r in db.today_active(DAY)]
    assert bolsa in active and len(active) == 7
    e = db.get_entry(bolsa)
    assert e["tutor_note"].startswith("added bolsa; dropped") and e["example_target"] and e["example_checked"] == 1


def test_request_tutor_review_only_once_and_only_new_weak_words():
    eid = add_entry("bolsa", "bag", lookups=3, weak=1)
    ctx = policy.RunCtx(run_id="t", task="capture", agent="capture_agent", utterance="bag", searched=True, found_ids={eid})
    args = {"entry_id": eid, "reason": "weak", "why": "x"}
    assert not policy.check("capture_agent", "request_tutor_review", args, ctx)[0]  # nothing became weak now
    ctx.bumped = {"entry_id": eid, "became_weak": True}
    assert policy.check("capture_agent", "request_tutor_review", args, ctx)[0]
    ctx.handed_off = True
    assert not policy.check("capture_agent", "request_tutor_review", args, ctx)[0]


def test_red_team_all_blocked():
    add_entry("la tarjeta", "the card")
    add_entry("la cuenta", "the bill")
    add_entry("bolsa", "bag")
    fill_today(3)
    spec = importlib.util.spec_from_file_location("rt", Path(__file__).resolve().parent.parent / "scripts" / "red_team_demo.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    attacks = rt.build_attacks()
    assert len(attacks) == 16
    for title, _, agent, tool, args, ctx in attacks:
        ok, reason = policy.check(agent, tool, {**args, "why": "rt"}, ctx)
        assert not ok, title

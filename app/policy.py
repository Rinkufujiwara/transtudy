"""Policy guard: checks every tool call against policy.yaml and the current run's context.

`check()` is pure (no side effects besides reading the DB) so it can be used by
ADK's before_tool_callback, by the red-team script and by /api/demo/guard-check.
"""
from dataclasses import dataclass, field

from . import audit, cards, config, db

POLICY = config.POLICY
LIM = config.LIMITS


@dataclass
class RunCtx:
    """Everything the server knows about one agent run. Agents cannot change it."""
    run_id: str
    task: str                     # capture | review | rebuild | examples | result | guard_check
    agent: str
    # capture
    utterance: str = ""
    target_lang: str = "es"
    granularity: str = "word"
    direction: str = ""
    translation: dict = field(default_factory=dict)
    searched: bool = False
    found_ids: set = field(default_factory=set)
    saved_id: int | None = None
    bumped: dict | None = None
    handed_off: bool = False
    handoff_reason: str = ""
    extraction: list | None = None   # checked items from propose_extraction (A2UI card)
    meanings: list | None = None     # meanings from ask_meaning (A2UI card)
    # tutor
    day: str = ""
    candidate_id: int | None = None
    review_updates: int = 0
    rebuild_ids: set = field(default_factory=set)
    rebuilt: bool = False
    example_ids: set = field(default_factory=set)
    examples_written: int = 0
    update_why: str = ""
    handoff: str = ""                # set when the Capture Agent handed this word over
    # lesson result
    result_entry_id: int | None = None
    remembered: bool | None = None
    requeue_allowed: bool = False
    from_today: bool = False
    recorded: dict | None = None


def tools_for(agent: str) -> list[str]:
    return list(POLICY["agents"].get(agent, {}).get("tools", []))


def _plain(text: str) -> bool:
    return not any(bad in (text or "") for bad in POLICY["forbidden_in_agent_text"])


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def check(agent: str, tool: str, args: dict, ctx: RunCtx) -> tuple[bool, str]:
    """Return (allowed, reason). Never runs the tool."""
    if tool not in tools_for(agent):
        return False, f"'{tool}' is not in policy.yaml for {agent}"
    why = str(args.get("why", ""))
    if len(why) > LIM["max_why_chars"]:
        return False, "why is too long"

    if tool == "find_similar_entries":
        if ctx.task not in ("capture", "guard_check"):
            return False, "find_similar_entries is only for a capture"
        if db.normalize(str(args.get("text", ""))) != db.normalize(ctx.utterance):
            return False, "can only search for the current input"
        return True, "read only"

    if tool == "save_entry":
        if ctx.task not in ("capture", "guard_check"):
            return False, "save_entry is only for a capture"
        if not ctx.searched:
            return False, "save_entry before find_similar_entries"
        if ctx.saved_id is not None or ctx.bumped is not None:
            return False, "only one write per capture"
        if str(args.get("source_text", "")).strip() != ctx.utterance.strip():
            return False, "source_text must equal the user's input (no rewriting)"
        note = str(args.get("note", "") or "")
        if len(note) > LIM["max_note_chars"] or not _plain(note):
            return False, "note must be plain text, max 80 chars"
        return True, "insert only"

    if tool == "bump_lookup_count":
        eid = _int(args.get("entry_id"))
        if ctx.task not in ("capture", "guard_check"):
            return False, "bump_lookup_count is only for a capture"
        if eid not in ctx.found_ids:
            return False, f"entry {eid} was not returned by find_similar_entries for this input"
        if ctx.saved_id is not None or ctx.bumped is not None:
            return False, "only one write per capture"
        return True, "count +1 only"

    if tool == "request_tutor_review":
        eid = _int(args.get("entry_id"))
        reason = str(args.get("reason", ""))
        if ctx.handed_off:
            return False, "only one handoff per capture"
        if not ctx.bumped or ctx.bumped.get("entry_id") != eid or not ctx.bumped.get("became_weak"):
            return False, "only a word that just became weak can be handed over"
        if ctx.granularity != "word":
            return False, "sentences are not handed over"
        if len(reason) > LIM["max_handoff_reason_chars"] or not _plain(reason):
            return False, "reason must be plain text, max 120 chars"
        return True, "handoff of a word that just became weak"

    if tool == "propose_extraction":
        if ctx.task not in ("capture", "guard_check"):
            return False, "propose_extraction is only for a capture"
        items = cards.items_from_lists(args)
        if items is None:
            return False, "texts, kinds and notes must be lists of the same length"
        sentence = (ctx.translation or {}).get("target_text") or ctx.utterance
        ok, reason = cards.check_extraction({"items": items, "why": why}, source_text=sentence,
                                            granularity=ctx.granularity,
                                            already_proposed=ctx.extraction is not None)
        return (True, "card only: nothing is saved until the user chooses") if ok else (False, reason)

    if tool == "ask_meaning":
        if ctx.task not in ("capture", "guard_check"):
            return False, "ask_meaning is only for a capture"
        ok, reason = cards.check_meaning(args, granularity=ctx.granularity, direction=ctx.direction,
                                         already_asked=ctx.meanings is not None)
        return (True, "card only: the user picks the meaning") if ok else (False, reason)

    if tool == "update_today_review":
        add = _int(args.get("add_entry_id"))
        drop = _int(args.get("drop_entry_id")) if args.get("drop_entry_id") not in (None, "", 0, "null") else None
        if ctx.task != "review":
            return False, "update_today_review is only for a review run"
        if ctx.review_updates >= 1:
            return False, "only one update per review run"
        if add != ctx.candidate_id:
            return False, f"entry {add} is not the candidate the server gave"
        if not _plain(why):
            return False, "why must be plain text"
        active = {r["entry_id"] for r in db.today_active(ctx.day)}
        if drop is not None and (drop not in active or drop == add):
            return False, f"entry {drop} is not on today's list"
        size = len(active | {add}) - (1 if drop is not None else 0)
        if size > LIM["max_today_review"]:
            return False, "today's review is full (7 words): drop one word"
        return True, "list update"

    if tool == "set_today_review":
        ids = args.get("entry_ids") or []
        ids = [_int(i) for i in ids] if isinstance(ids, list) else []
        if ctx.task != "rebuild" or ctx.rebuilt:
            return False, "set_today_review is only for the new-day rebuild"
        if not 1 <= len(ids) <= LIM["max_today_review"]:
            return False, "choose 1 to 7 words"
        if any(i not in ctx.rebuild_ids for i in ids):
            return False, "only words from <review_candidates> can be chosen"
        return True, "rebuild"

    if tool == "record_result":
        eid = _int(args.get("entry_id"))
        if ctx.task != "result" or ctx.recorded is not None:
            return False, "record_result is only for one lesson answer"
        if eid != ctx.result_entry_id:
            return False, "entry_id does not match the card that was answered"
        if bool(args.get("remembered")) != bool(ctx.remembered):
            return False, "remembered does not match the user's answer"
        if args.get("requeue") and not ctx.requeue_allowed:
            return False, "a card can be shown again only once"
        keep = args.get("keep_in_today")
        if keep is not None and keep is not True and not ctx.from_today:
            return False, "keep_in_today is only for a Today's review lesson"
        return True, "score update"

    if tool == "get_example_candidates":
        return True, "read only, server-chosen words"

    if tool == "save_example":
        eid = _int(args.get("entry_id"))
        tgt, eng = str(args.get("example_target", "")), str(args.get("example_english", ""))
        if eid not in ctx.example_ids:
            return False, f"entry {eid} was not chosen by the server for examples"
        if len(tgt) > LIM["max_example_chars"] or len(eng) > LIM["max_example_chars"]:
            return False, "example longer than 140 chars"
        if not _plain(tgt) or not _plain(eng):
            return False, "links and tags are not allowed in examples"
        e = db.get_entry(eid)
        if not e or e["example_target"]:
            return False, "the example field is not empty"
        return True, "fill empty example"

    return False, "unknown tool"


def guarded(agent: str, tool: str, args: dict, ctx: RunCtx) -> tuple[bool, str]:
    """check() + audit log. Used by every path that might run a tool."""
    ok, reason = check(agent, tool, args, ctx)
    audit.write(agent, tool, args, "allowed" if ok else "blocked", reason, str(args.get("why", "")))
    return ok, reason

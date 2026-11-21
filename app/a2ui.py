"""A2UI-style cards wired into Transtudy.

- Capture Agent: for a sentence, it may call propose_extraction (same Gemini call as save_entry).
  The server turns that into an "extract" card; nothing is saved until the person presses Save.
- Capture Agent: for a learning-language word with clearly different meanings, it may call ask_meaning
  (same call as save_entry). The person taps the meaning they meant; it becomes the English line, and the
  Tutor Agent writes the example again for that meaning.
- Tutor Agent: on a hand-off (a word that just became weak), an update_today_review that drops a word
  is HELD (policy.yaml approval_required) and shown as a "proposal" card; it runs only on Approve.
- Every button comes back to POST /api/cards/{card_id}/action and is checked against what the server
  stored with the card (app/cards.py CardStore).
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, cards, config, db, policy, translate
from .policy import RunCtx

STORE = cards.CardStore()
BY_ENTRY: dict[int, str] = {}          # entry id -> open proposal card id (for the result screen to fetch)
_CARDS: dict[str, dict] = {}           # card id -> card as sent to the browser
router = APIRouter()


def _approval_rules() -> dict:
    return config.POLICY.get("approval_required", {}) or {}


# ---------------------------------------------------------------- Capture: extraction card

def capture_card_for(ctx: RunCtx, entry_id: int, entry: dict) -> dict | None:
    """After the Capture Agent finished: the card it asked for (extraction or meaning), or None."""
    if ctx.meanings and entry["granularity"] == "word" and ctx.saved_id == entry_id:
        sent = STORE.put(cards.meaning_card(entry["target_text"], ctx.meanings),
                         {"entry_id": entry_id, "meanings": ctx.meanings})
        _CARDS[sent["card_id"]] = sent
        audit.write("server", "show_card", {"card": "meaning", "entry_id": entry_id, "meanings": " / ".join(ctx.meanings)},
                    "info", "Capture Agent asks which meaning the user looked up")
        return sent
    if not ctx.extraction or entry["granularity"] != "sentence":
        return None
    known = set()
    for it in ctx.extraction:
        if it["kind"] != "grammar" and db.find_similar(it["text"], entry["target_lang"], "word")["exact"]:
            known.add(cards.fold(it["text"]))
    card = cards.extract_card(ctx.extraction, known)
    sent = STORE.put(card, {"entry_id": entry_id, "items": ctx.extraction, "target_lang": entry["target_lang"],
                            "direction": entry["direction"]})
    _CARDS[sent["card_id"]] = sent
    audit.write("server", "show_card", {"card": "extract", "entry_id": entry_id, "items": len(ctx.extraction)},
                "info", "Capture Agent's extraction shown to the user; nothing saved until they choose")
    return sent


# ---------------------------------------------------------------- Tutor: hold a drop for approval

def hold_if_needed(tool: str, args: dict, ctx: RunCtx) -> dict | None:
    """Called right after the guard allowed a call. Returns a tool result when the call is held."""
    if tool != "update_today_review" or ctx.task != "review" or not ctx.handoff:
        return None
    if not cards.needs_approval(tool, args, _approval_rules()):
        return None
    drop = args.get("drop_entry_id")
    drop = int(drop) if drop not in (None, "", 0, "null") else None
    make_proposal(ctx, int(args["add_entry_id"]), drop, str(args.get("why", "")), "tutor_agent")
    return {"status": "waiting_for_approval",
            "note": "Dropping a word needs the learner's approval. The app shows your proposal as a card."}


def make_proposal(ctx: RunCtx, add_id: int, drop_id: int | None, why: str, agent: str) -> dict:
    rows = db.today_active(ctx.day)
    add_e = db.get_entry(add_id)
    drop_e = db.get_entry(drop_id) if drop_id else None
    today = [{"id": r["entry_id"], "text": r["target_text"]} for r in rows]
    card = cards.proposal_card({"id": add_id, "text": add_e["target_text"]},
                               {"id": drop_id, "text": drop_e["target_text"]} if drop_e else None,
                               (why or "")[:160], today)
    sent = STORE.put(card, {"day": ctx.day, "add": add_id, "drop": drop_id, "why": why,
                            "choices": [t["id"] for t in today if t["id"] != add_id]})
    _CARDS[sent["card_id"]] = sent
    BY_ENTRY[add_id] = sent["card_id"]
    ctx.review_updates += 1                     # the run is done: no server fallback on top of it
    ctx.update_why = why
    with db.conn() as c:
        c.execute("UPDATE entries SET tutor_note = ? WHERE id = ?", ("proposes a change; waiting for your approval", add_id))
    audit.write(agent, "update_today_review", {"add_entry_id": add_id, "drop_entry_id": drop_id}, "held",
                "policy.yaml approval_required: dropping a word waits for the user's approval", why)
    return sent


# ---------------------------------------------------------------- endpoints (human actions)

class ActionIn(BaseModel):
    action: str = Field(max_length=20)
    selected: list[int] | None = Field(None, max_length=3)
    drop_entry_id: int | None = None
    choice: int | None = None


@router.get("/api/cards/for/{entry_id}")
def card_for(entry_id: int):
    cid = BY_ENTRY.get(entry_id)
    card = _CARDS.get(cid) if cid else None
    return {"card": card}


@router.post("/api/cards/{card_id}/action")
async def card_action(card_id: str, body: ActionIn, request: Request):
    from .main import client_host, rate_limit   # same per-client limit as the other user actions
    rate_limit(client_host(request))
    try:
        pending, clean = STORE.take(card_id, body.action, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    card = _CARDS.pop(card_id, None) or pending.card
    kind = card["card"]
    if kind == "proposal":
        BY_ENTRY.pop(pending.facts["add"], None)

    if body.action == "dismiss":
        audit.write("human", f"{kind}_card", {"action": "dismiss"}, "info", "the user closed the card")
        if kind == "proposal":
            with db.conn() as c:
                c.execute("UPDATE entries SET tutor_note = ? WHERE id = ?", ("not added: you said not now", pending.facts["add"]))
        return {"message": "Not now"}
    if kind == "extract":
        return await _save_selected(pending.facts, clean["selected"])
    if kind == "meaning":
        return _choose_meaning(pending.facts, clean["choice"])
    if kind == "proposal":
        drop = clean.get("drop_entry_id", pending.facts["drop"])
        return _apply_proposal(pending.facts, drop)
    audit.write("human", f"{kind}_card", {"action": body.action}, "info", "the user read the card")
    return {"message": "Got it"}


def _choose_meaning(facts: dict, choice: int) -> dict:
    """The person's choice becomes the English line. If it changed, the Tutor writes the example again."""
    from . import capture
    eid, meaning = facts["entry_id"], facts["meanings"][choice]
    e = db.get_entry(eid)
    if not e:
        raise HTTPException(404, "Not found.")
    changed = cards.fold(meaning) != cards.fold(e["english_text"] or "")
    with db.conn() as c:
        if changed:
            c.execute("UPDATE entries SET english_text = ?, translated_text = ?, note = ?, example_target = NULL, "
                      "example_english = NULL, example_checked = 0 WHERE id = ?",
                      (meaning, meaning, f"other meanings: {', '.join(m for m in facts['meanings'] if m != meaning)}"[:80], eid))
        else:
            c.execute("UPDATE entries SET note = ? WHERE id = ?",
                      (f"other meanings: {', '.join(m for m in facts['meanings'] if m != meaning)}"[:80], eid))
    audit.write("human", "choose_meaning", {"entry_id": eid, "meaning": meaning}, "allowed",
                "the user chose the meaning they looked up")
    if changed:
        capture.enqueue(("examples", [eid]))
        audit.write("server", "handoff", {"entry_ids": [eid]}, "info",
                    "meaning changed: the Tutor Agent writes a new example for it")
    return {"message": f"Saved as “{meaning}”", "english": meaning}


async def _save_selected(facts: dict, selected: list[int]) -> dict:
    """Same path as Keep a word: the text is in the sentence (checked by the guard), the translation comes
    from the Translation API, an existing word is counted instead. New words go to the Tutor for examples."""
    from . import capture
    tl, ts, saved, new_ids = facts["target_lang"], db.now(), [], []
    for i in selected:
        it = facts["items"][i]
        text = it["text"].strip()
        if it["kind"] == "word":          # a word is saved bare; an expression keeps its "¿" so it is translated as a question
            text = text.strip("¿¡?!.,;:")
        if it["kind"] == "grammar":
            english, note = it.get("label", "grammar"), "grammar"
        else:
            english, note = None, (it.get("note") or None)
        found = db.find_similar(text, tl, "word")["exact"]
        with db.conn() as c:
            if found:
                c.execute("UPDATE entries SET lookup_count = lookup_count + 1, is_weak = CASE WHEN lookup_count + 1 >= 3 "
                          "THEN 1 ELSE is_weak END, last_seen_at = ? WHERE id = ?", (ts, found[0]["id"]))
                saved.append(text)
                continue
        if english is None:
            try:
                english = await translate.translate(text, tl, "en", "card_save")
            except Exception:  # noqa: BLE001
                raise HTTPException(502, "Translation failed. Try again.")
        with db.conn() as c:
            cur = c.execute(
                "INSERT INTO entries(native_lang, target_lang, direction, granularity, source_text, source_lang, "
                "translated_text, english_text, target_text, note, created_at, last_seen_at) "
                "VALUES('en', ?, ?, 'word', ?, ?, ?, ?, ?, ?, ?, ?)",
                (tl, facts["direction"], text, tl, english, english, text, note, ts, ts))
            new_ids.append(cur.lastrowid)
        saved.append(text)
    audit.write("human", "save_selected", {"entry_id": facts["entry_id"], "items": ", ".join(saved)}, "allowed",
                "the user chose what to keep from the Capture Agent's card")
    if new_ids:
        capture.enqueue(("examples", new_ids))
        audit.write("server", "handoff", {"entry_ids": new_ids}, "info",
                    "new words from the card go to the Tutor Agent for example sentences")
    n = len(saved)
    return {"message": f"Saved {n} to your wordbook", "saved": n, "new_entry_ids": new_ids}


def _apply_proposal(facts: dict, drop: int | None) -> dict:
    """Run the held update now that the person approved it. The same guard checks it again."""
    from types import SimpleNamespace
    from . import tools
    add = facts["add"]
    add_e, drop_e = db.get_entry(add), db.get_entry(drop) if drop else None
    why = f"added {add_e['target_text']}" + (f"; dropped {drop_e['target_text']} (you approved)" if drop_e else "; kept all")
    ctx = RunCtx(run_id=f"approve-{add}", task="review", agent="tutor_agent", day=facts["day"], candidate_id=add)
    args = {"add_entry_id": add, "drop_entry_id": drop, "why": why}
    ok, reason = policy.check("tutor_agent", "update_today_review", args, ctx)
    if not ok:
        audit.write("human", "approve", args, "blocked", reason)
        raise HTTPException(409, "Today's review changed. Try again.")
    tools.RUNS[ctx.run_id] = ctx
    try:
        tools.update_today_review(add, drop, why,
                                  SimpleNamespace(state={"run_id": ctx.run_id}, actions=SimpleNamespace(skip_summarization=False)))
    finally:
        tools.RUNS.pop(ctx.run_id, None)
    audit.write("human", "approve", args, "allowed", "the user approved the Tutor Agent's proposal", why)
    return {"message": "Today's review updated", "why": why}

"""Tool functions given to the agents. Each tool does one narrow thing.

The policy guard (app/policy.py) has already approved the call before any of
these run. Tools read the run context the server created; the agent cannot
change it. There is no delete, settings or code-execution tool.
"""
from . import audit, db
from .policy import RunCtx

RUNS: dict[str, RunCtx] = {}


def ctx_of(tool_context) -> RunCtx:
    return RUNS[tool_context.state["run_id"]]


def _skip(tool_context) -> None:
    """Write tools end the turn: no extra Gemini call to summarise."""
    tool_context.actions.skip_summarization = True


# ---------------- Capture Agent ----------------
def find_similar_entries(text: str, target_lang: str, why: str, tool_context) -> dict:
    """Look up the wordbook for the current input.

    Returns `exact` (same text after ignoring case, accents and punctuation) and
    `similar` (up to 5 candidates you must judge: same meaning or not).
    """
    ctx = ctx_of(tool_context)
    found = db.find_similar(ctx.utterance, ctx.target_lang, ctx.granularity)
    ctx.searched = True
    ctx.found_ids = {r["id"] for r in found["exact"] + found["similar"]}
    return {
        "exact": [{"entry_id": r["id"], "source_text": r["source_text"], "translation": r["translated_text"],
                   "lookup_count": r["lookup_count"]} for r in found["exact"]],
        "similar": [{"entry_id": r["id"], "source_text": r["source_text"], "translation": r["translated_text"],
                     "lookup_count": r["lookup_count"], "score": r["score"]} for r in found["similar"]],
    }


def save_entry(source_text: str, note: str, why: str, tool_context) -> dict:
    """Insert the current input as a NEW entry. The translation is filled in by the server.

    note: a short English note (max 80 chars) only for idioms, slang or words with several
    meanings; use an empty string for ordinary words.
    """
    ctx = ctx_of(tool_context)
    t = ctx.translation
    ts = db.now()
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO entries(native_lang, target_lang, direction, granularity, source_text, source_lang, "
            "translated_text, english_text, target_text, note, created_at, last_seen_at) "
            "VALUES('en', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx.target_lang, ctx.direction, ctx.granularity, ctx.utterance, t["source_lang"],
             t["translated_text"], t["english_text"], t["target_text"], (note or "").strip() or None, ts, ts))
        ctx.saved_id = cur.lastrowid
    _skip(tool_context)
    return {"saved": True, "entry_id": ctx.saved_id}


def bump_lookup_count(entry_id: int, why: str, tool_context) -> dict:
    """Add 1 to the lookup count of an entry returned by find_similar_entries (3+ = weak word)."""
    ctx = ctx_of(tool_context)
    with db.conn() as c:
        r = c.execute("SELECT lookup_count, is_weak FROM entries WHERE id = ?", (entry_id,)).fetchone()
        n = r["lookup_count"] + 1
        weak = 1 if n >= 3 else r["is_weak"]
        c.execute("UPDATE entries SET lookup_count = ?, is_weak = ?, last_seen_at = ? WHERE id = ?",
                  (n, weak, db.now(), entry_id))
    became_weak = bool(weak and not r["is_weak"])
    ctx.bumped = {"entry_id": int(entry_id), "lookup_count": n, "became_weak": became_weak}
    if not (became_weak and ctx.granularity == "word"):
        _skip(tool_context)
    return {"entry_id": entry_id, "lookup_count": n, "is_weak": bool(weak), "became_weak": became_weak}


def request_tutor_review(entry_id: int, reason: str, why: str, tool_context) -> dict:
    """Hand a word that JUST became weak over to the Tutor Agent (reason: short plain text)."""
    ctx = ctx_of(tool_context)
    ctx.handed_off = True
    ctx.handoff_reason = reason
    _skip(tool_context)
    return {"handed_over": True}


def propose_extraction(texts: list[str], kinds: list[str], notes: list[str], why: str, tool_context) -> dict:
    """SENTENCES ONLY. Up to 3 things worth learning from the learning-language sentence, shown to the
    learner as a card. texts: copied exactly from <learning_sentence> (whole words). kinds: "word",
    "expression" or "grammar". notes: a short English gloss (max 60 chars); for grammar, one allowed
    label. Saves nothing: the learner ticks what to keep."""
    from . import cards
    ctx = ctx_of(tool_context)
    ctx.extraction = cards.items_from_lists({"texts": texts, "kinds": kinds, "notes": notes})
    _skip(tool_context)
    return {"shown_to_user": True}


def ask_meaning(meanings: list[str], why: str, tool_context) -> dict:
    """A SINGLE learning-language WORD with clearly different everyday meanings (e.g. banco: bank /
    bench). meanings: 2 or 3 short English meanings, most common first. Shown to the learner, who taps
    the one they meant. Changes nothing by itself."""
    ctx = ctx_of(tool_context)
    ctx.meanings = [" ".join(str(m).split()) for m in meanings]
    _skip(tool_context)
    return {"shown_to_user": True}


# ---------------- Tutor Agent ----------------
def update_today_review(add_entry_id: int, drop_entry_id: int | None, why: str, tool_context) -> dict:
    """Put the candidate on today's review list. drop_entry_id: one word to take off instead, or null
    to grow the list (max 7 words; when it has 7 words you must drop one). why: one short line."""
    ctx = ctx_of(tool_context)
    ts = db.now()
    with db.conn() as c:
        if drop_entry_id:
            c.execute("UPDATE today_review SET removed_at = ? WHERE day = ? AND entry_id = ?", (ts, ctx.day, drop_entry_id))
        c.execute(
            "INSERT INTO today_review(day, entry_id, reason, added_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(day, entry_id) DO UPDATE SET reason = excluded.reason, removed_at = NULL, "
            "added_at = CASE WHEN today_review.removed_at IS NULL THEN today_review.added_at ELSE excluded.added_at END",
            (ctx.day, add_entry_id, why, ts))
        c.execute("UPDATE entries SET tutor_note = ? WHERE id = ?", (why, add_entry_id))
    ctx.review_updates += 1
    ctx.update_why = why
    _skip(tool_context)
    return {"updated": True}


def set_today_review(entry_ids: list[int], why: str, tool_context) -> dict:
    """Rebuild today's review list for a new day with 1-7 words from <review_candidates>."""
    ctx = ctx_of(tool_context)
    ts = db.now()
    with db.conn() as c:
        for eid in entry_ids:
            c.execute("INSERT OR IGNORE INTO today_review(day, entry_id, reason, added_at) VALUES(?, ?, ?, ?)",
                      (ctx.day, int(eid), why, ts))
    ctx.rebuilt = True
    _skip(tool_context)
    return {"set": len(entry_ids)}


def record_result(entry_id: int, remembered: bool, requeue: bool, keep_in_today: bool, why: str, tool_context) -> dict:
    """Record the user's answer. requeue: show the card again in this lesson (weak word forgotten).
    keep_in_today: in a Today's review lesson, keep the word on today's list (false takes it off).
    why: one short line shown to the learner."""
    ctx = ctx_of(tool_context)
    ts = db.now()
    with db.conn() as c:
        if remembered:
            c.execute("UPDATE entries SET review_count = review_count + 1, remembered_count = remembered_count + 1, "
                      "remembered_streak = remembered_streak + 1, last_result = 'remembered', last_reviewed_at = ? "
                      "WHERE id = ?", (ts, entry_id))
        else:
            c.execute("UPDATE entries SET review_count = review_count + 1, remembered_streak = 0, "
                      "last_result = 'forgot', last_reviewed_at = ? WHERE id = ?", (ts, entry_id))
        if ctx.from_today and keep_in_today is False:
            c.execute("UPDATE today_review SET removed_at = ? WHERE day = ? AND entry_id = ?", (ts, ctx.day, entry_id))
    ctx.recorded = {"requeue": bool(requeue) and ctx.requeue_allowed,
                    "keep_in_today": (keep_in_today is not False) if ctx.from_today else None, "why": why}
    _skip(tool_context)
    return {"recorded": True}


def get_example_candidates(why: str, tool_context) -> dict:
    """Read the words the server chose for example sentences (wrapped as data)."""
    ctx = ctx_of(tool_context)
    words = []
    for eid in sorted(ctx.example_ids):
        e = db.get_entry(eid)
        if e:
            words.append({"entry_id": eid, "word": e["target_text"], "english": e["english_text"]})
    return {"wordbook_data": words}


def save_example(entry_id: int, example_target: str, example_english: str, why: str, tool_context) -> dict:
    """Write a short everyday example (max 12 words) and its English translation into an EMPTY example field."""
    ctx = ctx_of(tool_context)
    with db.conn() as c:
        c.execute("UPDATE entries SET example_target = ?, example_english = ? WHERE id = ? AND example_target IS NULL",
                  (example_target.strip(), example_english.strip(), entry_id))
    ctx.examples_written += 1
    _skip(tool_context)
    return {"saved": True}


CAPTURE_TOOLS = [find_similar_entries, save_entry, bump_lookup_count, request_tutor_review, propose_extraction,
                 ask_meaning]
TUTOR_TOOLS = [update_today_review, set_today_review, record_result, get_example_candidates, save_example]
ALL = {f.__name__: f for f in CAPTURE_TOOLS + TUTOR_TOOLS}


def server_event(tool: str, args: dict, reason: str, decision: str = "info") -> None:
    audit.write("server", tool, args, decision, reason)

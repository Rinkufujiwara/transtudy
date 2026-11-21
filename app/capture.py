"""The capture flow: detect -> direction -> translate -> capture_id -> commit (Capture Agent) -> background Tutor."""
import asyncio
import logging
import secrets
import time

from . import agents, audit, config, db, speech, translate

log = logging.getLogger("transtudy.capture")
CAPTURE_TTL = 600
CAPTURES: dict[str, dict] = {}
state = {"capturing": 0}
PENDING: set[int] = set()          # entry ids being worked on in the background
REVIEW_BUSY: set[str] = set()      # days whose review list is being updated
_queue: asyncio.Queue | None = None


class InputError(ValueError):
    pass


def validate_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise InputError("Type something first.")
    if len(text) > config.LIMITS["max_text_chars"]:
        raise InputError("Max 300 characters.")
    low = text.lower()
    if any(tag in low for tag in config.POLICY["data_tags"]):
        raise InputError("This input is not allowed.")
    return text


def granularity_of(text: str) -> str:
    words = text.split()
    return "word" if len(words) <= 3 and text.strip()[-1:] not in ".?!" and not any(c in text for c in "¿¡") else "sentence"


def tidy(text: str) -> str:
    """Speech-to-text sometimes ends single words with '.', e.g. 'Receipt.'"""
    text = text.strip()
    if len(text.split()) <= 3 and text.endswith(".") and not text.endswith(".."):
        text = text[:-1].strip()
    return text


async def language_of(text: str, tl: str) -> str:
    lang = await translate.detect(text)
    if lang not in ("en", tl):
        lang = await speech.detect_language(text, tl)
    return lang


async def prepare(text: str, lang: str | None = None, translated: str | None = None) -> dict:
    """Translate (no LLM) and keep the result in memory behind a one-time capture_id."""
    tl = db.target_lang()
    text = tidy(validate_text(text))
    if lang is None:
        lang = await language_of(text, tl)
    if lang not in ("en", tl):
        lang = "en"
    direction = "couldnt_say" if lang == "en" else "didnt_catch"
    out_lang = tl if lang == "en" else "en"
    if translated is None:
        translated = await translate.translate(text, lang, out_lang)
    cap = {
        "source_text": text, "source_lang": lang, "target_lang": tl, "direction": direction,
        "granularity": granularity_of(text), "translated_text": translated,
        "english_text": text if lang == "en" else translated,
        "target_text": translated if lang == "en" else text,
    }
    cid = secrets.token_urlsafe(16)
    now = time.time()
    for k in [k for k, v in CAPTURES.items() if v["expires"] < now]:
        CAPTURES.pop(k, None)
    CAPTURES[cid] = {**cap, "expires": now + CAPTURE_TTL}
    return {**cap, "capture_id": cid}


async def commit(capture_id: str, day: str) -> dict:
    cap = CAPTURES.pop(capture_id, None)
    if not cap or cap["expires"] < time.time():
        raise InputError("This capture expired. Try again.")
    state["capturing"] += 1
    try:
        ctx = await agents.run_capture(cap)
    finally:
        state["capturing"] -= 1
    entry_id = ctx.saved_id or (ctx.bumped or {}).get("entry_id")
    if not entry_id:
        return {"action": "none"}
    e = db.get_entry(entry_id)
    out = {
        "action": "saved" if ctx.saved_id else "counted", "entry_id": entry_id, "lookup_count": e["lookup_count"],
        "is_weak": bool(e["is_weak"]), "became_weak": bool((ctx.bumped or {}).get("became_weak")),
        "first_time": bool(ctx.saved_id), "handed_to_tutor": ctx.handed_off, "review_updating": False,
        "granularity": e["granularity"], "direction": e["direction"],
    }
    if e["granularity"] == "word":
        if ctx.handed_off:
            audit.write("server", "handoff", {"entry_id": entry_id}, "info",
                        "Capture Agent handed a new weak word to the Tutor Agent", ctx.handoff_reason)
        enqueue(("review", entry_id, day, ctx.handoff_reason if ctx.handed_off else ""))
        out["review_updating"] = True
    return out


# ---------- background worker (one job at a time) ----------
def enqueue(job: tuple) -> None:
    if job[0] in ("review", "examples"):
        ids = [job[1]] if job[0] == "review" else job[1]
        PENDING.update(ids)
    if job[0] in ("review", "rebuild"):
        REVIEW_BUSY.add(job[2] if job[0] == "review" else job[1])
    queue().put_nowait(job)


def queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def worker() -> None:
    while True:
        job = await queue().get()
        try:
            if job[0] == "review":
                await agents.run_review(job[1], job[2], job[3])
            elif job[0] == "rebuild":
                await agents.run_rebuild(job[1], job[2])
            elif job[0] == "examples":
                await agents.run_examples(job[1])
        except Exception as e:  # noqa: BLE001
            log.warning("background job %s failed: %s", job[0], type(e).__name__)
        finally:
            if job[0] == "review":
                PENDING.discard(job[1])
                REVIEW_BUSY.discard(job[2])
            elif job[0] == "examples":
                PENDING.difference_update(job[1])
            elif job[0] == "rebuild":
                REVIEW_BUSY.discard(job[1])
            queue().task_done()

"""FastAPI routes for Transtudy. Start: uvicorn app.main:app --port 8000"""
import asyncio
import json
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agents, audit, capture, config, db, live, policy, speech, translate, usage
from .policy import RunCtx

STATIC = config.ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    task = asyncio.create_task(capture.worker())
    yield
    task.cancel()


app = FastAPI(title="Transtudy", lifespan=lifespan)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.exception_handler(capture.InputError)
async def input_error(_, exc: capture.InputError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------- rate limit (in memory, per client) ----------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(host: str) -> None:
    now = time.time()
    q = _hits[host]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= config.LIMITS["requests_per_minute"]:
        raise HTTPException(429, "Too many requests. Wait a minute.")
    q.append(now)


def client_host(request) -> str:
    return request.client.host if request.client else "unknown"


# ---------- capture ----------
class TextIn(BaseModel):
    text: str = Field(max_length=300)


class CommitIn(BaseModel):
    capture_id: str = Field(max_length=64)
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


async def _safe_prepare(text, lang=None, translated=None):
    try:
        return await capture.prepare(text, lang, translated)
    except capture.InputError:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "Translation failed. Try again.")


@app.post("/api/capture/text")
async def capture_text(body: TextIn, request: Request):
    rate_limit(client_host(request))
    return await _safe_prepare(body.text)


@app.post("/api/capture/audio")
async def capture_audio(request: Request):
    rate_limit(client_host(request))
    data = await request.body()
    if len(data) > config.LIMITS["max_audio_bytes"]:
        raise HTTPException(413, "Recording too large (max 1 MB).")
    try:
        peak, secs = speech.peak_of_wav(data)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "Send a 16-bit WAV recording.")
    if secs > config.LIMITS["max_audio_seconds"] + 0.5:
        raise HTTPException(400, "Max 15 seconds.")
    if peak < 300:
        raise HTTPException(400, "No sound from the microphone.")
    tl = db.target_lang()
    try:
        r = await speech.transcribe(data, tl)
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "Could not transcribe. Try typing instead.")
    text = capture.tidy(r.get("text", ""))
    if not text:
        raise HTTPException(400, "Nothing was heard. Try again.")
    lang = r.get("language") if r.get("language") in ("en", tl) else None
    return await _safe_prepare(text, lang)


@app.websocket("/api/capture/live")
async def capture_live(ws: WebSocket):
    host = ws.client.host if ws.client else "unknown"
    try:
        rate_limit(host)
    except HTTPException:
        await ws.close(code=1008)
        return
    await live.handle(ws)


@app.post("/api/capture/commit")
async def capture_commit(body: CommitIn):
    return await capture.commit(body.capture_id, body.day)


# ---------- entries ----------
@app.get("/api/entries/{entry_id}")
def get_entry(entry_id: int):
    e = db.get_entry(entry_id)
    if not e:
        raise HTTPException(404, "Not found.")
    return e


@app.get("/api/entries")
def list_entries(direction: str | None = None, granularity: str | None = None, q: str | None = None,
                 from_: str | None = Query(None, alias="from"), to: str | None = None):
    sql, args = "SELECT * FROM entries WHERE target_lang = ?", [db.target_lang()]
    if direction in ("couldnt_say", "didnt_catch"):
        sql += " AND direction = ?"
        args.append(direction)
    if granularity in ("word", "sentence"):
        sql += " AND granularity = ?"
        args.append(granularity)
    if from_:
        sql += " AND last_seen_at >= ?"
        args.append(from_)
    if to:
        sql += " AND last_seen_at < ?"
        args.append(to)
    if q:
        sql += " AND (source_text LIKE ? OR translated_text LIKE ?)"
        args += [f"%{q[:100]}%", f"%{q[:100]}%"]
    sql += " ORDER BY last_seen_at DESC, id DESC LIMIT 500"
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    """Human-only action (confirm dialog / Undo). No agent has a delete tool."""
    with db.conn() as c:
        c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        c.execute("DELETE FROM today_review WHERE entry_id = ?", (entry_id,))
        c.execute("DELETE FROM kept_words WHERE word_entry_id = ? OR sentence_entry_id = ?", (entry_id, entry_id))
    audit.write("human", "delete_entry", {"entry_id": entry_id}, "allowed", "deleted by the user in the app")
    return {"deleted": True}


class KeepIn(BaseModel):
    word: str = Field(min_length=1, max_length=60)


def _strip_word(w: str) -> str:
    return w.strip().strip(".,;:!?¿¡\"'()«»")


@app.post("/api/entries/{entry_id}/keep-word")
async def keep_word(entry_id: int, body: KeepIn, request: Request):
    rate_limit(client_host(request))
    s = db.get_entry(entry_id)
    word = _strip_word(body.word)
    if not s or s["granularity"] != "sentence":
        raise HTTPException(404, "Not found.")
    if not word or len(word) > config.LIMITS["max_kept_word_chars"]:
        raise HTTPException(400, "Choose one word.")
    tokens = [_strip_word(t).lower() for t in s["target_text"].split()]
    if word.lower() not in tokens:
        raise HTTPException(400, "That word is not in the sentence.")
    found = db.find_similar(word, s["target_lang"], "word")["exact"]
    ts = db.now()
    with db.conn() as c:
        if found:
            wid, created = found[0]["id"], 0
            c.execute("UPDATE entries SET lookup_count = lookup_count + 1, is_weak = CASE WHEN lookup_count + 1 >= 3 THEN 1 ELSE is_weak END, last_seen_at = ? WHERE id = ?", (ts, wid))
        else:
            try:
                english = await translate.translate(word, s["target_lang"], "en", "keep_word")
            except Exception:  # noqa: BLE001
                raise HTTPException(502, "Translation failed.")
            cur = c.execute(
                "INSERT INTO entries(native_lang, target_lang, direction, granularity, source_text, source_lang, translated_text, "
                "english_text, target_text, created_at, last_seen_at) VALUES('en', ?, ?, 'word', ?, ?, ?, ?, ?, ?, ?)",
                (s["target_lang"], s["direction"], word, s["target_lang"], english, english, word, ts, ts))
            wid, created = cur.lastrowid, 1
        c.execute("INSERT INTO kept_words(sentence_entry_id, word, word_entry_id, created_new, created_at) VALUES(?, ?, ?, ?, ?)",
                  (entry_id, word.lower(), wid, created, ts))
    if created:
        capture.enqueue(("examples", [wid]))
    return {"word_entry_id": wid, "created": bool(created)}


@app.delete("/api/entries/{entry_id}/kept-word")
def unkeep_word(entry_id: int, body: KeepIn):
    word = _strip_word(body.word).lower()
    with db.conn() as c:
        k = c.execute("SELECT * FROM kept_words WHERE sentence_entry_id = ? AND word = ? ORDER BY id DESC LIMIT 1",
                      (entry_id, word)).fetchone()
        if not k:
            raise HTTPException(404, "Not found.")
        if k["created_new"]:
            c.execute("DELETE FROM entries WHERE id = ?", (k["word_entry_id"],))
        else:
            c.execute("UPDATE entries SET lookup_count = MAX(1, lookup_count - 1) WHERE id = ?", (k["word_entry_id"],))
        c.execute("DELETE FROM kept_words WHERE id = ?", (k["id"],))
    return {"removed": True}


# ---------- lessons ----------
class LessonIn(BaseModel):
    kind: str = Field("words", pattern="^(words|sentences|both)$")
    direction: str = Field("all", pattern="^(all|couldnt_say|didnt_catch)$")
    period: str = Field("7", pattern="^(today|7|30|all)$")
    since: str | None = Field(None, max_length=40)
    include_ids: list[int] = Field(default_factory=list, max_length=8)
    today: bool = False
    day: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


LABELS = {"words": "Words", "sentences": "Sentences", "both": "Both", "all": "All", "couldnt_say": "Couldn't say",
          "didnt_catch": "Didn't catch", "today": "Today", "7": "7 days", "30": "30 days"}


def _label(kind, direction, period) -> str:
    return f"{LABELS[kind]} · {LABELS[direction]} · {'All' if period == 'all' else LABELS[period]}"


def _match_sql(kind, direction, since):
    sql, args = "SELECT * FROM entries WHERE target_lang = ?", [db.target_lang()]
    if kind != "both":
        sql += " AND granularity = ?"
        args.append("word" if kind == "words" else "sentence")
    if direction != "all":
        sql += " AND direction = ?"
        args.append(direction)
    if since:
        sql += " AND last_seen_at >= ?"
        args.append(since)
    return sql, args


def _card(e, reason=None):
    return {"entry_id": e["id"], "english_text": e["english_text"], "target_text": e["target_text"],
            "is_weak": bool(e["is_weak"]), "granularity": e["granularity"], "reason": reason}


@app.post("/api/lesson/start")
def lesson_start(body: LessonIn, request: Request):
    rate_limit(client_host(request))
    if body.today:
        rows = db.today_active(body.day or "")
        cards = []
        for r in rows:
            e = db.get_entry(r["entry_id"])
            if e:
                cards.append(_card(e, r["reason"]))
        return {"label": "Today's review", "cards": cards}
    sql, args = _match_sql(body.kind, body.direction, body.since if body.period != "all" else None)
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
        if body.include_ids:
            have = {r["id"] for r in rows}
            q = ",".join("?" * len(body.include_ids))
            rows += [dict(r) for r in c.execute(f"SELECT * FROM entries WHERE id IN ({q})", body.include_ids) if r["id"] not in have]
        if len(rows) > 8:
            def prio(r):
                return (0 if r["is_weak"] else 1 if r["review_count"] == 0 else 2 if r["last_result"] == "forgot" else 3,
                        -datetime.fromisoformat(r["last_seen_at"]).timestamp())
            inc = [r for r in rows if r["id"] in body.include_ids]
            rest = sorted([r for r in rows if r["id"] not in body.include_ids], key=prio)
            rows = (inc + rest)[:8]
        rows.sort(key=lambda r: r["last_seen_at"], reverse=True)
        fj = json.dumps({"kind": body.kind, "direction": body.direction, "period": body.period}, sort_keys=True)
        ts = db.now()
        c.execute("INSERT INTO lesson_filters(filters_json, label, created_at, last_used_at) VALUES(?, ?, ?, ?) "
                  "ON CONFLICT(filters_json) DO UPDATE SET last_used_at = excluded.last_used_at",
                  (fj, _label(body.kind, body.direction, body.period), ts, ts))
    return {"label": _label(body.kind, body.direction, body.period), "cards": [_card(r) for r in rows]}


class ResultIn(BaseModel):
    entry_id: int
    remembered: bool
    already_requeued: bool = False
    from_today: bool = False
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.post("/api/lesson/result")
async def lesson_result(body: ResultIn, request: Request):
    rate_limit(client_host(request))
    if not db.get_entry(body.entry_id):
        raise HTTPException(404, "Not found.")
    rec = await agents.run_result(body.entry_id, body.remembered, body.already_requeued, body.from_today, body.day)
    return rec or {"requeue": False, "keep_in_today": None, "why": "Recorded"}


@app.get("/api/lesson/recent-filters")
def recent_filters(day_start: str | None = None):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM lesson_filters ORDER BY last_used_at DESC LIMIT 6")]
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        f = json.loads(r["filters_json"])
        p = f["period"]
        since = None if p == "all" else (day_start if p == "today" else (now - timedelta(days=int(p))).isoformat(timespec="seconds"))
        sql, args = _match_sql(f["kind"], f["direction"], since)
        with db.conn() as c:
            n = len(c.execute(sql, args).fetchall())
        out.append({**f, "id": r["id"], "label": r["label"], "count": n, "last_used_at": r["last_used_at"]})
    return out


# ---------- today's review ----------
@app.get("/api/review/today")
def review_today(day: str, day_start: str):
    if len(day) != 10 or len(day_start) > 40:
        raise HTTPException(400, "Bad day.")
    stale = False
    show_day = day
    if not db.today_has_rows(day):
        if day not in capture.REVIEW_BUSY:
            capture.enqueue(("rebuild", day, day_start))
        prev = db.latest_day_before(day)
        stale, show_day = True, prev or day
    rows = db.today_active(show_day)
    with db.conn() as c:
        removed = c.execute("SELECT COUNT(*) AS n FROM today_review WHERE day = ? AND removed_at IS NOT NULL", (show_day,)).fetchone()["n"]
    return {"day": show_day, "stale": stale, "updating": day in capture.REVIEW_BUSY, "count": len(rows), "removed": removed,
            "words": [{"entry_id": r["entry_id"], "target_text": r["target_text"], "english_text": r["english_text"],
                       "reason": r["reason"]} for r in rows]}


# ---------- wordbook ----------
class ExamplesIn(BaseModel):
    entry_ids: list[int] = Field(max_length=8)


@app.post("/api/wordbook/examples")
def wordbook_examples(body: ExamplesIn, request: Request):
    rate_limit(client_host(request))
    ids = []
    for i in body.entry_ids:
        e = db.get_entry(i)
        if (e and e["granularity"] == "word" and not e["example_checked"] and i not in capture.PENDING
                and "<" not in e["target_text"] and ">" not in e["target_text"]):
            ids.append(i)
        elif e and not e["example_checked"] and ("<" in e["target_text"] or ">" in e["target_text"]):
            with db.conn() as c:
                c.execute("UPDATE entries SET example_checked = 1 WHERE id = ?", (i,))
    if ids:
        capture.enqueue(("examples", ids))
    return {"queued": ids}


@app.get("/api/wordbook/pending")
def wordbook_pending():
    return {"pending": sorted(capture.PENDING), "capturing": capture.state["capturing"]}


# ---------- settings, logs, policy, usage ----------
class SettingsIn(BaseModel):
    target_lang: str = Field(pattern="^(es|de)$")


@app.get("/api/settings")
def get_settings():
    return {"native_lang": "en", "target_lang": db.target_lang()}


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    db.set_setting("target_lang", body.target_lang)
    return get_settings()


@app.get("/api/audit-log")
def audit_log():
    return audit.read(200)


@app.get("/api/policy")
def get_policy():
    return {name: a["tools"] for name, a in config.POLICY["agents"].items()}


@app.get("/api/usage")
def get_usage():
    return usage.snapshot()


# ---------- demo: check a request against the real guard (never executes) ----------
class GuardIn(BaseModel):
    input: str = Field(max_length=300)
    agent: str = Field(max_length=40)
    name: str = Field(max_length=60)
    args: dict = Field(default_factory=dict)


@app.post("/api/demo/guard-check")
def guard_check(body: GuardIn, request: Request):
    if client_host(request) not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "Only from this computer.")
    found = db.find_similar(body.input, db.target_lang(), capture.granularity_of(body.input))
    ctx = RunCtx(run_id="guard-check", task="guard_check", agent=body.agent, utterance=body.input,
                 target_lang=db.target_lang(), granularity=capture.granularity_of(body.input), searched=True,
                 found_ids={r["id"] for r in found["exact"] + found["similar"]})
    ok, reason = policy.guarded(body.agent, body.name, body.args, ctx)
    return {"decision": "allowed" if ok else "blocked", "reason": reason, "executed": False}


# ---------- static ----------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")

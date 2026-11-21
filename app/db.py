"""SQLite storage. Every query uses ? placeholders. Connections are closed after each use."""
import difflib
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  native_lang TEXT NOT NULL DEFAULT 'en',
  target_lang TEXT NOT NULL,
  direction TEXT NOT NULL,
  granularity TEXT NOT NULL,
  source_text TEXT NOT NULL,
  source_lang TEXT NOT NULL,
  translated_text TEXT NOT NULL,
  english_text TEXT NOT NULL,
  target_text TEXT NOT NULL,
  note TEXT,
  example_target TEXT,
  example_english TEXT,
  example_checked INTEGER NOT NULL DEFAULT 0,
  lookup_count INTEGER NOT NULL DEFAULT 1,
  is_weak INTEGER NOT NULL DEFAULT 0,
  review_count INTEGER NOT NULL DEFAULT 0,
  remembered_count INTEGER NOT NULL DEFAULT 0,
  remembered_streak INTEGER NOT NULL DEFAULT 0,
  last_result TEXT,
  tutor_note TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS lesson_filters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filters_json TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kept_words (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sentence_entry_id INTEGER NOT NULL,
  word TEXT NOT NULL,
  word_entry_id INTEGER NOT NULL,
  created_new INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS today_review (
  day TEXT NOT NULL,
  entry_id INTEGER NOT NULL,
  reason TEXT,
  added_at TEXT NOT NULL,
  removed_at TEXT,
  PRIMARY KEY (day, entry_id)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def clear_all() -> None:
    with conn() as c:
        for t in ("entries", "lesson_filters", "kept_words", "today_review", "settings"):
            c.execute(f"DELETE FROM {t}")  # fixed table names, not user input
        c.execute("DELETE FROM sqlite_sequence")


def row(r) -> dict | None:
    return dict(r) if r else None


def get_entry(entry_id: int) -> dict | None:
    with conn() as c:
        return row(c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone())


def get_setting(key: str, default: str) -> str:
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def target_lang() -> str:
    return get_setting("target_lang", "es")


# ---------- matching ----------
_PUNCT = re.compile(r"[.,;:!?¿¡\"“”«»()]")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = _PUNCT.sub(" ", t)
    return " ".join(t.split())


def similarity(a: str, b: str) -> float:
    """Return a 0..1 score; 0 means 'not a candidate' under the code rules."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    sa, sb = set(na.split()), set(nb.split())
    jac = len(sa & sb) / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    contains = sa <= sb or sb <= sa
    if contains or jac >= 0.5 or ratio >= 0.8:
        return round(max(jac, ratio, 0.75 if contains else 0), 3)
    return 0.0


def find_similar(text: str, target_lang: str, granularity: str) -> dict:
    n = normalize(text)
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, source_text, translated_text, lookup_count, granularity FROM entries "
            "WHERE target_lang = ? AND granularity = ?", (target_lang, granularity))]
    exact, similar = [], []
    for r in rows:
        if n and n in (normalize(r["source_text"]), normalize(r["translated_text"])):
            exact.append(r)
            continue
        score = max(similarity(text, r["source_text"]), similarity(text, r["translated_text"]))
        if score > 0:
            r["score"] = score
            similar.append(r)
    similar.sort(key=lambda r: r["score"], reverse=True)
    return {"exact": exact[:1], "similar": similar[:5]}


# ---------- today's review ----------
def today_active(day: str) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT t.entry_id, t.reason, t.added_at, e.target_text, e.english_text, e.lookup_count, e.is_weak, "
            "e.remembered_count, e.remembered_streak, e.last_result, e.last_reviewed_at "
            "FROM today_review t JOIN entries e ON e.id = t.entry_id "
            "WHERE t.day = ? AND t.removed_at IS NULL ORDER BY t.added_at DESC, t.rowid DESC", (day,))]


def today_has_rows(day: str) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM today_review WHERE day = ? LIMIT 1", (day,)).fetchone() is not None


def latest_day_before(day: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT MAX(day) AS d FROM today_review WHERE day < ?", (day,)).fetchone()
    return r["d"] if r and r["d"] else None

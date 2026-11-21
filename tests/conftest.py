"""Test settings: a temporary DB and audit log, and no network (TRANSTUDY_FAKE_AI=1)."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="transtudy-test-")
os.environ["TRANSTUDY_DB"] = str(Path(_tmp) / "test.db")
os.environ["TRANSTUDY_AUDIT"] = str(Path(_tmp) / "audit.jsonl")
os.environ["TRANSTUDY_FAKE_AI"] = "1"
os.environ["TRANSTUDY_FAKE_LIVE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    db.init()
    db.clear_all()
    yield


def add_entry(target="recibo", english="receipt", direction="couldnt_say", granularity="word", lookups=1, weak=0, created=None):
    ts = created or db.now()
    src, trans = (english, target) if direction == "couldnt_say" else (target, english)
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO entries(native_lang, target_lang, direction, granularity, source_text, source_lang, translated_text, "
            "english_text, target_text, lookup_count, is_weak, created_at, last_seen_at) VALUES('en','es',?,?,?,?,?,?,?,?,?,?,?)",
            (direction, granularity, src, "en" if direction == "couldnt_say" else "es", trans, english, target, lookups, weak, ts, ts))
        return cur.lastrowid

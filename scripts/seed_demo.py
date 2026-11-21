"""Demo data: ~15 Spanish entries with history and a full (7-word) Today's review.

  python scripts/seed_demo.py           # seed only if the wordbook is empty
  python scripts/seed_demo.py --reset   # empty the tables + audit log, then seed (server may keep running)
  python scripts/seed_demo.py --today   # keep the words, rebuild only today's review list
No Gemini or network calls.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import audit, db  # noqa: E402

NOW = datetime.now(timezone.utc)

# (target, english, direction, granularity, lookups, reviews, remembered, streak, last_result, days_ago, example_es, example_en, note)
WORDS = [
    ("la cuenta", "the bill", "couldnt_say", "word", 2, 4, 4, 2, "remembered", 6, "¿Nos trae la cuenta, por favor?", "Can you bring us the bill, please?", None),
    ("el equipaje", "the luggage", "couldnt_say", "word", 3, 3, 1, 0, "forgot", 5, "Mi equipaje no ha llegado.", "My luggage hasn't arrived.", None),
    ("la tarjeta", "the card", "couldnt_say", "word", 1, 1, 1, 1, "remembered", 5, "¿Puedo pagar con tarjeta?", "Can I pay by card?", None),
    ("bolsa", "bag", "couldnt_say", "word", 2, 1, 0, 0, "forgot", 3, "¿Quiere una bolsa?", "Do you want a bag?", None),
    ("el horario", "the timetable", "couldnt_say", "word", 3, 2, 0, 0, "forgot", 4, "El horario cambia en invierno.", "The timetable changes in winter.", None),
    ("el andén", "the platform", "didnt_catch", "word", 3, 2, 1, 0, "forgot", 4, "El tren sale del andén tres.", "The train leaves from platform three.", None),
    ("echar de menos", "to miss someone", "couldnt_say", "word", 2, 2, 1, 1, "remembered", 3, "Echo de menos a mi familia.", "I miss my family.", "idiom: to miss someone or something"),
    ("la parada", "the stop", "didnt_catch", "word", 1, 0, 0, 0, None, 2, "Bájate en la próxima parada.", "Get off at the next stop.", None),
    ("el plazo", "the deadline", "didnt_catch", "word", 3, 3, 2, 1, "remembered", 6, "El plazo termina el viernes.", "The deadline ends on Friday.", None),
    ("la matrícula", "the enrolment", "didnt_catch", "word", 1, 1, 1, 1, "remembered", 7, "La matrícula abre en septiembre.", "Enrolment opens in September.", None),
    ("el apunte", "the note (class notes)", "didnt_catch", "word", 2, 2, 1, 0, "forgot", 2, "¿Me pasas los apuntes de ayer?", "Can you send me yesterday's notes?", "usually plural: class notes"),
    ("el cajero", "the cash machine", "couldnt_say", "word", 1, 1, 1, 1, "remembered", 1, "El cajero está en la esquina.", "The cash machine is on the corner.", None),
    ("¿Tiene una talla más grande?", "Do you have a bigger size?", "couldnt_say", "sentence", 1, 1, 0, 0, "forgot", 3, None, None, None),
    ("¿Me lo puede envolver?", "Can you wrap it for me?", "couldnt_say", "sentence", 2, 1, 1, 1, "remembered", 2, None, None, None),
    ("Se me ha olvidado la contraseña.", "I forgot my password.", "didnt_catch", "sentence", 1, 0, 0, 0, None, 1, None, None, None),
]
TODAY_REVIEW = [  # 7 words: weak, forgotten last time, and la cuenta remembered twice in a row
    ("el equipaje", "weak word: looked up 3 times, forgot last time"),
    ("el horario", "weak word, forgot last time"),
    ("el andén", "weak word you keep hearing at the station"),
    ("el plazo", "weak word, not reviewed for 6 days"),
    ("el apunte", "forgot last time"),
    ("echar de menos", "idiom, still new"),
    ("la cuenta", "remembered twice in a row; easy check"),
]


def seed_words() -> None:
    with db.conn() as c:
        for i, (tgt, eng, d, g, lk, rv, rm, st, lr, ago, ex, exe, note) in enumerate(WORDS):
            created = (NOW - timedelta(days=ago, minutes=len(WORDS) - i)).isoformat(timespec="seconds")
            seen = (NOW - timedelta(days=max(0, ago - 1), minutes=len(WORDS) - i)).isoformat(timespec="seconds")
            reviewed = (NOW - timedelta(days=max(1, ago - 1))).isoformat(timespec="seconds") if rv else None
            src, src_lang, trans = (eng, "en", tgt) if d == "couldnt_say" else (tgt, "es", eng)
            c.execute(
                "INSERT INTO entries(native_lang, target_lang, direction, granularity, source_text, source_lang, translated_text, "
                "english_text, target_text, note, example_target, example_english, example_checked, lookup_count, is_weak, "
                "review_count, remembered_count, remembered_streak, last_result, created_at, last_seen_at, last_reviewed_at) "
                "VALUES('en', 'es', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d, g, src, src_lang, trans, eng, tgt, note, ex, exe, lk, 1 if lk >= 3 else 0, rv, rm, st, lr, created, seen, reviewed))


def seed_today() -> None:
    day = date.today().isoformat()  # the PC's local date, same as the browser
    with db.conn() as c:
        c.execute("DELETE FROM today_review WHERE day = ?", (day,))
        for i, (tgt, reason) in enumerate(TODAY_REVIEW):
            r = c.execute("SELECT id FROM entries WHERE target_text = ? AND target_lang = 'es'", (tgt,)).fetchone()
            if r:
                added = (NOW - timedelta(minutes=10 * (i + 1))).isoformat(timespec="seconds")
                c.execute("INSERT INTO today_review(day, entry_id, reason, added_at) VALUES(?, ?, ?, ?)", (day, r["id"], reason, added))


def main() -> None:
    db.init()
    args = set(sys.argv[1:])
    if "--today" in args:
        seed_today()
        print("Today's review rebuilt (7 words).")
        return
    if "--reset" in args:
        db.clear_all()
        audit.clear()
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    if n and "--reset" not in args:
        print(f"Wordbook already has {n} entries. Nothing changed (use --reset).")
        return
    db.set_setting("target_lang", "es")
    seed_words()
    seed_today()
    print(f"Seeded {len(WORDS)} entries and a 7-word Today's review.")


if __name__ == "__main__":
    main()

from app import db
from tests.conftest import add_entry


def test_normalize_ignores_case_accents_punctuation():
    assert db.normalize("¿Cómo estás?") == db.normalize("como estas")
    assert db.normalize("Receipt.") == "receipt"


def test_exact_match_on_source_or_translation():
    eid = add_entry("recibo", "receipt")
    assert db.find_similar("Receipt", "es", "word")["exact"][0]["id"] == eid
    assert db.find_similar("Recibo", "es", "word")["exact"][0]["id"] == eid


def test_greeting_variant_is_similar_but_other_question_is_not():
    eid = add_entry("¿Cómo estás?", "How are you?", "didnt_catch", "sentence")
    r = db.find_similar("Hola, ¿cómo estás?", "es", "sentence")
    assert [x["id"] for x in r["similar"]] == [eid]
    r2 = db.find_similar("¿Dónde estás?", "es", "sentence")
    assert r2["exact"] == [] and r2["similar"] == []


def test_words_and_sentences_are_not_mixed():
    add_entry("recibo", "receipt")
    assert db.find_similar("recibo", "es", "sentence") == {"exact": [], "similar": []}

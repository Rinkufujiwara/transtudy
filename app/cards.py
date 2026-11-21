"""A2UI-style cards.

An agent never writes HTML. It describes a card as JSON, using only the kinds and fields in CATALOG,
and the app draws it (static/cards.js). Every button is a whitelisted action that the server handles
from the facts it stored with the card, never from what the browser or the model sends back.

Card kinds
  extract   Capture Agent: what to learn from a sentence (words, expressions, grammar). The person
            unticks what they do not want, then saves. Nothing is saved until they press the button.
  proposal  Tutor Agent: add a weak word to today's review and drop another. Dropping waits for the
            person's approval (policy.yaml: approval_required).
  contrast  Tutor Agent: a word that looks like one already in the wordbook, and how they differ.
  meaning   Capture Agent: a learning-language word with clearly different meanings (banco: bank / bench).
            The person taps the one they meant; it becomes the word's English line.

Standard library only, so it can be tested without the app.
"""
from __future__ import annotations

import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------- limits

MAX_ITEMS = 3            # items on one card
MAX_ITEM_CHARS = 40      # one extracted word / expression / grammar span
MAX_ITEM_TOKENS = 6
MAX_NOTE_CHARS = 60      # gloss or reason shown under an item
MAX_TITLE_CHARS = 60
CARD_TTL_SEC = 600       # a card can be answered for 10 minutes, once
MAX_OPEN_CARDS = 3       # older cards are dropped when a fourth arrives

EXTRACT_KINDS = ("word", "expression", "grammar")

# Grammar points the Capture Agent may name. A fixed list, so a label can never carry an instruction.
GRAMMAR_LABELS = (
    "two pronouns together",
    "subjunctive",
    "reflexive verb",
    "polite question",
    "past tense",
    "imperfect tense",
    "future tense",
    "conditional",
    "ser vs estar",
    "por vs para",
    "gustar-type verb",
    "object pronoun",
    "gender agreement",
    "separable verb",
    "dative case",
    "word order",
)

CATALOG = {
    "extract": {"agent": "capture_agent", "actions": ("save_selected", "dismiss")},
    "proposal": {"agent": "tutor_agent", "actions": ("approve", "choose_drop", "dismiss")},
    "contrast": {"agent": "tutor_agent", "actions": ("got_it", "dismiss")},
    "meaning": {"agent": "capture_agent", "actions": ("choose_meaning", "dismiss")},
}

_PUNCT = "¿¡?!.,;:\"'«»“”‘’()…"
_BAD = re.compile(r"[<>]|://|[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ---------------------------------------------------------------- text helpers

def fold(text: str) -> str:
    """Lower case, no accents, no punctuation, single spaces. 'Envuelva?' -> 'envuelva'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = "".join(" " if c in _PUNCT else c for c in text)
    return " ".join(text.split())


def plain(text, limit: int) -> str | None:
    """The text if it is short plain text, else None (no tags, no links, no control characters)."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text or len(text) > limit or _BAD.search(text):
        return None
    return text


def in_sentence(span: str, sentence: str) -> bool:
    """True if span is a run of whole words in the sentence (case, accents and punctuation ignored)."""
    s, t = fold(span).split(), fold(sentence).split()
    if not s or len(s) > len(t):
        return False
    return any(t[i:i + len(s)] == s for i in range(len(t) - len(s) + 1))


# ---------------------------------------------------------------- guard checks (called from before_tool_callback)

def items_from_lists(args: dict) -> list[dict] | None:
    """The tool takes three parallel lists (simple types keep the Gemini function schema valid):
    texts, kinds, notes. For grammar the note is the label. Returns item dicts, or None if malformed."""
    texts, kinds, notes = args.get("texts"), args.get("kinds"), args.get("notes")
    if not isinstance(texts, list) or not isinstance(kinds, list):
        return None
    if notes is None:
        notes = [""] * len(texts)
    if not isinstance(notes, list) or not len(texts) == len(kinds) == len(notes):
        return None
    items = []
    for t, k, n in zip(texts, kinds, notes):
        item = {"text": t, "kind": k, "note": n or ""}
        if k == "grammar":
            item["label"] = n
            item["note"] = ""
        items.append(item)
    return items


def check_extraction(args: dict, *, source_text: str, granularity: str,
                     already_proposed: bool) -> tuple[bool, str]:
    """Guard for the Capture Agent's propose_extraction(items, why).

    The model is asked to call it in the same response as save_entry / bump_lookup_count, so it cannot
    know the entry id yet: the server attaches the card to this input's entry itself. The facts
    (source_text, granularity) come from the server's record of this capture, not from the model.
    Returns (allowed, reason).
    """
    if granularity != "sentence":
        return False, "extraction is only for sentences"
    if already_proposed:
        return False, "one extraction card per input"
    items = args.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        return False, f"items must be a list of 1 to {MAX_ITEMS}"
    seen = set()
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return False, f"item {n} is not an object"
        text = plain(item.get("text"), MAX_ITEM_CHARS)
        if text is None:
            return False, f"item {n}: text must be plain text of at most {MAX_ITEM_CHARS} characters"
        if len(text.split()) > MAX_ITEM_TOKENS:
            return False, f"item {n}: at most {MAX_ITEM_TOKENS} words"
        if not in_sentence(text, source_text):
            return False, f"item {n}: \"{text}\" is not in the input sentence"
        kind = item.get("kind")
        if kind not in EXTRACT_KINDS:
            return False, f"item {n}: kind must be one of {', '.join(EXTRACT_KINDS)}"
        if kind == "grammar" and item.get("label") not in GRAMMAR_LABELS:
            return False, f"item {n}: grammar label is not in the allowed list"
        note = item.get("note", "")
        if note and plain(note, MAX_NOTE_CHARS) is None:
            return False, f"item {n}: note must be plain text of at most {MAX_NOTE_CHARS} characters"
        key = fold(text)
        if key in seen:
            return False, f"item {n}: duplicate"
        seen.add(key)
    why = args.get("why", "")
    if why and plain(why, 120) is None:
        return False, "why must be plain text of at most 120 characters"
    return True, "ok"


def check_contrast(args: dict, *, new_entry_id: int, similar_ids: set[int],
                   already_shown: bool) -> tuple[bool, str]:
    """Guard for the Tutor Agent's show_contrast(new_entry_id, other_entry_id, difference, why)."""
    if already_shown:
        return False, "one contrast card per input"
    if args.get("new_entry_id") != new_entry_id:
        return False, "new_entry_id is not the word of this input"
    if args.get("other_entry_id") not in similar_ids:
        return False, "other_entry_id was not returned by find_similar_entries"
    if plain(args.get("difference"), MAX_NOTE_CHARS * 2) is None:
        return False, "difference must be plain text of at most 120 characters"
    return True, "ok"


def check_meaning(args: dict, *, granularity: str, direction: str, already_asked: bool) -> tuple[bool, str]:
    """Guard for the Capture Agent's ask_meaning(meanings, why)."""
    if granularity != "word":
        return False, "ask_meaning is only for a single word"
    if direction != "didnt_catch":
        return False, "ask_meaning is only for a word in the learning language"
    if already_asked:
        return False, "one meaning card per input"
    meanings = args.get("meanings")
    if not isinstance(meanings, list) or not 2 <= len(meanings) <= MAX_ITEMS:
        return False, f"meanings must be a list of 2 to {MAX_ITEMS}"
    seen = set()
    for n, m in enumerate(meanings, 1):
        if plain(m, MAX_ITEM_CHARS) is None:
            return False, f"meaning {n} must be plain text of at most {MAX_ITEM_CHARS} characters"
        if fold(m) in seen:
            return False, f"meaning {n}: duplicate"
        seen.add(fold(m))
    return True, "ok"


def needs_approval(tool: str, args: dict, approval_required: dict) -> bool:
    """policy.yaml approval_required, e.g. {"update_today_review": ["drop_entry_id"]}:
    the call is held for the person when any of the listed arguments is set."""
    fields = approval_required.get(tool)
    if fields is None:
        return False
    if not fields:
        return True
    return any(args.get(f) not in (None, "", []) for f in fields)


# ---------------------------------------------------------------- card builders (server side, from checked data)

def extract_card(items: list[dict], known_texts: set[str]) -> dict:
    """known_texts: folded texts already in the wordbook (they start unticked and say so)."""
    rows = []
    for i, it in enumerate(items):
        known = fold(it["text"]) in known_texts
        note = it.get("label") if it["kind"] == "grammar" else it.get("note", "")
        rows.append({"id": i, "text": it["text"], "kind": it["kind"], "note": note or "",
                     "known": known, "checked": not known})
    n = len(rows)
    return {"card": "extract", "agent": "capture_agent",
            "title": f"{n} thing{'s' if n != 1 else ''} to learn from this sentence",
            "items": rows,
            "buttons": [{"label": "Save to my wordbook", "action": "save_selected", "style": "primary"},
                        {"label": "Not now", "action": "dismiss"}]}


def proposal_card(add: dict, drop: dict | None, why: str, today: list[dict]) -> dict:
    """add / drop / today items: {"id", "text"}; why: the Tutor's one-line reason."""
    rows = [{"id": add["id"], "text": add["text"], "role": "add", "note": "add to today's review"}]
    if drop:
        rows.append({"id": drop["id"], "text": drop["text"], "role": "drop", "note": "drop from today's review"})
    choices = [{"id": w["id"], "text": w["text"]} for w in today if w["id"] != add["id"]]
    buttons = [{"label": "Approve", "action": "approve", "style": "primary"}]
    if drop and len(choices) > 1:
        buttons.append({"label": "Drop another word", "action": "choose_drop"})
    return {"card": "proposal", "agent": "tutor_agent", "title": "Proposes a change to today's review",
            "why": why, "items": rows, "choices": choices, "buttons": buttons}


def contrast_card(new: dict, other: dict, difference: str) -> dict:
    return {"card": "contrast", "agent": "tutor_agent", "title": "Looks similar, means something else",
            "why": difference,
            "items": [{"id": new["id"], "text": new["text"], "note": new.get("english", "")},
                      {"id": other["id"], "text": other["text"], "note": other.get("english", "")}],
            "buttons": [{"label": "Got it", "action": "got_it", "style": "primary"}]}


def meaning_card(word: str, meanings: list[str]) -> dict:
    return {"card": "meaning", "agent": "capture_agent", "title": f"Which meaning of “{word[:30]}”?",
            "items": [{"id": i, "text": " ".join(m.split())} for i, m in enumerate(meanings)],
            "buttons": [{"label": "Not sure", "action": "dismiss"}]}


def validate_card(card: dict) -> tuple[bool, str]:
    """Last check before a card goes to the browser: only catalog kinds, actions and plain text."""
    spec = CATALOG.get(card.get("card"))
    if not spec:
        return False, "unknown card kind"
    if card.get("agent") != spec["agent"]:
        return False, "this agent may not show this card"
    if plain(card.get("title"), MAX_TITLE_CHARS) is None:
        return False, "bad title"
    if card.get("why") and plain(card["why"], 160) is None:
        return False, "bad reason"
    items = card.get("items") or []
    if not 1 <= len(items) <= MAX_ITEMS:
        return False, "bad item count"
    for it in items:
        if plain(it.get("text"), MAX_ITEM_CHARS * 2) is None:
            return False, "bad item text"
        if it.get("note") and plain(it["note"], MAX_NOTE_CHARS * 2) is None:
            return False, "bad item note"
    for b in card.get("buttons") or []:
        if b.get("action") not in spec["actions"] or plain(b.get("label"), 30) is None:
            return False, "button action not allowed"
    for c in card.get("choices") or []:
        if plain(c.get("text"), MAX_ITEM_CHARS * 2) is None:
            return False, "bad choice"
    return True, "ok"


# ---------------------------------------------------------------- pending cards

@dataclass
class Pending:
    card: dict
    facts: dict                      # what the server needs to act (ids, texts) — never sent to the browser
    created: float = field(default_factory=time.monotonic)


class CardStore:
    """Cards waiting for the person. One answer per card; 10 minutes; at most 3 open."""

    def __init__(self, ttl: float = CARD_TTL_SEC, max_open: int = MAX_OPEN_CARDS):
        self.ttl, self.max_open = ttl, max_open
        self._cards: dict[str, Pending] = {}

    def put(self, card: dict, facts: dict) -> dict:
        ok, why = validate_card(card)
        if not ok:
            raise ValueError(why)
        self._expire()
        while len(self._cards) >= self.max_open:
            self._cards.pop(next(iter(self._cards)))
        card_id = secrets.token_urlsafe(12)
        self._cards[card_id] = Pending(card, facts)
        return {**card, "card_id": card_id}

    def take(self, card_id: str, action: str, payload: dict | None = None) -> tuple[Pending, dict]:
        """Check the person's answer against the stored card and remove the card.
        Returns (pending, cleaned) where cleaned holds only checked values. Raises ValueError."""
        self._expire()
        p = self._cards.get(card_id)
        if p is None:
            raise ValueError("This card has expired.")
        if action not in CATALOG[p.card["card"]]["actions"]:
            raise ValueError("Action not allowed for this card.")
        payload = payload or {}
        cleaned: dict = {}
        if action == "save_selected":
            ids = {it["id"] for it in p.card["items"]}
            sel = payload.get("selected")
            if not isinstance(sel, list) or not sel or not all(isinstance(i, int) and i in ids for i in sel):
                raise ValueError("Choose at least one item.")
            cleaned["selected"] = sorted(set(sel))
        elif action == "choose_meaning":
            ids = {it["id"] for it in p.card["items"]}
            if payload.get("choice") not in ids:
                raise ValueError("Choose one meaning.")
            cleaned["choice"] = payload["choice"]
        elif action == "choose_drop":
            ids = {c["id"] for c in p.card.get("choices", [])}
            if payload.get("drop_entry_id") not in ids:
                raise ValueError("Choose a word from today's review.")
            cleaned["drop_entry_id"] = payload["drop_entry_id"]
        del self._cards[card_id]
        return p, cleaned

    def _expire(self) -> None:
        now = time.monotonic()
        for k in [k for k, p in self._cards.items() if now - p.created > self.ttl]:
            del self._cards[k]

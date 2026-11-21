"""Cloud Translation API v2 (REST). The key goes in the X-Goog-Api-Key header, never in a URL."""
import httpx

from . import config, usage

BASE = "https://translation.googleapis.com/language/translate/v2"
_client: httpx.AsyncClient | None = None

# Offline dictionary for TRANSTUDY_FAKE_AI=1 (tests and UI checks without network).
FAKE = {
    ("en", "es"): {"receipt": "recibo", "bag": "bolsa", "the bill": "la cuenta", "hello": "hola",
                   "can you wrap it for me?": "¿Me lo puede envolver?", "card": "tarjeta"},
    ("es", "en"): {"recibo": "receipt", "bolsa": "bag", "¿quiere que se lo envuelva?": "Do you want me to wrap it?",
                   "envuelva": "wrap", "la cuenta": "the bill", "tarjeta": "card"},
}
ES_HINTS = set("el la los las que se lo quiere de por favor gracias hola recibo bolsa cuenta tarjeta envuelva cómo dónde estás".split())
DE_HINTS = set("der die das ist ich und nicht danke bitte quittung tasche".split())


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10, headers={"X-Goog-Api-Key": config.GOOGLE_TRANSLATE_API_KEY})
    return _client


def _fake_detect(text: str) -> str:
    words = set(text.lower().replace("¿", " ").replace("?", " ").split())
    if any(c in text for c in "¿¡ñáéíóú") or words & ES_HINTS:
        return "es"
    if words & DE_HINTS or any(c in text for c in "äöüß"):
        return "de"
    return "en"


async def detect(text: str, step: str = "detect") -> str:
    if config.FAKE_AI:
        return _fake_detect(text)
    try:
        r = await client().post(f"{BASE}/detect", json={"q": text})
        r.raise_for_status()
        return r.json()["data"]["detections"][0][0]["language"].split("-")[0]
    except Exception as e:  # noqa: BLE001
        usage.fail("translation", step, usage.status_of(e))
        raise


async def translate(text: str, source: str, target: str, step: str = "translate") -> str:
    if config.FAKE_AI:
        d = FAKE.get((source, target), {})
        return d.get(text.strip().lower(), f"[{target}] {text}")
    try:
        r = await client().post(BASE, json={"q": text, "source": source, "target": target, "format": "text"})
        r.raise_for_status()
        return r.json()["data"]["translations"][0]["translatedText"]
    except Exception as e:  # noqa: BLE001
        usage.fail("translation", step, usage.status_of(e))
        raise

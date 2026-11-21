"""Settings loaded from .env and policy.yaml (no secrets are ever logged)."""
import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_TRANSLATE_API_KEY = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "") or "gemini-2.5-flash"
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "") or "gemini-3.5-transcribe-live"

# Offline switches for development and tests (no network calls).
FAKE_AI = os.getenv("TRANSTUDY_FAKE_AI", "") == "1"      # agents, Gemini, Translation
FAKE_LIVE = os.getenv("TRANSTUDY_FAKE_LIVE", "") == "1"  # Gemini Live stand-in

DB_PATH = Path(os.getenv("TRANSTUDY_DB", str(ROOT / "data" / "transtudy.db")))
AUDIT_PATH = Path(os.getenv("TRANSTUDY_AUDIT", str(ROOT / "logs" / "audit.jsonl")))
POLICY_PATH = ROOT / "policy.yaml"

with open(POLICY_PATH, encoding="utf-8") as f:
    POLICY = yaml.safe_load(f)

LIMITS = POLICY["limits"]
TARGET_LANGS = POLICY["languages"]["target"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full URLs at INFO; keep it quiet so nothing sensitive is printed.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_adk").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)

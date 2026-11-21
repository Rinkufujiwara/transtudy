"""WebSocket relay: browser PCM (16 kHz mono 16-bit) -> Gemini Live transcription -> translation.

The API key stays on the server. With TRANSTUDY_FAKE_LIVE=1 a stand-in returns fixed text
so the browser + real server path can be tested without the Live API.
"""
import asyncio
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from . import capture, config, db, translate, usage

log = logging.getLogger("transtudy.live")
MAX_AUDIO_BYTES = 16000 * 2 * 15  # 15 s
FAKE_TEXT = "receipt"


def _norm(t: str) -> str:
    return db.normalize(t)


class Session:
    def __init__(self, ws: WebSocket, tl: str):
        self.ws, self.tl = ws, tl
        self.final, self.interim = "", ""
        self.last_text_at = 0.0
        self.pre_stop_text = ""
        self.partial = {"text": "", "lang": None, "translated": None}
        self.partial_busy = False
        self.partial_last = 0.0
        self.lang_cache = None
        self.closed = False

    @property
    def full(self) -> str:
        return (self.final + self.interim).strip()

    async def send(self, msg: dict):
        if not self.closed:
            try:
                await self.ws.send_json(msg)
            except Exception:  # noqa: BLE001
                self.closed = True

    def on_text(self, final: str | None, interim: str | None):
        if final:
            self.final += final
            self.interim = ""
        if interim:
            self.interim = interim
        if final or interim:
            self.last_text_at = time.monotonic()
            asyncio.create_task(self.send({"type": "interim", "text": self.full}))
            asyncio.create_task(self.maybe_partial())

    async def maybe_partial(self):
        """Provisional translation (Translation API only, never Gemini). Kept server-side."""
        if self.partial_busy or time.monotonic() - self.partial_last < 0.3:
            return
        text = self.full
        if not text or text == self.partial["text"]:
            return
        self.partial_busy = True
        try:
            if len(text.split()) < 4 or self.lang_cache is None:
                self.lang_cache = await translate.detect(text, "live_partial")
            lang = self.lang_cache
            if lang not in ("en", self.tl):
                return
            out = await translate.translate(text, lang, self.tl if lang == "en" else "en", "live_partial")
            self.partial = {"text": text, "lang": lang, "translated": out}
            await self.send({"type": "partial", "text": text, "translated": out})
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.partial_busy = False
            self.partial_last = time.monotonic()


async def _fake_live(s: Session, audio_q: asyncio.Queue, stopped: asyncio.Event):
    sent = 0
    while not stopped.is_set():
        try:
            await asyncio.wait_for(audio_q.get(), 0.2)
        except asyncio.TimeoutError:
            continue
        sent += 1
        if sent == 3:
            s.on_text(None, FAKE_TEXT[:3])
        elif sent == 6:
            s.on_text(None, FAKE_TEXT)
    s.on_text(FAKE_TEXT, None)


async def _real_live(s: Session, audio_q: asyncio.Queue, stopped: asyncio.Event, failed: asyncio.Event):
    from google.genai import types
    from .speech import client
    cfg = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=["en-US", "es-ES" if s.tl == "es" else "de-DE"]))
    usage.count("live")
    try:
        async with client().aio.live.connect(model=config.GEMINI_LIVE_MODEL, config=cfg) as session:
            async def pump():
                while True:
                    chunk = await audio_q.get()
                    if chunk is None:
                        await session.send_realtime_input(audio_stream_end=True)
                        return
                    await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))

            async def recv():
                while True:
                    async for msg in session.receive():
                        sc = msg.server_content
                        if not sc:
                            continue
                        fin = sc.input_transcription.text if sc.input_transcription and sc.input_transcription.text else None
                        inter = sc.interim_input_transcription.text if sc.interim_input_transcription and sc.interim_input_transcription.text else None
                        s.on_text(fin, inter)
            pt, rt = asyncio.create_task(pump()), asyncio.create_task(recv())
            await stopped.wait()
            await asyncio.sleep(3)  # finalisation window is handled by the caller; keep the session open
            pt.cancel()
            rt.cancel()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        usage.fail("gemini", "live", usage.status_of(e))
        failed.set()


async def handle(ws: WebSocket):
    origin = ws.headers.get("origin", "")
    host = ws.headers.get("host", "")
    if not origin or origin.split("://", 1)[-1] != host:
        await ws.close(code=1008)  # before accept -> HTTP 403
        return
    await ws.accept()
    s = Session(ws, db.target_lang())
    audio_q: asyncio.Queue = asyncio.Queue()
    stopped, failed = asyncio.Event(), asyncio.Event()
    runner = asyncio.create_task(_fake_live(s, audio_q, stopped) if config.FAKE_LIVE else _real_live(s, audio_q, stopped, failed))
    received = 0
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                received += len(msg["bytes"])
                if received <= MAX_AUDIO_BYTES:
                    audio_q.put_nowait(msg["bytes"])
            elif msg.get("text") == "stop":
                break
        s.pre_stop_text = s.full
        stop_at = time.monotonic()
        audio_q.put_nowait(None)
        stopped.set()
        # Live rarely sends turn_complete: settle 0.7 s after the later of stop / last text (max 2.5 s).
        while True:
            if failed.is_set():
                break
            now = time.monotonic()
            if now - max(stop_at, s.last_text_at) >= 0.7 or now - stop_at >= 2.5:
                break
            await asyncio.sleep(0.05)
        text = s.full
        if failed.is_set() or not text or len(text) > config.LIMITS["max_text_chars"]:
            await s.send({"type": "fallback"})
            return
        pre = s.pre_stop_text
        lang, translated = None, None
        # Short-word guard: "receipt" can turn into "recibo" at finalisation and flip the direction.
        if (len(text.split()) <= 3 and pre and _norm(pre) != _norm(text)
                and s.partial["text"] == pre and s.partial["translated"]):
            text, lang, translated = pre, s.partial["lang"], s.partial["translated"]
        else:
            deadline = time.monotonic() + 0.8
            while s.partial_busy and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if s.partial["text"] == text and s.partial["translated"]:
                lang, translated = s.partial["lang"], s.partial["translated"]
        try:
            result = await capture.prepare(text, lang, translated)
        except capture.InputError as e:
            await s.send({"type": "error", "detail": str(e)})
            return
        await s.send({"type": "result", **result})
    except WebSocketDisconnect:
        return
    except Exception as e:  # noqa: BLE001
        log.warning("live session failed: %s", type(e).__name__)
        await s.send({"type": "fallback"})
    finally:
        s.closed = True
        runner.cancel()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

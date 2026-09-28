"""Voice notes → text (roadmap 2026-09 §5.7): "پنجاه هزار تومن نون خریدم"
said into the chat, or sent as a voice message to the bot, becomes the
sentence the assistant then books.

The same provider as the rest of the AI: Gemini through Metis's Google-format
wrapper first (it reads Persian speech well, and is the OCR model already),
then the OpenAI-compatible ``/audio/transcriptions`` endpoint of the active
backend (Whisper-family models). Every call is metered and budgeted like a
chat turn. The audio is never stored.
"""
from __future__ import annotations

import base64
import logging
import re

import httpx

from app.core.ai_runtime import resolve_active_ai_backend
from app.core.config import settings
from app.services.ai_usage import AIBudgetExceeded, metered_llm, usage_from_gemini, usage_from_openai

log = logging.getLogger("app.speech")

MAX_BYTES = 10 * 1024 * 1024          # ~10 minutes of Opus; a voice note is seconds
# a dead provider fails in seconds (the user is waiting), a slow one gets a minute
TIMEOUT = httpx.Timeout(60.0, connect=8.0)
PROMPT = (
    "Transcribe this voice note exactly as spoken, in the language spoken (usually Persian or English). "
    "It is someone telling their bookkeeping app about money: keep every amount, currency word, name and date. "
    "Output only the transcript — no quotes, labels or translation."
)


class SpeechError(Exception):
    """The audio could not be read or no provider could transcribe it."""


def sniff(data: bytes) -> str | None:
    """The audio format from its first bytes (the browser's or messenger's
    content type is not trusted)."""
    head = data[:16]
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "audio/aac"
    return None


_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3",
        "audio/mp4": "m4a", "audio/aac": "aac"}


def _clean(text: str) -> str:
    t = (text or "").strip().strip('"“”«»').strip()
    return re.sub(r"\s+", " ", t)


def _gemini_ready() -> bool:
    model = (settings.stt_gemini_model or "").strip()
    key = (resolve_active_ai_backend().get("api_key") or "").strip()
    return bool(model.lower().startswith("gemini") and key and (settings.gemini_base_url or "").strip())


async def _gemini(data: bytes, mime: str) -> str:
    model = settings.stt_gemini_model.strip()
    key = (resolve_active_ai_backend().get("api_key") or "").strip()
    url = f"{settings.gemini_base_url.strip().rstrip('/')}/models/{model}:generateContent"
    payload = {"contents": [{"parts": [{"text": PROMPT},
                                       {"inline_data": {"mime_type": mime,
                                                        "data": base64.b64encode(data).decode("ascii")}}]}],
               "generationConfig": {"temperature": 0}}
    async with metered_llm("gemini", model, "speech") as meter, httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(url, json=payload, headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        r.raise_for_status()
        body = r.json()
        meter.ok(usage_from_gemini(body), prompt=PROMPT, output=body.get("candidates"))
    parts = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


def _transcriptions_url(base: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith("/chat/completions"):
        b = b[: -len("/chat/completions")]
    if "/openai/" in b or "/wrapper/" in b or re.search(r"/v\d+$", b):
        return f"{b}/audio/transcriptions"
    return f"{b}/v1/audio/transcriptions"


async def _openai(data: bytes, mime: str) -> str:
    from app.services.ocr_extract import _resolve_ai_headers
    cfg = resolve_active_ai_backend()
    base = (cfg.get("base_url") or "").strip()
    if not base:
        raise SpeechError("no AI backend configured")
    model = (settings.stt_model or "whisper-1").strip()
    files = {"file": (f"voice.{_EXT.get(mime, 'bin')}", data, mime)}
    async with metered_llm(cfg.get("provider") or "openai-compatible", model, "speech") as meter, \
            httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(_transcriptions_url(base), data={"model": model, "response_format": "json"},
                              files=files, headers=_resolve_ai_headers() or None)
        r.raise_for_status()
        body = r.json()
        meter.ok(usage_from_openai(body), prompt="(audio)", output=body.get("text"))
    return body.get("text") or ""


async def transcribe(data: bytes) -> dict:
    """``{"text": ..., "provider": "gemini" | "openai"}``. Raises
    SpeechError (unreadable audio, every provider failed) or
    AIBudgetExceeded."""
    if not data:
        raise SpeechError("The recording is empty.")
    if len(data) > MAX_BYTES:
        raise SpeechError("The recording is longer than this app accepts (10 MB).")
    mime = sniff(data)
    if mime is None:
        raise SpeechError("That is not an audio format this app reads.")
    last: Exception | None = None
    chain = ([("gemini", _gemini)] if _gemini_ready() else []) + [("openai", _openai)]
    for name, fn in chain:
        try:
            text = _clean(await fn(data, mime))
        except AIBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 — try the next provider
            last = exc
            log.warning("transcription via %s failed: %r", name, exc)
            continue
        if text:
            return {"text": text, "provider": name}
    raise SpeechError("Could not transcribe the recording — try again, or type it.") from last

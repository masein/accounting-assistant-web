"""Voice notes (roadmap 2026-09 §5.7): a recording from the chat's microphone
button becomes text through the configured AI provider — Gemini first, then
the OpenAI-compatible transcription endpoint — metered and budgeted like a
chat turn; the audio is never stored."""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.services import speech

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 60
OGG = b"OggS" + b"\x00" * 60


@pytest.mark.parametrize("data,mime", [
    (WEBM, "audio/webm"), (OGG, "audio/ogg"), (b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 8, "audio/wav"),
    (b"ID3\x03" + b"\x00" * 20, "audio/mpeg"), (b"\xff\xfb\x90" + b"\x00" * 20, "audio/mpeg"),
    (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8, "audio/mp4"), (b"\xff\xf1\x50" + b"\x00" * 20, "audio/aac"),
])
def test_audio_formats_are_recognised_from_their_bytes(data, mime):
    assert speech.sniff(data) == mime


@pytest.mark.parametrize("data", [b"%PDF-1.7 ...", b"\x89PNG\r\n\x1a\n....", b"hello world"])
def test_anything_else_is_not_audio(data):
    assert speech.sniff(data) is None


@pytest.mark.parametrize("base,url", [
    ("https://api.metisai.ir/openai/v1", "https://api.metisai.ir/openai/v1/audio/transcriptions"),
    ("https://api.openai.com/v1/", "https://api.openai.com/v1/audio/transcriptions"),
    ("https://x.example/v1/chat/completions", "https://x.example/v1/audio/transcriptions"),
    ("http://host:1234", "http://host:1234/v1/audio/transcriptions"),
])
def test_the_transcription_url_follows_the_chat_base(base, url):
    assert speech._transcriptions_url(base) == url


# ─── The providers, over a mocked network ───────────────────────────────────

@pytest.fixture()
def backend(monkeypatch):
    monkeypatch.setattr(speech, "resolve_active_ai_backend",
                        lambda: {"provider": "metis", "base_url": "https://api.metisai.ir/openai/v1", "api_key": "k-1"})
    import app.services.ocr_extract as ocr
    monkeypatch.setattr(ocr, "resolve_active_ai_backend",
                        lambda: {"provider": "metis", "base_url": "https://api.metisai.ir/openai/v1", "api_key": "k-1",
                                 "api_key_header": "Authorization", "api_key_prefix": "Bearer"})
    calls: list[httpx.Request] = []
    replies: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        for key, resp in replies.items():
            if key in str(request.url):
                return resp
        return httpx.Response(500, json={"error": "no route"})
    real = httpx.AsyncClient
    monkeypatch.setattr(speech.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return calls, replies


def _gemini_reply(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}],
                                     "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 9}})


def test_gemini_transcribes_first(backend):
    calls, replies = backend
    replies[":generateContent"] = _gemini_reply(' «پنجاه هزار تومن نون خریدم» \n')
    out = asyncio.run(speech.transcribe(WEBM))
    assert out == {"text": "پنجاه هزار تومن نون خریدم", "provider": "gemini"}
    req = calls[0]
    assert req.url.path.endswith("/models/gemini-3.7-flash:generateContent") and req.headers["x-goog-api-key"] == "k-1"
    body = json.loads(req.content)
    part = body["contents"][0]["parts"][1]["inline_data"]
    assert part["mime_type"] == "audio/webm" and part["data"]


def test_falls_back_to_the_transcription_endpoint(backend):
    calls, replies = backend
    replies[":generateContent"] = httpx.Response(503, json={"error": "busy"})
    replies["/audio/transcriptions"] = httpx.Response(200, json={"text": "paid 20 pounds for lunch"})
    out = asyncio.run(speech.transcribe(OGG))
    assert out == {"text": "paid 20 pounds for lunch", "provider": "openai"}
    req = calls[-1]
    assert str(req.url) == "https://api.metisai.ir/openai/v1/audio/transcriptions"
    assert req.headers["authorization"] == "Bearer k-1"
    assert b'name="model"' in req.content and b"gpt-4o-mini-transcribe" in req.content
    assert b'filename="voice.ogg"' in req.content


def test_every_provider_failing_is_a_clear_error(backend):
    _calls, replies = backend
    replies[":generateContent"] = httpx.Response(200, json={"candidates": []})     # nothing heard
    replies["/audio/transcriptions"] = httpx.Response(500, json={})
    with pytest.raises(speech.SpeechError, match="type it"):
        asyncio.run(speech.transcribe(WEBM))


@pytest.mark.parametrize("data,msg", [(b"", "empty"), (b"%PDF-1.4", "not an audio"),
                                      (WEBM + b"\x00" * speech.MAX_BYTES, "10 MB")])
def test_bad_recordings_are_refused_before_any_call(backend, data, msg):
    calls, _ = backend
    with pytest.raises(speech.SpeechError, match=msg):
        asyncio.run(speech.transcribe(data))
    assert calls == []


def test_an_exhausted_budget_is_not_retried(monkeypatch):
    from app.services.ai_usage import AIBudgetExceeded
    monkeypatch.setattr(speech, "_gemini_ready", lambda: True)

    async def over(*_a):
        raise AIBudgetExceeded("company", 100, 100, 60)

    async def never(*_a):
        raise AssertionError("no fallback once the budget is used up")
    monkeypatch.setattr(speech, "_gemini", over)
    monkeypatch.setattr(speech, "_openai", never)
    with pytest.raises(AIBudgetExceeded):
        asyncio.run(speech.transcribe(WEBM))


def test_a_call_is_metered_as_speech(backend, db):
    from app.models.ai_usage import AIUsageEvent
    _calls, replies = backend
    replies[":generateContent"] = _gemini_reply("fifty thousand for bread")
    before = db.execute(select(AIUsageEvent).where(AIUsageEvent.purpose == "speech")).scalars().all()
    asyncio.run(speech.transcribe(WEBM))
    db.expire_all()
    after = db.execute(select(AIUsageEvent).where(AIUsageEvent.purpose == "speech")).scalars().all()
    assert len(after) == len(before) + 1 and after[-1].input_tokens == 40


# ─── The endpoint ──────────────────────────────────────────────────────────────

def _login(client, role):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=role == "owner", role=role)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_the_endpoint_returns_text_for_the_input(client, monkeypatch):
    async def fake(data):
        assert data == WEBM
        return {"text": "۵۰ هزار نان", "provider": "gemini"}
    monkeypatch.setattr(speech, "transcribe", fake)
    api = _login(client, "owner")
    r = api.post("/ai-accountant/transcribe", files={"file": ("voice-note", WEBM, "audio/webm")})
    assert r.status_code == 200 and r.json()["text"] == "۵۰ هزار نان"


def test_the_endpoint_says_why_it_could_not(client):
    api = _login(client, "owner")
    r = api.post("/ai-accountant/transcribe", files={"file": ("voice-note", b"not audio", "audio/webm")})
    assert r.status_code == 422 and "audio" in r.json()["detail"]


def test_viewers_cannot_use_the_microphone(client):
    api = _login(client, "viewer")
    assert api.post("/ai-accountant/transcribe", files={"file": ("v", WEBM, "audio/webm")}).status_code == 403

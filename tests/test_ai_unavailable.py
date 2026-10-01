"""When the AI provider can't be reached, the chat says so plainly in the
user's language instead of "[error] OpenAI-shape provider unreachable after
3 attempts: …" — the server marks that failure so the page can tell it apart."""
from __future__ import annotations

from app.services.ai_accountant.anthropic_client import AIAccountantError


def test_a_provider_failure_is_marked_for_the_page(auth_client, monkeypatch):
    from app.api import ai_accountant

    async def broken(*a, **k):
        raise AIAccountantError("OpenAI-shape provider unreachable after 3 attempts: connection refused")

    monkeypatch.setattr(ai_accountant, "run_chat_turn", broken)
    r = auth_client.post("/ai-accountant/chat", json={"message": "what is my cash?"})
    assert r.status_code == 502
    assert r.headers.get("x-error-code") == "ai_unavailable"
    assert "unreachable" in r.json()["detail"]                              # the technical reason is still there


def test_the_chat_says_it_plainly():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "15-ai-chat.js").read_text(encoding="utf-8")
    assert "'[error] '" not in js and "t('aiUnavailable')" in js and "X-Error-Code" in js

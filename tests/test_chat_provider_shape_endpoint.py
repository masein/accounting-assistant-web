"""Tests for /admin/chat-provider-shape.

Covers:

* default GET returns shape='' (auto) when the user hasn't picked one
* effective shape auto-detects: 'anthropic' when a key is configured,
  'openai' otherwise
* PUT persists explicit choices ('anthropic' / 'openai') and they
  override auto-detection
* PUT with '' clears the explicit choice (re-enables auto-detection)
* PUT with an unknown value 400s
* The orchestrator's resolve_chat_shape agrees with the admin endpoint
  (no drift between the two readers)
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import ai_runtime
from app.models.app_setting import AppSetting
from app.services.ai_accountant.orchestrator import _resolve_chat_shape


@pytest.fixture(autouse=True)
def _isolate(db: Session):
    """Reset the shape setting + Anthropic-key state around every test."""
    # Snapshot the current Anthropic API key so we can restore it.
    original_key = ai_runtime._state["anthropic"].get("api_key", "")
    yield
    db.execute(delete(AppSetting).where(AppSetting.key == "ai_chat_provider_shape"))
    db.commit()
    ai_runtime._state["anthropic"]["api_key"] = original_key


def _clear_anthropic_key():
    ai_runtime._state["anthropic"]["api_key"] = ""


def _set_anthropic_key(key: str = "sk-ant-test"):
    ai_runtime._state["anthropic"]["api_key"] = key


class TestGet:
    def test_default_when_no_key_is_openai(self, auth_client, db: Session) -> None:
        _clear_anthropic_key()
        r = auth_client.get("/admin/chat-provider-shape")
        assert r.status_code == 200
        body = r.json()
        assert body["shape"] == ""           # nothing explicitly persisted
        assert body["effective"] == "openai"  # auto-detect: no Anthropic key
        assert set(body["supported"]) == {"anthropic", "openai"}

    def test_default_with_anthropic_key_is_anthropic(self, auth_client, db: Session) -> None:
        _set_anthropic_key()
        r = auth_client.get("/admin/chat-provider-shape")
        assert r.json()["effective"] == "anthropic"


class TestPut:
    def test_put_openai_persists_and_overrides_autodetect(self, auth_client, db: Session) -> None:
        _set_anthropic_key()  # auto-detect would say anthropic
        r = auth_client.put("/admin/chat-provider-shape", json={"shape": "openai"})
        assert r.status_code == 200
        body = r.json()
        assert body["shape"] == "openai"
        assert body["effective"] == "openai"  # explicit choice wins
        # Verify it actually persisted.
        row = db.execute(
            select(AppSetting).where(AppSetting.key == "ai_chat_provider_shape")
        ).scalar_one()
        assert row.value == "openai"

    def test_put_anthropic_persists(self, auth_client, db: Session) -> None:
        _clear_anthropic_key()  # auto-detect would say openai
        r = auth_client.put("/admin/chat-provider-shape", json={"shape": "anthropic"})
        assert r.status_code == 200
        body = r.json()
        assert body["shape"] == "anthropic"
        assert body["effective"] == "anthropic"

    def test_put_empty_clears_explicit_choice(self, auth_client, db: Session) -> None:
        _set_anthropic_key()
        # First pin to openai.
        auth_client.put("/admin/chat-provider-shape", json={"shape": "openai"})
        # Then clear.
        r = auth_client.put("/admin/chat-provider-shape", json={"shape": ""})
        assert r.status_code == 200
        body = r.json()
        assert body["shape"] == ""
        assert body["effective"] == "anthropic"  # back to auto-detect

    def test_put_unknown_400s(self, auth_client) -> None:
        r = auth_client.put("/admin/chat-provider-shape", json={"shape": "gemini"})
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]


class TestNoDriftBetweenReaders:
    """The orchestrator and the admin endpoint MUST agree on the
    effective shape — they read from the same place but via different
    code paths, so this guards against drift."""

    def test_agrees_on_explicit_anthropic(self, auth_client, db: Session) -> None:
        _clear_anthropic_key()
        auth_client.put("/admin/chat-provider-shape", json={"shape": "anthropic"})
        assert _resolve_chat_shape(db) == "anthropic"
        assert auth_client.get("/admin/chat-provider-shape").json()["effective"] == "anthropic"

    def test_agrees_on_explicit_openai(self, auth_client, db: Session) -> None:
        _set_anthropic_key()
        auth_client.put("/admin/chat-provider-shape", json={"shape": "openai"})
        assert _resolve_chat_shape(db) == "openai"
        assert auth_client.get("/admin/chat-provider-shape").json()["effective"] == "openai"

    def test_agrees_on_autodetect_with_key(self, auth_client, db: Session) -> None:
        _set_anthropic_key()
        auth_client.put("/admin/chat-provider-shape", json={"shape": ""})
        assert _resolve_chat_shape(db) == "anthropic"
        assert auth_client.get("/admin/chat-provider-shape").json()["effective"] == "anthropic"

    def test_agrees_on_autodetect_without_key(self, auth_client, db: Session) -> None:
        _clear_anthropic_key()
        auth_client.put("/admin/chat-provider-shape", json={"shape": ""})
        assert _resolve_chat_shape(db) == "openai"
        assert auth_client.get("/admin/chat-provider-shape").json()["effective"] == "openai"

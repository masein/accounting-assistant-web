"""Tests for the /admin/anthropic-config endpoint.

Verifies the AI accountant's Claude credentials can be edited
independently of the OpenAI-compatible default provider, and that
clearing the base URL falls back to the Anthropic default.
"""
from __future__ import annotations

import pytest


class TestAnthropicConfigEndpoint:
    def test_get_returns_defaults_when_unset(self, auth_client) -> None:
        r = auth_client.get("/admin/anthropic-config")
        assert r.status_code == 200
        body = r.json()
        # The endpoint always returns the resolved base_url (defaulted)
        # so the UI has a non-empty value to render.
        assert body["base_url"] == "https://api.anthropic.com"
        assert body["model"] == "claude-opus-4-6"
        assert body["default_base_url"] == "https://api.anthropic.com"
        assert body["default_model"] == "claude-opus-4-6"
        assert isinstance(body["has_api_key"], bool)

    def test_patch_overrides_base_url_and_model(self, auth_client) -> None:
        r = auth_client.patch(
            "/admin/anthropic-config",
            json={"base_url": "https://example-proxy.test/v1", "model": "claude-haiku-4-5"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["base_url"] == "https://example-proxy.test/v1"
        assert body["model"] == "claude-haiku-4-5"

        # Round-trip via GET.
        r = auth_client.get("/admin/anthropic-config")
        assert r.json()["base_url"] == "https://example-proxy.test/v1"

    def test_blank_base_url_falls_back_to_default(self, auth_client) -> None:
        # First set a custom URL.
        auth_client.patch(
            "/admin/anthropic-config",
            json={"base_url": "https://example-proxy.test/v1"},
        )
        # Then clear it.
        r = auth_client.patch("/admin/anthropic-config", json={"base_url": ""})
        assert r.status_code == 200
        # resolve_anthropic_config returns the default when stored value is blank.
        assert r.json()["base_url"] == "https://api.anthropic.com"

    def test_api_key_dash_clears_it(self, auth_client) -> None:
        auth_client.patch("/admin/anthropic-config", json={"api_key": "sk-test-key"})
        r = auth_client.get("/admin/anthropic-config")
        assert r.json()["has_api_key"] is True

        auth_client.patch("/admin/anthropic-config", json={"api_key": "-"})
        r = auth_client.get("/admin/anthropic-config")
        assert r.json()["has_api_key"] is False

    def test_api_key_empty_keeps_existing(self, auth_client) -> None:
        auth_client.patch("/admin/anthropic-config", json={"api_key": "sk-keep-this"})
        # Empty string in the PATCH must NOT clear the key.
        auth_client.patch("/admin/anthropic-config", json={"api_key": ""})
        r = auth_client.get("/admin/anthropic-config")
        assert r.json()["has_api_key"] is True

    def test_updating_anthropic_does_not_change_active_provider(self, auth_client) -> None:
        """Critical: editing the AI accountant credentials must not flip
        the OpenAI-compatible default provider used by other AI features."""
        # Read the current default provider.
        before = auth_client.get("/admin/ai-config").json()["provider"]
        # Update only the Anthropic config.
        auth_client.patch(
            "/admin/anthropic-config",
            json={"base_url": "https://my-proxy.test/v1", "model": "claude-haiku-4-5"},
        )
        after = auth_client.get("/admin/ai-config").json()["provider"]
        assert after == before, (
            f"Anthropic update incorrectly changed default provider "
            f"from {before!r} to {after!r}"
        )

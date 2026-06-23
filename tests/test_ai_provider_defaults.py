"""Sane AI defaults + Anthropic base-URL normalization (the /v1/v1 404 fix)."""
from __future__ import annotations

import pytest

from app.services.ai_accountant.anthropic_client import normalize_anthropic_base_url


class TestAnthropicBaseUrlNormalization:
    @pytest.mark.parametrize("raw, expected", [
        # A Metis endpoint that already ends in /v1 must lose it, because the
        # SDK appends /v1/messages → otherwise /anthropic/v1/v1/messages (404).
        ("https://api.metisai.ir/anthropic/v1", "https://api.metisai.ir/anthropic"),
        ("https://api.metisai.ir/anthropic/v1/", "https://api.metisai.ir/anthropic"),
        ("https://api.metisai.ir/anthropic/V1", "https://api.metisai.ir/anthropic"),  # case-insensitive
        ("https://api.metisai.ir/anthropic/v1///", "https://api.metisai.ir/anthropic"),
        # Already-correct URLs are left alone.
        ("https://api.metisai.ir/anthropic", "https://api.metisai.ir/anthropic"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("https://api.anthropic.com/", "https://api.anthropic.com"),
        # Empty / None → None (SDK uses its own default).
        ("", None),
        ("   ", None),
        (None, None),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_anthropic_base_url(raw) == expected

    def test_only_trailing_v1_is_stripped(self):
        # A /v1 in the middle of the path must be preserved.
        assert normalize_anthropic_base_url("https://host/v1/anthropic") == "https://host/v1/anthropic"

    def test_resolves_to_single_v1_messages(self):
        # The contract: normalized base + the SDK's "/v1/messages" = one /v1.
        base = normalize_anthropic_base_url("https://api.metisai.ir/anthropic/v1")
        path = base + "/v1/messages"
        assert path == "https://api.metisai.ir/anthropic/v1/messages"
        assert "/v1/v1/" not in path


class TestSaneDefaults:
    """Assert the SHIPPED code defaults (pydantic field defaults), independent of
    any local .env override that this dev instance happens to set."""

    def test_default_provider_is_metis_not_lmstudio(self):
        """Fresh deploy should use the hosted Metis provider, not a local server."""
        from app.core.config import Settings
        assert Settings.model_fields["ai_provider"].default == "metis"

    def test_default_anthropic_model_is_not_4_7(self):
        from app.core.config import Settings
        default = Settings.model_fields["anthropic_model"].default
        assert default != "claude-opus-4-7"
        assert default == "claude-opus-4-6"

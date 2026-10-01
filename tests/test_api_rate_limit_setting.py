"""The API's per-user budget is a setting: 120 a minute by default, raised
only where a browser suite loads page after page as one user."""
from __future__ import annotations

from app.core.config import Settings


def test_the_default_is_120_a_minute(monkeypatch):
    monkeypatch.delenv("API_RATE_LIMIT_PER_MINUTE", raising=False)
    assert Settings().api_rate_limit_per_minute == 120


def test_the_environment_sets_it(monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "1000")
    assert Settings().api_rate_limit_per_minute == 1000


def test_the_limiter_uses_it():
    from app.core.config import settings
    from app.main import _api_limiter

    assert _api_limiter.max_requests == max(1, settings.api_rate_limit_per_minute)
    assert _api_limiter.window_seconds == 60

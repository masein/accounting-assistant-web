"""Rate limits say what they are (QA 2026-09-24 follow-up): a user over the
per-minute API limit got "You do not have permission to view users" on the
users panel. The 429 now carries ``code: rate_limited`` and ``Retry-After``,
the app shows a "too many requests — wait N seconds" notice, and only a 403
reads as "no permission"."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.rate_limit import RateLimiter

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def test_retry_after_is_when_the_oldest_hit_leaves_the_window(monkeypatch):
    import app.core.rate_limit as rl
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock[0])
    lim = RateLimiter(max_requests=2, window_seconds=60)
    assert lim.retry_after("u") == 0
    assert lim.is_allowed("u")
    clock[0] += 10
    assert lim.is_allowed("u") and not lim.is_allowed("u")
    assert lim.retry_after("u") == 50                    # first hit at 1000 leaves at 1060; now 1010
    clock[0] += 49.5
    assert lim.retry_after("u") == 1
    clock[0] += 0.5
    assert lim.retry_after("u") == 0 and lim.is_allowed("u")


@pytest.fixture()
def tight_limit(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_api_limiter", RateLimiter(max_requests=2, window_seconds=60))


def test_the_api_limit_answers_with_a_code_and_when_to_retry(auth_client, tight_limit):
    for _ in range(2):
        assert auth_client.get("/fx/metadata").status_code == 200
    r = auth_client.get("/fx/metadata")
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == "rate_limited" and body["retry_after"] >= 1
    assert r.headers["Retry-After"] == str(body["retry_after"])
    assert "try again in" in body["detail"]


def test_the_app_tells_a_limit_from_a_refusal():
    core = (JS / "01-core.js").read_text(encoding="utf-8")
    assert "res.status === 429" in core and "rateLimitedNotice" in core
    admin = (JS / "04-admin-settings.js").read_text(encoding="utf-8")
    users = admin[admin.index("async function loadUsers"):]
    users = users[: users.index("\n    }\n")]
    assert re.search(r"res\.status === 403 \? 'usersNoPermission'", users)
    assert "rateLimitedShort" in users

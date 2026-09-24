"""Security review 2026-09-24 M2: login limits were per username only (one
address could spray a password across every account, and five bad tries by
anyone locked the real owner out for 15 minutes), unknown usernames were
faster to reject than wrong passwords, and X-Forwarded-For was trusted from
anyone."""
from __future__ import annotations

import uuid

import pytest

from app.api import auth as auth_mod
from app.core.auth import hash_password
from app.core.config import settings
from app.core.rate_limit import RateLimiter
from app.models.user import User

PW = "Regular#Pass2026"


def _user(db) -> User:
    h, s = hash_password(PW)
    u = User(username=f"bf-{uuid.uuid4().hex[:8]}", password_hash=h, password_salt=s, is_admin=True, role="owner", is_active=True)
    db.add(u); db.commit()
    return u


def _login(client, username, password, **headers):
    client.cookies.clear()
    return client.post("/auth/login", json={"username": username, "password": password}, headers=headers or None)


def test_five_failures_lock_that_username_only(client, db):
    a, b = _user(db), _user(db)
    for _ in range(5):
        assert _login(client, a.username, "wrong").status_code == 401
    assert _login(client, a.username, PW).status_code == 429  # even the right password waits now
    assert _login(client, b.username, PW).status_code == 200  # another account from the same IP is fine


def test_successful_logins_never_count_and_clear_the_failures(client, db):
    u = _user(db)
    for _ in range(7):
        assert _login(client, u.username, PW).status_code == 200
    for _ in range(4):
        assert _login(client, u.username, "wrong").status_code == 401
    assert _login(client, u.username, PW).status_code == 200  # 4 failures < 5: allowed, and it resets
    for _ in range(4):
        assert _login(client, u.username, "wrong").status_code == 401
    assert _login(client, u.username, PW).status_code == 200  # still only 4 since the reset


def test_password_spraying_from_one_ip_is_stopped(client, db, monkeypatch):
    victim = _user(db)
    for i in range(30):  # 30 different usernames, one wrong password each
        assert _login(client, f"ghost-{i}-{uuid.uuid4().hex[:4]}", "Spring2026!").status_code == 401
    assert _login(client, victim.username, PW).status_code == 429  # the IP is exhausted, whoever it claims to be
    # a different client address is not affected (proxy headers trusted for the check)
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    assert _login(client, victim.username, PW, **{"X-Forwarded-For": "203.0.113.9"}).status_code == 200


def test_unknown_username_costs_a_password_verification(client, db, monkeypatch):
    calls = []
    real = auth_mod.verify_password
    monkeypatch.setattr(auth_mod, "verify_password", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _login(client, f"nobody-{uuid.uuid4().hex[:6]}", "whatever").status_code == 401
    assert len(calls) == 1  # the dummy hash was verified
    u = _user(db)
    calls.clear()
    assert _login(client, u.username, "wrong").status_code == 401
    assert len(calls) == 1  # same amount of work for a real user


def test_forwarded_for_is_ignored_unless_trusted(client, db, monkeypatch):
    from app.core.audit import get_client_ip
    from fastapi import Request

    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"198.51.100.7, 10.0.0.1")], "client": ("127.0.0.1", 1), "method": "GET", "path": "/", "query_string": b"", "scheme": "http", "server": ("t", 80)}
    req = Request(scope)
    assert get_client_ip(req) == "127.0.0.1"
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    assert get_client_ip(req) == "198.51.100.7"


def test_rate_limiter_prunes_idle_keys():
    lim = RateLimiter(max_requests=3, window_seconds=0)  # everything expires at once
    for i in range(1200):
        lim.hit(f"k{i}")
    assert len(lim._hits) < 600  # idle keys were dropped along the way
    lim2 = RateLimiter(max_requests=2, window_seconds=60)
    assert lim2.would_allow("x") and lim2.would_allow("x")  # checking records nothing
    lim2.hit("x"); lim2.hit("x")
    assert not lim2.would_allow("x")
    lim2.reset("x")
    assert lim2.would_allow("x")

"""Audit actor attribution — the acting user + role are stamped on writes."""
from __future__ import annotations

import uuid

import pytest

from app.core.audit import audit_log
from app.core.auth import SessionUser
from app.core.request_context import clear_current_user, set_current_user
from app.services.audit_service import log_audit_event


@pytest.fixture()
def actor():
    u = SessionUser(user_id="u-123", username="alice", is_admin=False,
                    company_id="c1", role="accountant")
    set_current_user(u)
    yield u
    clear_current_user()


def test_audit_log_defaults_actor_from_context(db, actor):
    entry = audit_log(db, action="create", entity_type="transaction", entity_id="t1")
    assert entry.user_id == "u-123"
    assert entry.username == "alice"
    assert entry.actor_role == "accountant"


def test_log_audit_event_defaults_actor_from_context(db, actor):
    entry = log_audit_event(db, "update", "invoice", entity_id="i1")
    assert entry.user_id == "u-123"
    assert entry.username == "alice"
    assert entry.actor_role == "accountant"


def test_explicit_actor_overrides_context(db, actor):
    entry = audit_log(db, action="approve", entity_type="mileage_claim",
                      entity_id="m1", user_id="u-999", username="bob", role="manager")
    assert entry.user_id == "u-999"
    assert entry.username == "bob"
    assert entry.actor_role == "manager"


def test_no_actor_when_context_empty(db):
    clear_current_user()
    entry = audit_log(db, action="login", entity_type="user", entity_id="x")
    assert entry.user_id is None and entry.username is None and entry.actor_role is None


def test_decider_falls_back_to_current_user(actor):
    from app.api.expenses import _decider
    assert _decider(None) == "alice"          # from context
    assert _decider("explicit") == "explicit"  # explicit wins
    clear_current_user()
    assert _decider(None) == "admin"           # safe default when unauthenticated

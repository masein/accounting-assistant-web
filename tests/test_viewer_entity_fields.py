"""Roles without bank:read (viewer, manager, employee) must not receive bank
account numbers, IBANs or national ids on entity reads (QA 2026-09-24)."""
from __future__ import annotations

import uuid

import pytest

from app.models.entity import Entity

SENSITIVE = ("account_number", "iban", "sort_code", "account_holder", "national_id")


def _client_as(client, role: str):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    token = create_session_token(user_id=str(uuid.uuid4()), username=f"{role}-user", is_admin=(role == "owner"), role=role)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def bank_entity(db):
    e = Entity(type="bank", name=f"Strip Bank {uuid.uuid4().hex[:5]}", bank_name="Mellat",
               account_number="1234567890", iban="IR000000000000000000000001", sort_code="12-34",
               account_holder="QA Holder", national_id="0012345678", phone="021000")
    db.add(e)
    db.commit()
    return e


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_roles_without_bank_read_get_bank_fields_stripped(client, db, bank_entity, role):
    c = _client_as(client, role)
    if role == "manager":
        # managers cannot list entities at all — that stays 403
        assert c.get("/entities").status_code == 403
        return
    rows = c.get("/entities").json()
    mine = next(r for r in rows if r["id"] == str(bank_entity.id))
    assert all(mine[f] is None for f in SENSITIVE)
    assert mine["name"] == bank_entity.name and mine["bank_name"] == "Mellat" and mine["phone"] == "021000"
    one = c.get(f"/entities/{bank_entity.id}").json()
    assert all(one[f] is None for f in SENSITIVE)


@pytest.mark.parametrize("role", ["owner", "cfo", "accountant"])
def test_roles_with_bank_read_see_everything(client, db, bank_entity, role):
    c = _client_as(client, role)
    one = c.get(f"/entities/{bank_entity.id}").json()
    assert one["account_number"] == "1234567890" and one["iban"] == "IR000000000000000000000001"

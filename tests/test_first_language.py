"""A new company's owner starts in the company's language: an Iranian
company's first sign-in landed in English (deep browser test, 2026-10-02,
finding #2). A self-serve sign-up keeps the language it signed up in."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.user import User


@pytest.mark.parametrize("locale, lang", [("ir", "fa"), ("uk", "en"), ("default", "en")])
def test_a_new_companys_owner_speaks_the_companys_language(superadmin_client, db, locale, lang):
    from tests.test_admin_audit import _purge_company
    username = f"first-{uuid.uuid4().hex[:6]}"
    r = superadmin_client.post("/admin/companies", json={"name": f"First {uuid.uuid4().hex[:4]}", "locale": locale,
                                                          "username": username, "password": "Strong#Pass2026"})
    assert r.status_code == 201, r.text
    try:
        owner = db.execute(select(User).where(User.username == username)).scalars().one()
        assert owner.preferred_language == lang
    finally:
        _purge_company(db, r.json()["id"])

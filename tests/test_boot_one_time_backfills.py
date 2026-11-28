"""A restart changes nobody (review 2026-11-28, scenario CV3).

``ensure_default_company`` runs on every boot. It used to make ``admin`` a
super-admin in the Default company each time (taking the flag away never
stuck) and pull every company-less login into Default, a platform super-admin
included; ``seed_admin_user_if_missing`` re-created a renamed ``admin`` with the
initial password. These run the real boot steps on a private database."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.db.seed import DEFAULT_COMPANY_ID, ensure_default_company, seed_admin_user_if_missing
from app.models.company import Company
from app.models.user import User


@pytest.fixture()
def boot(monkeypatch):
    """A private database with just companies and users; the tenant-row
    backfill is pinned by test_boot_platform_settings, so it gets no tables."""
    import app.db.tenant as tenant
    monkeypatch.setattr(tenant, "tenant_model_tablenames", lambda: set())
    eng = create_engine("sqlite://")
    Company.__table__.create(eng)
    User.__table__.create(eng)

    def _boot():
        with Session(bind=eng) as s:
            seed_admin_user_if_missing(s)
        ensure_default_company(eng)

    yield eng, _boot
    eng.dispose()


def _users(eng) -> dict:
    with eng.connect() as c:
        return {r.username: (r.company_id, bool(r.is_superadmin))
                for r in c.execute(text("SELECT username, company_id, is_superadmin FROM users"))}


def _add_user(eng, username, *, superadmin=False, company=None):
    ph, salt = hash_password("x" * 12)
    with Session(bind=eng) as s:
        s.add(User(username=username, password_hash=ph, password_salt=salt, is_admin=True,
                   is_superadmin=superadmin, is_active=True))
        s.commit()
    if company:
        with eng.begin() as c:
            c.execute(text("UPDATE users SET company_id = :c WHERE username = :u"), {"c": company, "u": username})


def _sql(eng, sql, **params):
    with eng.begin() as c:
        c.execute(text(sql), params)


def test_cv3_a_fresh_install_makes_admin_the_super_admin_in_default(boot):
    """CV3: first boot creates Default and the admin super-admin in it."""
    eng, run = boot
    run()
    assert _users(eng) == {"admin": (DEFAULT_COMPANY_ID, True)}
    with eng.connect() as c:
        assert c.execute(text("SELECT locale, base_currency FROM companies")).one() == ("uk", "GBP")
    run()                                                   # a second boot: the same
    assert _users(eng) == {"admin": (DEFAULT_COMPANY_ID, True)}


def test_cv3_a_revoked_admin_and_a_company_less_super_admin_stay_as_set(boot):
    """CV3: the flag taken from admin stays off; a platform super-admin with no
    company is not pulled into Default."""
    eng, run = boot
    run()
    _add_user(eng, "root", superadmin=True)                 # platform-wide, no company
    _sql(eng, "UPDATE users SET is_superadmin = false WHERE username = 'admin'")
    run()
    run()
    assert _users(eng) == {"admin": (DEFAULT_COMPANY_ID, False), "root": (None, True)}


def test_cv3_a_renamed_admin_is_not_created_again(boot):
    """CV3: with a super-admin on the platform, no fresh admin with the initial
    password appears on the next boot."""
    eng, run = boot
    run()
    _sql(eng, "UPDATE users SET username = 'platform-owner' WHERE username = 'admin'")
    run()
    assert _users(eng) == {"platform-owner": (DEFAULT_COMPANY_ID, True)}


def test_cv3_no_super_admin_at_all_gets_admin_back(boot):
    """CV3: a platform locked out of its console (no super-admin) promotes
    admin again, keeping its company."""
    eng, run = boot
    run()
    other = "11111111-1111-1111-1111-111111111111"
    _sql(eng, "UPDATE users SET is_superadmin = false, company_id = :c WHERE username = 'admin'", c=other)
    run()
    assert _users(eng) == {"admin": (other, True)}


def test_cv3_a_company_less_login_that_is_not_a_super_admin_joins_default(boot):
    """CV3: with no company its requests would run unscoped and read every
    company's books, so that one repair still runs on every boot."""
    eng, run = boot
    run()
    _add_user(eng, "stray")
    run()
    assert _users(eng)["stray"] == (DEFAULT_COMPANY_ID, False)

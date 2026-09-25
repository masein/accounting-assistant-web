"""Per-update onboarding: the what's-new registry, the /auth/me payload, the
seen marker, and new users starting on the current release."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core import release_notes as rn
from app.models.user import User

INDEX = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _pages() -> set[str]:
    return set(re.findall(r'data-page="([a-z\-]+)"', INDEX.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------

def test_current_release_is_the_newest_entry():
    versions = [r.version for r in rn.RELEASES]
    assert rn.CURRENT_RELEASE in versions
    assert max(versions, key=rn.version_tuple) == rn.CURRENT_RELEASE
    assert len(set(versions)) == len(versions)


def test_every_highlight_is_fully_translated_and_points_at_a_real_page():
    pages = _pages()
    for rel in rn.RELEASES:
        assert rel.highlights, rel.version
        keys = [h.key for h in rel.highlights]
        assert len(set(keys)) == len(keys), "duplicate highlight key"
        for h in rel.highlights:
            assert set(h.title) == set(rn.LANGUAGES), (rel.version, h.key, "title")
            assert set(h.body) == set(rn.LANGUAGES), (rel.version, h.key, "body")
            assert all(v.strip() for v in h.title.values()) and all(v.strip() for v in h.body.values())
            for page in [h.page, *h.page_by_role.values()]:
                if page is not None:
                    assert page in pages, (h.key, page)


def test_version_tuple_orders_dates_and_suffixes():
    assert rn.version_tuple("2026.09.22") < rn.version_tuple("2026.09.22.1") < rn.version_tuple("2026.10.01")
    assert rn.version_tuple(None) == ()


def test_locale_limited_highlights():
    ir = {h["key"] for r in rn.whats_new_for("owner", None, locale="ir")["releases"] for h in r["highlights"]}
    uk = rn.whats_new_for("owner", None, locale="uk")
    assert "moadian-export" in ir
    assert all(h["key"] != "moadian-export" for r in uk["releases"] for h in r["highlights"])
    assert uk["seen"] is True        # nothing else in this release for a UK company
    # Unknown locale (no company in the session) keeps every highlight.
    assert "moadian-export" in {h["key"] for r in rn.whats_new_for("owner", None)["releases"] for h in r["highlights"]}


def test_whats_new_for_role_and_last_seen():
    # Existing user (never seen anything) → exactly the current release.
    out = rn.whats_new_for("owner", None)
    assert out["seen"] is False
    assert [r["version"] for r in out["releases"]] == [rn.CURRENT_RELEASE]
    keys = {h["key"] for h in out["releases"][0]["highlights"]}
    assert {"moadian-export"} <= keys
    # Earlier releases stay in the full history.
    history = rn.whats_new_for("owner", None, include_all=True)
    all_keys = {h["key"] for r in history["releases"] for h in r["highlights"]}
    assert {"chat-statement", "insights", "whats-new",
            "per-currency-views", "chat-periods-cash", "balance-sheet-check",
            "payroll-statutory-rules", "ai-invoices-cheques", "quotes", "invoice-email-reminders",
            "recurring-invoices"} <= all_keys

    # Up to date → nothing.
    assert rn.whats_new_for("owner", rn.CURRENT_RELEASE)["seen"] is True
    assert rn.whats_new_for("owner", rn.CURRENT_RELEASE)["releases"] == []
    # A future last_seen (rolled back deploy) is also "seen".
    assert rn.whats_new_for("owner", "2099.01.01")["releases"] == []

    # Role filtering on the 2026.09.22 release (from the full history): a
    # personal user doesn't get the SME-only entity note and their insights
    # step opens their own dashboard.
    def _rel(role, version):
        rels = rn.whats_new_for(role, None, include_all=True)["releases"]
        return {h["key"]: h for r in rels if r["version"] == version for h in r["highlights"]}

    pkeys = _rel("personal", "2026.09.22")
    assert "entity-statement" not in pkeys
    assert pkeys["insights"]["page"] == "personal-dashboard"
    assert _rel("owner", "2026.09.22")["insights"]["page"] == "dashboard"
    # …and on the current release: SME-only notes are hidden from personal users.
    # The current release is SME-only (مودیان export): a personal user gets nothing new.
    assert rn.whats_new_for("personal", None)["releases"] == []
    p25 = set(_rel("personal", "2026.09.25"))
    assert {"ai-invoices-cheques"} <= p25
    assert "payroll-statutory-rules" not in p25
    p24 = set(_rel("personal", "2026.09.24"))
    assert {"per-currency-views", "chat-periods-cash"} <= p24
    assert not ({"stricter-checks", "balance-sheet-check", "invoice-credit"} & p24)

    # An employee only gets the notes meant for everyone.
    emp = rn.whats_new_for("employee", None, include_all=True)
    assert {h["key"] for r in emp["releases"] for h in r["highlights"]} == {"whats-new"}

    # include_all lists history regardless of what was seen.
    everything = rn.whats_new_for("owner", rn.CURRENT_RELEASE, include_all=True)
    assert everything["releases"] and everything["seen"] is True


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _make_user(db, **kw) -> User:
    from app.core.auth import hash_password
    h, salt = hash_password("Secret#12345")
    u = User(username=f"wn-{uuid.uuid4().hex[:8]}", password_hash=h, password_salt=salt,
             is_admin=True, role="owner", is_active=True, **kw)
    db.add(u)
    db.commit()
    return u


def _client_for(client, user: User):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient

    token = create_session_token(user_id=str(user.id), username=user.username, is_admin=True)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_me_shows_the_tour_once_then_marks_it_seen(client, db):
    user = _make_user(db)   # last_seen_release NULL = existing user
    c = _client_for(client, user)

    me = c.get("/auth/me").json()
    assert me["whats_new"]["seen"] is False
    assert me["whats_new"]["releases"][0]["version"] == rn.CURRENT_RELEASE
    assert me["whats_new"]["releases"][0]["highlights"][0]["title"]["fa"]

    r = c.post("/auth/whats-new/seen")
    assert r.status_code == 200 and r.json()["last_seen_release"] == rn.CURRENT_RELEASE
    db.refresh(user)
    assert user.last_seen_release == rn.CURRENT_RELEASE

    me2 = c.get("/auth/me").json()
    assert me2["whats_new"]["seen"] is True and me2["whats_new"]["releases"] == []

    # The Settings button still gets the full history.
    allr = c.get("/auth/whats-new").json()
    assert allr["releases"] and allr["seen"] is True


def test_user_who_saw_an_older_release_gets_only_the_newer_ones(client, db):
    user = _make_user(db, last_seen_release="2026.01.01")
    c = _client_for(client, user)
    me = c.get("/auth/me").json()
    assert me["whats_new"]["seen"] is False
    assert all(rn.version_tuple(r["version"]) > rn.version_tuple("2026.01.01") for r in me["whats_new"]["releases"])


def test_new_users_start_on_the_current_release(db):
    """provision_company and the admin user-create both stamp the current
    release, so a first login never opens with a changelog."""
    import inspect
    from app.api import admin
    from app.services import company_service

    assert "last_seen_release=CURRENT_RELEASE" in inspect.getsource(company_service.provision_company)
    src = inspect.getsource(admin)
    assert "last_seen_release=CURRENT_RELEASE" in src

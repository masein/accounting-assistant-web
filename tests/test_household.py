"""A shared household: inviting someone onto one set of personal books (roadmap §4.12)."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.tenant import tenant_bypass

STRONG = "Str0ng-household-pass!"


@pytest.fixture()
def home(client, db, monkeypatch):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.services.company_service import provision_company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company
    monkeypatch.setattr(settings, "allow_self_signup", False)             # an invitation works without it
    from app.api import auth as auth_api
    monkeypatch.setattr(auth_api._signup_limiter, "is_allowed", lambda *a, **k: True)   # (its own test below)
    company, user = provision_company(db, name="Our Money", locale="ir", base_currency="IRR",
                                      username=f"sara-{uuid.uuid4().hex[:6]}", password=STRONG, kind="personal")
    db.commit()
    cid, uid, uname = str(company.id), str(user.id), user.username
    csrf = generate_csrf_token()

    def as_user(user_id=uid, username=uname):
        client.cookies.clear()
        client.cookies.set(settings.auth_cookie_name, create_session_token(
            user_id=user_id, username=username, is_admin=False, role="personal", company_id=cid))
        client.cookies.set(CSRF_COOKIE, csrf)
        return _CSRFTestClient(client, csrf)

    db.expunge_all()
    yield {"api": as_user(), "as_user": as_user, "cid": cid, "uid": uid, "uname": uname, "client": client,
           "csrf": csrf}
    client.cookies.clear()
    _purge_company(db, cid)


def _invite(api, **body):
    r = api.post("/personal/household/invites", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _join(h, token, username=None, **extra):
    from tests.conftest import _CSRFTestClient
    c = h["client"]
    c.cookies.clear()
    from app.core.auth import CSRF_COOKIE
    c.cookies.set(CSRF_COOKIE, h["csrf"])
    r = c.post("/auth/signup", json={"invite": token, "username": username or f"reza-{uuid.uuid4().hex[:6]}",
                                     "password": STRONG, **extra})
    return r, _CSRFTestClient(c, h["csrf"])


def _user(db, username):
    from app.models.user import User
    with tenant_bypass():
        db.expire_all()
        return db.execute(select(User).where(User.username == username)).scalar_one()


def test_an_invitation_joins_someone_to_the_same_books(home, db):
    api = home["api"]
    out = api.get("/personal/household").json()
    assert [(m["username"], m["you"]) for m in out["members"]] == [(home["uname"], True)]
    assert out["invites"] == [] and (out["limit"], out["room"]) == (6, 5)

    inv = _invite(api, name="Reza")
    assert inv["emailed"] is False and inv["link"].endswith(f"/login?invite={inv['token']}")
    from app.models.household_invite import HouseholdInvite
    with tenant_bypass():
        row = db.execute(select(HouseholdInvite)).scalars().one()
    assert row.token_hash == hashlib.sha256(inv["token"].encode()).hexdigest() and inv["token"] not in row.token_hash
    assert api.get("/personal/household").json()["invites"][0]["name"] == "Reza"

    # a goal Sara set up, before Reza joins
    assert api.post("/personal/goals", json={"name": "Holiday", "account_code": "1110", "target_amount": 1}).status_code == 201

    home["client"].cookies.clear()                                           # the invitee has no session yet
    info = home["client"].get(f"/auth/invite/{inv['token']}")
    assert info.status_code == 200, info.text
    assert (info.json()["books"], info.json()["invited_by"], info.json()["name"]) == ("Our Money", home["uname"], "Reza")

    r, reza = _join(home, inv["token"], username="reza-home")
    assert r.status_code == 201, r.text
    assert r.json()["joined"] is True and r.json()["user"]["role"] == "personal"
    u = _user(db, "reza-home")
    assert (str(u.company_id), u.role, u.is_admin, u.is_active) == (home["cid"], "personal", False, True)
    assert [g["name"] for g in reza.get("/personal/goals").json()] == ["Holiday"]      # the same books
    members = reza.get("/personal/household").json()["members"]
    assert sorted(m["username"] for m in members) == sorted([home["uname"], "reza-home"])
    assert reza.get("/personal/household").json()["invites"] == []                   # used up

    again, _ = _join(home, inv["token"])
    assert again.status_code == 410 and "already been used" in again.json()["detail"]


def test_what_an_invitation_refuses(home, db):
    from app.models.household_invite import HouseholdInvite
    api = home["api"]
    assert home["client"].get("/auth/invite/not-a-token").status_code == 404
    r, _ = _join(home, "not-a-token")
    assert r.status_code == 404
    api = home["as_user"]()
    late = _invite(api, name="Late")
    with tenant_bypass():
        db.execute(select(HouseholdInvite).where(HouseholdInvite.name == "Late")).scalar_one().expires_at = \
            datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    r, _ = _join(home, late["token"])
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    api = home["as_user"]()
    gone = _invite(api, name="Gone")
    assert api.delete(f"/personal/household/invites/{gone['id']}").status_code == 204
    assert api.delete(f"/personal/household/invites/{gone['id']}").status_code == 204          # revoking twice is harmless
    r, _ = _join(home, gone["token"])
    assert r.status_code == 404
    api = home["as_user"]()
    ok = _invite(api)
    r, _ = _join(home, ok["token"], password="short")
    assert r.status_code in (400, 422)
    r, _ = _join(home, ok["token"], username=home["uname"])                                   # taken
    assert r.status_code == 400 and "taken" in r.json()["detail"]
    api = home["as_user"]()
    assert api.post("/personal/household/invites", json={"email": "not-an-address"}).status_code == 422


def test_a_household_has_room_for_six(home):
    api = home["api"]
    for _ in range(5):
        _invite(api)
    r = api.post("/personal/household/invites", json={})
    assert r.status_code == 409 and "room for 6" in r.json()["detail"]
    assert api.get("/personal/household").json()["room"] == 0


def test_members_remove_each_other_not_themselves(home, db):
    api = home["api"]
    inv = _invite(api)
    r, reza = _join(home, inv["token"], username="reza-leaves")
    assert r.status_code == 201
    reza_id = str(_user(db, "reza-leaves").id)
    assert reza.delete(f"/personal/household/members/{reza_id}").status_code == 400          # not yourself
    sara = home["as_user"]()
    assert sara.delete(f"/personal/household/members/{reza_id}").status_code == 204
    u = _user(db, "reza-leaves")
    assert u.is_active is False and u.token_version >= 1                                   # sessions end
    assert sara.delete(f"/personal/household/members/{uuid.uuid4()}").status_code == 404
    assert [m["username"] for m in sara.get("/personal/household").json()["members"] if m["active"]] == [home["uname"]]


def test_by_email_the_link_proves_the_address(home, db, monkeypatch):
    from app.services import mail_service
    from tests.test_invoice_mail import _FakeSMTP
    sent: list = []
    from app.services import email_verification as ev
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(ev, "mail_configured", lambda: True)                # verification is required now
    monkeypatch.setattr(ev, "send_email", lambda **kw: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP(sent))
    api = home["api"]
    inv = _invite(api, name="Reza", email="reza@home.example")
    assert inv["emailed"] is True and sent[0]["To"] == "reza@home.example"
    assert inv["token"] in sent[0].get_body(preferencelist=("plain",)).get_content()
    r, _ = _join(home, inv["token"], username="reza-mail", email="reza@home.example")
    assert r.status_code == 201 and r.json().get("joined") is True                         # signed straight in
    assert _user(db, "reza-mail").email_verified_at is not None
    # a copied link, with mail on: an address is needed, and it has to be confirmed
    api = home["as_user"]()
    copied = _invite(api)
    r, _ = _join(home, copied["token"])
    assert r.status_code == 400 and "email" in r.json()["detail"].lower()
    r, _ = _join(home, copied["token"], username="reza-confirm", email="someone@home.example")
    assert r.status_code == 201 and r.json()["pending_verification"] is True
    assert _user(db, "reza-confirm").email_verified_at is None


def test_invitation_lookups_are_rate_limited(client):
    client.cookies.clear()
    codes = [client.get("/auth/invite/guess-" + str(i)).status_code for i in range(7)]
    assert codes[:5] == [404] * 5 and codes[-1] == 429                  # no guessing tokens by the thousand


def test_only_personal_books_have_a_household(client, db):
    from tests.test_statement_export import _company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        assert api.get("/personal/household").status_code == 422
        assert api.post("/personal/household/invites", json={}).status_code == 422
    finally:
        from tests.test_admin_audit import _purge_company
        client.cookies.clear()
        _purge_company(db, cid)


def test_who_may_manage_a_household():
    from app.core.auth import SessionUser
    from app.core.permissions import user_can_access
    for role, ok in (("personal", True), ("employee", False), ("viewer", False), ("manager", False)):
        u = SessionUser(user_id="u", username="u", role=role, is_admin=False)
        assert user_can_access(u, "POST", "/personal/household/invites") is ok, role
        assert user_can_access(u, "DELETE", "/personal/household/members/{user_id}") is ok, role


def test_the_pages_are_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    for el in ('id="pd-household"', 'id="hh-members"', 'id="hh-invites"', 'id="hh-send"', 'id="hh-link-input"'):
        assert el in html, el
    js = open("app/static/js/05-reports-manager.js", encoding="utf-8").read()
    assert "loadHousehold();" in js and "'/personal/household/invites'" in js
    login = open("app/static/login.html", encoding="utf-8").read()
    for el in ('id="join-form"', 'id="join-username"', 'id="join-password"', 'id="join-email"'):
        assert el in login, el
    ljs = open("app/static/login.js", encoding="utf-8").read()
    assert "'/auth/invite/'" in ljs and "invite: invite.token" in ljs
    for k in ("joinTitle", "joinSub", "joinSubNoWho", "joinEmail", "joinBtn", "joining", "joinPending", "joinInvalid"):
        assert ljs.count(f"{k}:") == 4, k
    text = i18n_text()
    for k in ("hhTitle", "hhHint", "hhInvite", "hhName", "hhEmail", "hhSend", "hhCopy", "hhCopied", "hhYou", "hhRemove",
              "hhRemoveConfirm", "hhCancel", "hhSomeone", "hhInvitedEmailed", "hhInvitedLink", "hhLinkEmailed",
              "hhLinkCopy", "hhFailed"):
        assert text.count(f"{k}:") == 4, k


def test_an_invitation_is_written_in_the_inviters_language_and_escaped(home, db, monkeypatch):
    from app.models.user import User
    from app.services import mail_service
    from tests.test_invoice_mail import _FakeSMTP
    sent: list = []
    monkeypatch.setattr(mail_service, "mail_configured", lambda: True)
    monkeypatch.setattr(mail_service, "_connect", lambda: _FakeSMTP(sent))
    with tenant_bypass():
        db.get(User, uuid.UUID(home["uid"])).preferred_language = "fa"
        db.commit()
    inv = _invite(home["api"], name="<b>Reza</b>", email="reza@home.example")
    assert inv["emailed"] is True
    msg = sent[0]
    assert "شما را به دفاتر خود دعوت کرده است" in msg["Subject"]
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "&lt;b&gt;Reza&lt;/b&gt;" in html and "<b>Reza" not in html and "dir='rtl'" in html
    assert "«Our Money»" in msg.get_body(preferencelist=("plain",)).get_content()
    # whoever joins keeps the language they signed up in
    r2 = home["client"].post("/auth/signup", json={"invite": _invite(home["as_user"]())["token"], "username": "ana-es",
                                                   "password": STRONG}, headers={"X-UI-Language": "es"})
    assert r2.status_code == 201 and _user(db, "ana-es").preferred_language == "es"

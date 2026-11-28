"""Support (the owner, 2026-11-28, scenarios DA1–DA8): every signed-in person can write to the support desk from
the web page or a phone; the server only keeps the conversation; the desk — Claude in Nightshift's support desk on
the owner's Mac — fetches the waiting messages with its desk key, answers, and the person sees the reply in the
panel, as a badge and in the bell, until they read it. No AI and no credentials for it on the server."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.support import SupportMessage, SupportThread
from app.services import support_desk as S
from tests.test_ai_guardrails import _login, co  # noqa: F401  (fixture)

API = "/api/mobile/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


@pytest.fixture()
def desk(db):
    """A desk key, as the platform admin makes it; forgotten afterwards."""
    key = S.new_desk_key(db)
    db.commit()
    yield {"Authorization": f"Bearer {key}"}
    S.turn_off(db)
    db.commit()


def _bell(api) -> list[dict]:
    """The support lines in a person's bell."""
    feed = api.get("/notifications/feed").json()
    rows = feed if isinstance(feed, list) else feed.get("items", [])
    return [n for n in rows if n.get("kind") == "support"]


def _write(client, co, text="The trial balance page is empty", user="owner", **extra):
    api = _login(client, co["cid"], co[user])
    return api.post("/support/messages", json={"text": text, **extra})


# ─── DA1. Writing to support ─────────────────────────────────────────────────────────────────

def test_da1_a_message_is_kept_with_what_the_app_knew(client, db, co, desk):
    ctx = {"page": "manager-reports", "app_version": "a1b2c3d4", "language": "fa", "user_agent": "Firefox",
           "errors": [{"method": "GET", "path": "/manager-reports/books/trial-balance", "status": 500,
                       "request_id": "req-123"}] * 12, "secret": "dropped"}
    r = _write(client, co, context=ctx)
    assert r.status_code == 201, r.text
    assert r.json()["message"]["author"] == "user" and r.json()["desk"] == {"online": False}
    with use_company(co["cid"]):
        m = db.execute(select(SupportMessage)).scalars().one()
    assert m.channel == "web" and m.context["page"] == "manager-reports" and "secret" not in m.context
    assert len(m.context["errors"]) == 10 and m.context["errors"][0]["request_id"] == "req-123"
    conv = _login(client, co["cid"], co["owner"]).get("/support").json()
    assert [x["text"] for x in conv["messages"]] == ["The trial balance page is empty"] and conv["unread"] == 0
    # another person in the company has a conversation of their own
    assert _login(client, co["cid"], co["cfo"]).get("/support").json()["messages"] == []


def test_da1_an_empty_message_and_the_days_limit(client, db, co, monkeypatch):
    assert _write(client, co, text="  ").status_code == 422
    monkeypatch.setattr(S, "DAILY_LIMIT", 2)
    assert _write(client, co, text="one").status_code == 201
    assert _write(client, co, text="two").status_code == 201
    r = _write(client, co, text="three")
    assert r.status_code == 429 and r.headers["X-Error-Code"] == "support_limit"
    r = _login(client, co["cid"], co["owner"]).post("/support/messages", json={"text": "three"},
                                                     headers={"X-UI-Language": "fa"})
    assert r.json()["detail"].startswith("امروز")                       # in the page's language


# ─── DA2. A screenshot ─────────────────────────────────────────────────────────────────────────

def test_da2_a_screenshot_goes_with_the_message(client, db, co):
    api = _login(client, co["cid"], co["accountant"])
    up = api.post("/support/attachments", files={"file": ("screen.png", PNG, "image/png")})
    assert up.status_code == 201, up.text
    r = api.post("/support/messages", json={"text": "", "attachment_ids": [up.json()["id"]]})
    assert r.status_code == 201 and r.json()["message"]["attachments"] == [up.json()["id"]]
    assert api.post("/support/attachments", files={"file": ("a.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 422
    assert api.post("/support/messages", json={"attachment_ids": [str(uuid.uuid4())]}).status_code == 422


# ─── DA3. The desk's queue ──────────────────────────────────────────────────────────────────────

def test_da3_the_desk_takes_the_waiting_messages_with_who_wrote_them(client, db, co, desk):
    _write(client, co, text="first")
    _write(client, co, text="second", user="cfo")
    assert client.post("/desk/pending").status_code == 404                         # no key
    assert client.post("/desk/pending", headers={"Authorization": "Bearer nope"}).status_code == 404
    items = client.post("/desk/pending", headers=desk).json()["items"]
    assert [i["message"]["text"] for i in items] == ["first", "second"]
    one = items[0]
    assert one["person"]["username"] == co["owner"].username and one["person"]["role"] == "owner"
    assert one["company"]["id"] == co["cid"] and one["company"]["locale"] == "ir" and one["history"] == []
    assert client.post("/desk/pending", headers=desk).json()["items"] == []          # taken
    # a message taken and never answered comes back after the lease
    with use_company(co["cid"]):
        for m in db.execute(select(SupportMessage)).scalars():
            m.desk_taken_at = datetime.now(timezone.utc) - S.LEASE - timedelta(seconds=5)
        db.commit()
    client.post("/desk/reply", headers=desk, json={"thread_id": items[1]["thread_id"], "text": "On it"})
    again = client.post("/desk/pending", headers=desk).json()["items"]
    assert [i["message"]["text"] for i in again] == ["first"]                          # the answered one isn't


def test_da3_the_conversation_so_far_comes_with_a_new_message(client, db, co, desk):
    _write(client, co, text="the report is empty")
    [first] = client.post("/desk/pending", headers=desk).json()["items"]
    client.post("/desk/reply", headers=desk, json={"thread_id": first["thread_id"], "text": "Which report?"})
    _write(client, co, text="the trial balance")
    [second] = client.post("/desk/pending", headers=desk).json()["items"]
    assert [(h["author"], h["text"]) for h in second["history"]] == [("user", "the report is empty"),
                                                                       ("desk", "Which report?")]


# ─── DA4. The reply: in the panel, a badge, the bell — until it's read ────────────────────────

def test_da4_a_reply_is_unread_until_seen_and_rings_the_bell(client, db, co, desk):
    from app.services.notification_service import refresh_notifications as refresh
    _write(client, co, text="help")
    [item] = client.post("/desk/pending", headers=desk).json()["items"]
    r = client.post("/desk/reply", headers=desk, json={"thread_id": item["thread_id"], "text": "Fixed it for you"})
    assert r.status_code == 201
    owner = _login(client, co["cid"], co["owner"])
    conv = owner.get("/support").json()
    assert conv["unread"] == 1 and conv["messages"][-1] == {**conv["messages"][-1], "author": "desk",
                                                             "text": "Fixed it for you", "seen": False}
    lines = _bell(owner)
    assert lines and lines[0]["link_page"] == "support"
    assert not _bell(_login(client, co["cid"], co["cfo"]))                          # not someone else's
    with use_company(co["cid"]):                                                   # the bell's refresh keeps it
        refresh(db)
    assert _bell(_login(client, co["cid"], co["owner"]))
    owner = _login(client, co["cid"], co["owner"])
    assert owner.post("/support/seen").json() == {"seen": 1}
    assert owner.get("/support").json()["unread"] == 0 and not _bell(owner)
    with use_company(co["cid"]):
        refresh(db)                                                                 # and stays gone once read
    assert not _bell(_login(client, co["cid"], co["owner"]))
    # the next reply rings again, though the last line was read (a per-conversation line never came back)
    client.post("/desk/reply", headers=desk, json={"thread_id": item["thread_id"], "text": "One more thing"})
    lines = _bell(_login(client, co["cid"], co["owner"]))
    assert [n["message"] for n in lines] == ["One more thing"]
    client.post("/desk/reply", headers=desk, json={"thread_id": item["thread_id"], "text": "And another"})
    with use_company(co["cid"]):
        refresh(db)
    assert [n["message"] for n in _bell(_login(client, co["cid"], co["owner"]))] == ["And another"]   # one line, newest


def test_da4_a_reply_to_no_conversation_is_refused(client, db, co, desk):
    assert client.post("/desk/reply", headers=desk, json={"thread_id": str(uuid.uuid4()), "text": "x"}).status_code == 422


# ─── DA5. Online ────────────────────────────────────────────────────────────────────────────────

def test_da5_the_desk_is_online_while_it_checks_in(client, db, co, desk):
    assert _login(client, co["cid"], co["owner"]).get("/support").json()["desk"] == {"online": False}
    assert client.post("/desk/hello", headers=desk).json() == {"ok": True}
    assert _login(client, co["cid"], co["owner"]).get("/support").json()["desk"] == {"online": True}
    value = S._settings(db)
    value["seen_at"] = (datetime.now(timezone.utc) - S.ONLINE_WINDOW - timedelta(seconds=1)).isoformat()
    S._save(db, value)
    db.commit()
    assert _login(client, co["cid"], co["owner"]).get("/support").json()["desk"] == {"online": False}


# ─── DA6. The desk reads a screenshot — only one sent to support ───────────────────────────────

def test_da6_the_desk_downloads_only_support_screenshots(client, db, co, desk):
    api = _login(client, co["cid"], co["owner"])
    up = api.post("/support/attachments", files={"file": ("s.png", PNG, "image/png")}).json()
    stray = api.post("/support/attachments", files={"file": ("t.png", PNG, "image/png")}).json()
    api.post("/support/messages", json={"text": "look", "attachment_ids": [up["id"]]})
    r = client.get(f"/desk/attachments/{up['id']}", headers=desk)
    assert r.status_code == 200 and r.content == PNG
    assert client.get(f"/desk/attachments/{stray['id']}", headers=desk).status_code == 404   # never sent
    assert client.get(f"/desk/attachments/{up['id']}").status_code == 404                    # no key


# ─── DA7. The desk key ────────────────────────────────────────────────────────────────────────────

def test_da7_only_the_platform_admin_makes_a_desk_key(client, db, co):
    from tests.test_messenger_bot import _login as login_as
    owner = _login(client, co["cid"], co["owner"])
    assert owner.post("/admin/support-desk/key").status_code == 403
    admin = login_as(client, None, str(uuid.uuid4()), superadmin=True)
    try:
        r = admin.post("/admin/support-desk/key")
        assert r.status_code == 200 and r.json()["connected"] is True and r.json()["desk_key"].startswith("sd_")
        key = r.json()["desk_key"]
        assert key not in str(S._settings(db))                                   # only its hash is kept
        assert "desk_key" not in admin.get("/admin/support-desk").json()
        assert client.post("/desk/hello", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        new = admin.post("/admin/support-desk/key").json()["desk_key"]              # a new key replaces it
        assert client.post("/desk/hello", headers={"Authorization": f"Bearer {key}"}).status_code == 404
        assert admin.delete("/admin/support-desk").status_code == 204
        assert client.post("/desk/hello", headers={"Authorization": f"Bearer {new}"}).status_code == 404
    finally:
        S.turn_off(db)
        db.commit()


# ─── DA8. From a phone ────────────────────────────────────────────────────────────────────────────

def test_da8_a_phone_writes_to_the_same_conversation(client, db, co, desk):
    _write(client, co, text="from the web")
    client.cookies.clear()
    r = client.post(f"{API}/auth/login", json={"username": co["owner"].username, "password": "x" * 12,
                                               "device_name": "Pixel"})
    phone = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    up = client.post(f"{API}/support/attachments", headers=phone, files={"file": ("p.png", PNG, "image/png")})
    assert up.status_code == 201, up.text
    r = client.post(f"{API}/support/messages", headers=phone, json={"text": "and from the phone",
                                                                     "attachment_ids": [up.json()["id"]]})
    assert r.status_code == 201 and r.json()["message"]["channel"] == "android"
    conv = client.get(f"{API}/support", headers=phone).json()
    assert [m["text"] for m in conv["messages"]] == ["from the web", "and from the phone"]
    items = client.post("/desk/pending", headers=desk).json()["items"]
    client.post("/desk/reply", headers=desk, json={"thread_id": items[0]["thread_id"], "text": "Got both"})
    assert client.get(f"{API}/support", headers=phone).json()["unread"] == 1
    assert client.post(f"{API}/support/seen", headers=phone).json() == {"seen": 1}
    with use_company(co["cid"]):
        assert db.execute(select(SupportThread)).scalars().one().channel == "android"

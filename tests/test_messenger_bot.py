"""Telegram / Bale bot (roadmap 2026-09 §5.7, part 2): a private chat linked
to a login with a one-time code — or signed in, or made an account, in the
chat itself (scenarios CU1–CU6) — talks to the same assistant as the web chat
(its very handler, CU7), as that user; proposals come back with Confirm /
Cancel buttons; voice notes are transcribed; photos and files go in as the
web's attachments and files come back as documents (CU8); unlinked chats,
groups, other users' cards, re-delivered updates and a wrong webhook secret get
nowhere, and an update the server stopped half way through is handled again
(CU9)."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.messenger import MessengerLink
from app.services import messenger

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefgh"


@pytest.fixture()
def bot_api(monkeypatch):
    """The Bot API, mocked: every call is recorded; replies are ok."""
    calls: list[tuple[str, dict]] = []
    overrides: dict[str, httpx.Response] = {}

    files: dict[str, bytes] = {}                     # file_path → the bytes the platform serves

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if (request.headers.get("content-type") or "").startswith("multipart/"):
            body = {"_multipart": request.read()}               # a document's upload
        else:
            body = json.loads(request.content) if request.content and request.method == "POST" else {}
        calls.append((method, body))
        if method in overrides:
            return overrides[method]
        if "/file/bot" in request.url.path:
            path = request.url.path.split("/file/bot", 1)[1].split("/", 1)[1]
            return httpx.Response(200, content=files.get(path, b"OggS" + b"\x00" * 50))
        result = {"getMe": {"username": "BooksBot"}, "getFile": {"file_path": "voice/1.oga", "file_size": 54}}.get(method, True)
        if method == "getFile" and body.get("file_id") in files:
            result = {"file_path": body["file_id"], "file_size": len(files[body["file_id"]])}
        return httpx.Response(200, json={"ok": True, "result": result})
    monkeypatch.setattr(messenger, "_TRANSPORT", httpx.MockTransport(handler))
    handler.files = files
    return calls, overrides


@pytest.fixture()
def co(db):
    """A company, its owner (a real login) and a connected Telegram bot."""
    from app.core.auth import hash_password
    from app.models.user import User
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Sterling Ltd", slug=f"gb-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    h, salt = hash_password("Secret#12345")
    u = User(username=f"own-{uuid.uuid4().hex[:6]}", password_hash=h, password_salt=salt, is_admin=True,
             role="owner", is_active=True, company_id=c.id, preferred_language="en")
    db.add(u)
    db.commit()
    messenger._save(db, {"telegram": {"token": TOKEN, "secret": "s3cret-path", "username": "BooksBot"}})
    db.commit()
    ids = str(c.id), str(u.id)
    db.expunge_all()
    yield ids
    db.rollback()
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        db.query(MessengerLink).filter(MessengerLink.user_id == u.id).delete()
        db.commit()
    from app.models.app_setting import AppSetting
    db.query(AppSetting).filter(AppSetting.key == messenger.SETTINGS_KEY).delete()
    db.commit()
    _purge_company(db, ids[0])


def _msg(text=None, chat_id=5550001, update_id=None, chat_type="private", **extra):
    m = {"message_id": 1, "chat": {"id": chat_id, "type": chat_type, "first_name": "Sara"},
         "from": {"id": chat_id, "language_code": "en"}, **extra}
    if text is not None:
        m["text"] = text
    return {"update_id": update_id or int(uuid.uuid4().int % 10**9), "message": m}


def _run(db, update):
    return asyncio.run(messenger.handle_update(db, "telegram", update))


def _sent(calls, method="sendMessage"):
    return [b for m, b in calls if m == method]


def _start(db, user_id):
    """As the app does it: inside the user's company (the link is stamped)."""
    from app.db.tenant import use_company
    from app.models.user import User
    cid = str(db.get(User, uuid.UUID(user_id)).company_id)
    with use_company(cid):
        out = messenger.start_link(db, user_id=user_id, platform="telegram")
        db.commit()
    return out


def _link(db, user_id, chat_id=5550001):
    from app.db.tenant import tenant_bypass
    out = _start(db, user_id)
    _run(db, _msg(f"/start {out['code']}", chat_id=chat_id))
    with tenant_bypass():
        return db.execute(select(MessengerLink).where(MessengerLink.chat_id == str(chat_id))).scalars().one()


@dataclass
class FakeResult:
    text: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    proposals: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    turns: int = 1


@pytest.fixture()
def fake_turn(monkeypatch):
    """The model, faked where the web chat's handler calls it — the bot runs that handler (CU7)."""
    seen: list[dict] = []

    def install(result: FakeResult):
        async def run_chat_turn(db, **kw):
            from app.core.request_context import get_current_actor
            from app.db.tenant import get_current_company
            seen.append({**kw, "company": get_current_company(), "actor": get_current_actor()})
            return result
        from app.api import ai_accountant as api_mod
        from app.services.ai_accountant import orchestrator
        monkeypatch.setattr(orchestrator, "run_chat_turn", run_chat_turn)
        monkeypatch.setattr(api_mod, "run_chat_turn", run_chat_turn)

        async def no_ocr(db, ids):
            return "", []
        monkeypatch.setattr(api_mod, "_build_ocr_context", no_ocr)
        return seen
    return install


# ─── 1. Connecting a bot ──────────────────────────────────────────────────────

def test_connecting_checks_the_token_and_points_the_webhook_here(db, bot_api, monkeypatch):
    from app.core.config import settings
    calls, _ = bot_api
    monkeypatch.setattr(settings, "app_public_url", "https://books.example.com")
    try:
        out = asyncio.run(messenger.connect(db, "bale", TOKEN))
        assert out == {"name": "Bale", "connected": True, "username": "BooksBot", "mode": "webhook",
                       "relay_seen_at": None}
        (_, hook), = [c for c in calls if c[0] == "setWebhook"]
        secret = messenger.load_settings(db)["bale"]["secret"]
        assert hook["url"] == f"https://books.example.com/bots/bale/webhook/{secret}" and hook["secret_token"] == secret
        raw = db.execute(select(messenger._row(db).__class__.value).where(
            messenger._row(db).__class__.key == messenger.SETTINGS_KEY)).scalar_one()
        assert TOKEN not in raw
        asyncio.run(messenger.disconnect(db, "bale"))
        assert calls[-1][0] == "deleteWebhook" and "bale" not in messenger.load_settings(db)
    finally:
        messenger._save(db, {})
        db.commit()


@pytest.mark.parametrize("token,url,msg", [
    ("not-a-token", "https://books.example.com", "bot token"),
    (TOKEN, "http://localhost:8000", "https"),
])
def test_a_bad_token_or_a_private_address_is_refused(db, bot_api, monkeypatch, token, url, msg):
    from app.core.config import settings
    monkeypatch.setattr(settings, "app_public_url", url)
    with pytest.raises(ValueError, match=msg):
        asyncio.run(messenger.connect(db, "telegram", token))


def test_the_platform_refusing_the_token_is_reported(db, bot_api, monkeypatch):
    from app.core.config import settings
    _calls, overrides = bot_api
    monkeypatch.setattr(settings, "app_public_url", "https://books.example.com")
    overrides["getMe"] = httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    with pytest.raises(ValueError, match="Unauthorized"):
        asyncio.run(messenger.connect(db, "telegram", TOKEN))


# ─── 2. The webhook ─────────────────────────────────────────────────────────────

def test_the_webhook_wants_the_secret(client, co, bot_api, db, monkeypatch):
    from app.api import bots
    from tests.conftest import _TestSession
    monkeypatch.setattr(bots, "_session_factory", lambda: _TestSession)
    calls, _ = bot_api
    assert client.post("/bots/telegram/webhook/wrong", json=_msg("hi")).status_code == 404
    assert client.post("/bots/whatsapp/webhook/s3cret-path", json=_msg("hi")).status_code == 404
    assert client.post("/bots/telegram/webhook/s3cret-path", json=_msg("hi"),
                       headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}).status_code == 404
    r = client.post("/bots/telegram/webhook/s3cret-path", json=_msg("hi"),
                    headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret-path"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "link this chat" in _sent(calls)[-1]["text"]                      # handled after the response
    assert client.post("/bots/telegram/webhook/s3cret-path", content=b"[]",
                       headers={"Content-Type": "application/json"}).status_code == 400


# ─── 3. Linking ──────────────────────────────────────────────────────────────────

def test_a_one_time_code_links_the_chat(co, bot_api, db):
    _cid, uid = co
    calls, _ = bot_api
    link = _link(db, uid)
    assert link.status == "active" and link.chat_name == "Sara" and link.code is None
    assert "Signed in to Sterling Ltd" in _sent(calls)[-1]["text"]
    assert link.via == "code" and link.token_version == 0
    _run(db, _msg(f"/start {'x' * 12}", chat_id=5550002))                      # unknown code
    assert "expired" in _sent(calls)[-1]["text"]


def test_an_expired_code_does_not_link(co, bot_api, db):
    from app.db.tenant import tenant_bypass
    _cid, uid = co
    calls, _ = bot_api
    out = _start(db, uid)
    with tenant_bypass():
        row = db.execute(select(MessengerLink).where(MessengerLink.code == out["code"])).scalars().one()
        row.code_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    _run(db, _msg(f"/start {out['code']}"))
    assert "expired" in _sent(calls)[-1]["text"]
    assert out["url"] == f"https://t.me/BooksBot?start={out['code']}"


def test_unlinked_chats_and_groups_only_get_instructions(co, bot_api, db, fake_turn):
    calls, _ = bot_api
    seen = fake_turn(FakeResult("never"))
    _run(db, _msg("paid 20 for lunch"))
    assert "link this chat" in _sent(calls)[-1]["text"]
    _run(db, _msg("paid 20 for lunch", chat_id=-100200, chat_type="group"))
    assert "private chat" in _sent(calls)[-1]["text"]
    assert seen == []


def test_a_re_delivered_update_is_ignored(co, bot_api, db):
    calls, _ = bot_api
    update = _msg("hello", update_id=424242)
    assert _run(db, update) is not None
    n = len(calls)
    assert _run(db, update) is None and len(calls) == n


# ─── 4. Talking to the assistant ─────────────────────────────────────────────────

def test_a_message_is_a_turn_of_the_assistant_as_that_user(co, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    link = _link(db, uid)
    token = str(uuid.uuid4())
    seen = fake_turn(FakeResult("**Booked** under `7500` travel?",
                                proposals=[{"confirmation_token": token, "summary": "Paid 20 GBP — lunch"}]))
    _run(db, _msg("paid 20 for lunch from the card"))
    turn = seen[-1]
    assert turn["user_id"] == uid and turn["user_message"] == "paid 20 for lunch from the card"
    assert turn["company"] == cid and turn["actor"].user_id == uid and turn["mode"] == "default"
    texts = _sent(calls)
    assert texts[-2]["text"] == "Booked under 7500 travel?"                    # Markdown flattened
    card = texts[-1]
    assert card["text"] == "Paid 20 GBP — lunch"
    buttons = card["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [f"ok:{token}", f"no:{token}"]
    db.expire_all()
    assert db.get(MessengerLink, link.id).last_seen_at is not None


def test_the_conversation_continues_and_new_starts_another(co, bot_api, db, fake_turn):
    _cid, uid = co
    link = _link(db, uid)
    result = FakeResult("ok")
    seen = fake_turn(result)
    _run(db, _msg("one"))
    _run(db, _msg("two"))
    assert seen[0]["session_id"] is None and seen[1]["session_id"] == result.session_id
    _run(db, _msg("/new"))
    _run(db, _msg("three"))
    assert seen[2]["session_id"] is None
    _run(db, _msg("/stop"))
    db.expire_all()
    assert db.get(MessengerLink, link.id) is None


def test_long_replies_are_split(co, bot_api, db, fake_turn):
    _cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    fake_turn(FakeResult(("line of text\n" * 700).strip()))
    before = len(_sent(calls))
    _run(db, _msg("show everything"))
    parts = _sent(calls)[before:]
    assert len(parts) >= 3 and all(len(p["text"]) <= messenger.MAX_TEXT for p in parts)


def test_a_voice_note_is_transcribed_first(co, bot_api, db, fake_turn, monkeypatch):
    from app.services import speech
    _cid, uid = co
    calls, _ = bot_api
    _link(db, uid)

    async def fake_transcribe(data):
        assert data.startswith(b"OggS")
        return {"text": "پنجاه هزار تومن نون", "provider": "gemini"}
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    seen = fake_turn(FakeResult("ثبت شود؟"))
    _run(db, _msg(voice={"file_id": "F1", "duration": 3, "mime_type": "audio/ogg"}))
    assert ("getFile", {"file_id": "F1"}) in calls
    assert seen[-1]["user_message"] == "پنجاه هزار تومن نون"
    assert any(p["text"] == "🎙 پنجاه هزار تومن نون" for p in _sent(calls))


def test_a_role_without_the_assistant_and_a_suspended_company_are_told(co, bot_api, db, fake_turn):
    from app.models.user import User
    cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    seen = fake_turn(FakeResult("never"))
    user = db.get(User, uuid.UUID(uid))
    user.role = "viewer"
    db.commit()
    _run(db, _msg("paid 20"))
    assert "can't use the assistant" in _sent(calls)[-1]["text"]
    user.role = "owner"
    db.get(Company, uuid.UUID(cid)).status = "suspended"
    db.commit()
    _run(db, _msg("paid 20"))
    assert "suspended" in _sent(calls)[-1]["text"] and seen == []
    db.get(Company, uuid.UUID(cid)).status = "active"
    db.commit()


# ─── 5. Confirm / Cancel ──────────────────────────────────────────────────────────

def _proposal(db, cid, uid):
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    token = uuid.uuid4()
    with use_company(cid):
        db.add(AIProposal(confirmation_token=token, user_id=uid, tool_name="propose_remember_preference",
                          tool_input={"description": "Snapp ride", "account_code": "7400"}, status="pending",
                          user_message="always snapp under motor"))
        db.commit()
    return str(token)


def _tap(db, data, chat_id=5550001):
    return _run(db, {"update_id": int(uuid.uuid4().int % 10**9), "callback_query": {
        "id": "cb1", "data": data, "message": {"message_id": 77, "chat": {"id": chat_id, "type": "private"}}}})


def test_confirm_executes_the_proposal_as_the_linked_user(co, bot_api, db):
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from app.models.learned_preference import LearnedPreference
    cid, uid = co
    calls, _ = bot_api
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        db.commit()
    _link(db, uid)
    token = _proposal(db, cid, uid)
    _tap(db, f"ok:{token}")
    with use_company(cid):
        assert db.execute(select(LearnedPreference)).scalars().one().account_code == "7400"
    assert ("answerCallbackQuery", {"callback_query_id": "cb1", "text": "✅ Done."}) in calls
    assert ("editMessageReplyMarkup", {"chat_id": "5550001", "message_id": 77,
                                       "reply_markup": {"inline_keyboard": []}}) in calls


def test_cancel_and_someone_elses_card(co, bot_api, db):
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    token = _proposal(db, cid, uid)
    _tap(db, f"no:{token}")
    with use_company(cid):
        assert db.execute(select(AIProposal.status).where(AIProposal.confirmation_token == uuid.UUID(token))).scalar_one() == "cancelled"
    other = _proposal(db, cid, str(uuid.uuid4()))
    _tap(db, f"ok:{other}")
    assert calls[-1][1].get("text") == "This card isn't yours." or \
        any(b.get("text") == "This card isn't yours." for m, b in calls if m == "answerCallbackQuery")
    _tap(db, f"ok:{token}", chat_id=999)                                            # an unlinked chat
    assert any("signed out" in (b.get("text") or "") for m, b in calls if m == "answerCallbackQuery")


# ─── 6. The app's side ─────────────────────────────────────────────────────────────

def _login(client, cid, uid, role="owner", superadmin=False):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=uid, username="u", is_admin=role == "owner", role=role, company_id=cid,
                               is_superadmin=superadmin)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_users_make_a_link_see_their_chats_and_unlink(client, co, bot_api, db):
    cid, uid = co
    api = _login(client, cid, uid)
    st = api.get("/ai-accountant/messenger").json()
    assert st["platforms"]["telegram"]["connected"] is True and st["platforms"]["bale"]["connected"] is False
    r = api.post("/ai-accountant/messenger/link", json={"platform": "telegram"})
    assert r.status_code == 201 and r.json()["url"].startswith("https://t.me/BooksBot?start=")
    assert api.post("/ai-accountant/messenger/link", json={"platform": "bale"}).status_code == 422
    _run(db, _msg(f"/start {r.json()['code']}"))
    links = api.get("/ai-accountant/messenger").json()["links"]
    assert [lk["chat_name"] for lk in links] == ["Sara"]
    other = _login(client, cid, str(uuid.uuid4()))
    assert other.delete(f"/ai-accountant/messenger/links/{links[0]['id']}").status_code == 404
    api = _login(client, cid, uid)
    assert api.delete(f"/ai-accountant/messenger/links/{links[0]['id']}").status_code == 204
    assert api.get("/ai-accountant/messenger").json()["links"] == []
    viewer = _login(client, cid, str(uuid.uuid4()), role="viewer")        # a role the DB does not override
    assert viewer.post("/ai-accountant/messenger/link", json={"platform": "telegram"}).status_code == 403


def test_only_the_platform_admin_connects_bots(client, co, bot_api, db, monkeypatch):
    from app.core.config import settings
    cid, uid = co
    monkeypatch.setattr(settings, "app_public_url", "https://books.example.com")
    owner = _login(client, cid, uid)
    assert owner.get("/admin/messenger-bots").status_code == 403
    assert owner.put("/admin/messenger-bots/bale", json={"token": TOKEN}).status_code == 403
    admin = _login(client, None, str(uuid.uuid4()), superadmin=True)
    r = admin.put("/admin/messenger-bots/bale", json={"token": TOKEN})
    assert r.status_code == 200 and r.json()["username"] == "BooksBot"
    assert admin.get("/admin/messenger-bots").json()["bots"]["bale"]["connected"] is True
    assert admin.put("/admin/messenger-bots/bale", json={"token": "nonsense-token"}).status_code == 422
    assert admin.delete("/admin/messenger-bots/bale").status_code == 204
    assert admin.get("/admin/messenger-bots").json()["bots"]["bale"]["connected"] is False


def test_confirm_above_the_approval_limit_goes_for_approval(co, bot_api, db):
    """Guardrails (§5.6): the bot's Confirm is the requester's, so above the
    company's threshold it sends the entry for approval instead of posting."""
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant import guardrails
    cid, uid = co
    calls, _ = bot_api
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        guardrails.save_settings(db, approval_threshold=1_000)
        token = uuid.uuid4()
        db.add(AIProposal(confirmation_token=token, user_id=uid, tool_name="propose_create_transaction",
                          tool_input={"date": "2026-09-20", "description": "Van", "lines": [
                              {"account_code": "7400", "debit": 5_000, "credit": 0},
                              {"account_code": "1200", "debit": 0, "credit": 5_000}]},
                          status="pending", amount=5_000))
        db.commit()
    _link(db, uid)
    _tap(db, f"ok:{token}")
    text = next(b["text"] for m, b in calls if m == "answerCallbackQuery")
    assert text.startswith("⏳ Sent for approval")
    with use_company(cid):
        db.expire_all()
        p = db.execute(select(AIProposal).where(AIProposal.confirmation_token == token)).scalars().one()
        assert p.status == "pending" and p.approval_status == "requested"


# ─── Relay mode: a server that can't reach the platform (ops/telegram-relay) ──────────────────────────────────

def _relay(client, key, path, body):
    return client.post(f"/bots/telegram/relay/{path}", json=body, headers={"Authorization": f"Bearer {key}"})


@pytest.fixture()
def relay(co, db, client):
    """The co fixture's bot, switched to relay mode; the relay has checked in."""
    key = messenger.enable_relay(db, "telegram")
    db.commit()
    assert _relay(client, key, "hello", {"username": "BooksRelayBot"}).status_code == 200
    return key


def test_relay_mode_keeps_no_token_and_the_server_never_calls_the_platform(client, co, relay, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    cfg = messenger.load_settings(db)["telegram"]
    assert cfg["mode"] == "relay" and "token" not in cfg and relay not in json.dumps(cfg)   # only the key's hash
    st = messenger.public_status(db)["telegram"]
    assert st["connected"] and st["mode"] == "relay" and st["username"] == "BooksRelayBot" and st["relay_seen_at"]

    out = _start(db, uid)                                         # the app's link names the relay's bot
    assert out["url"].startswith("https://t.me/BooksRelayBot?start=")
    r = _relay(client, relay, "update", {"update": _msg(f"/start {out['code']}", update_id=4100)})
    assert r.status_code == 200
    [linked] = r.json()["calls"]
    assert linked["method"] == "sendMessage" and linked["params"]["chat_id"] == "5550001"
    assert linked["params"]["text"].startswith("Signed in to Sterling Ltd")

    seen = fake_turn(FakeResult("Recorded? Say yes."))
    r = _relay(client, relay, "update", {"update": _msg("paid 20 for lunch", update_id=4101)})
    assert [c["params"]["text"] for c in r.json()["calls"]] == ["Recorded? Say yes."]
    assert seen[-1]["user_message"] == "paid 20 for lunch" and seen[-1]["company"] == cid
    # a re-delivered update (the relay retrying) does nothing twice
    assert _relay(client, relay, "update", {"update": _msg("paid 20 for lunch", update_id=4101)}).json()["calls"] == []
    assert calls == []                                            # nothing went to the platform from here


def test_the_relay_key_is_the_only_way_in(client, co, relay, bot_api, db):
    upd = {"update": _msg("/start", update_id=4200)}
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": relay}):
        assert client.post("/bots/telegram/relay/update", json=upd, headers=headers).status_code == 404
    assert _relay(client, relay, "update", upd).status_code == 200
    assert client.post(f"/bots/bale/relay/update", json=upd, headers={"Authorization": f"Bearer {relay}"}).status_code == 404
    assert client.post("/bots/telegram/webhook/anything", json=upd["update"]).status_code == 404   # no webhook in relay mode
    assert _relay(client, relay, "hello", {"username": "not a name!"}).status_code == 422
    # a new key replaces the old one
    new = messenger.enable_relay(db, "telegram")
    db.commit()
    assert _relay(client, relay, "hello", {"username": "BooksRelayBot"}).status_code == 404
    assert _relay(client, new, "hello", {"username": "BooksRelayBot"}).status_code == 200


def test_a_relayed_voice_note_comes_with_its_audio(client, co, relay, bot_api, db, fake_turn, monkeypatch):
    import base64

    from app.services import speech
    _cid, uid = co
    calls, _ = bot_api
    out = _start(db, uid)
    _relay(client, relay, "update", {"update": _msg(f"/start {out['code']}", update_id=4300)})

    async def fake_transcribe(data):
        assert data.startswith(b"OggS")
        return {"text": "fifty for bread", "provider": "gemini"}
    monkeypatch.setattr(speech, "transcribe", fake_transcribe)
    seen = fake_turn(FakeResult("Bread, 50?"))
    voice = {"file_id": "F9", "duration": 3, "mime_type": "audio/ogg"}
    audio = base64.b64encode(b"OggS" + b"\x00" * 40).decode()
    r = _relay(client, relay, "update", {"update": _msg(voice=voice, update_id=4301), "files": {"F9": audio}})
    assert [c["params"]["text"] for c in r.json()["calls"]] == ["🎙 fifty for bread", "Bread, 50?"]
    assert seen[-1]["user_message"] == "fifty for bread"
    # without the audio (too large for the relay to send) the user is told, and nothing is guessed
    r = _relay(client, relay, "update", {"update": _msg(voice={**voice, "file_id": "F10"}, update_id=4302)})
    assert [c["params"]["text"] for c in r.json()["calls"]] == [messenger._t("en", "voice_failed")]
    assert calls == []                                            # the server never fetched a file itself


def test_a_relayed_confirm_answers_the_button(client, co, relay, bot_api, db):
    cid, uid = co
    out = _start(db, uid)
    _relay(client, relay, "update", {"update": _msg(f"/start {out['code']}", update_id=4400)})
    token = _proposal(db, cid, uid)
    tap = {"update_id": 4401, "callback_query": {"id": "cb9", "data": f"ok:{token}",
                                                 "message": {"message_id": 77, "chat": {"id": 5550001, "type": "private"}}}}
    methods = [c["method"] for c in _relay(client, relay, "update", {"update": tap}).json()["calls"]]
    assert "answerCallbackQuery" in methods and "editMessageReplyMarkup" in methods


def test_only_the_platform_admin_turns_relay_mode_on(client, co, bot_api, db):
    cid, uid = co
    assert _login(client, cid, uid).post("/admin/messenger-bots/telegram/relay").status_code == 403
    admin = _login(client, None, str(uuid.uuid4()), superadmin=True)
    r = admin.post("/admin/messenger-bots/telegram/relay")
    assert r.status_code == 200 and r.json()["mode"] == "relay" and len(r.json()["relay_key"]) >= 40
    assert admin.post("/admin/messenger-bots/whatsapp/relay").status_code == 404
    bots = admin.get("/admin/messenger-bots").json()["bots"]
    assert "relay_key" not in json.dumps(bots) and bots["telegram"]["mode"] == "relay"
    # before the relay checks in there is no bot name for a link
    with pytest.raises(ValueError, match="relay hasn't checked in"):
        messenger.start_link(db, user_id=uid, platform="telegram")
    assert admin.delete("/admin/messenger-bots/telegram").status_code == 204
    assert bot_api[0] == []                                       # disconnecting a relay calls no platform

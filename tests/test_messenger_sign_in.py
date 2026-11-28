"""Telegram / Bale on their own (the owner, 2026-11-28, scenarios CU1–CU9): someone who only uses the bot signs in
in the chat — username, password (its message deleted), the two-factor code — or makes a new account there; a new
password ends the chat's sign-in as it ends a browser's; a message runs the web chat's own handler; photos and files
go in as the web's attachments and the journal's Excel or a close pack come back as documents; an update the server
stopped half way through is handled again."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.messenger import MessengerLink, MessengerSignIn, MessengerUpdate
from app.models.user import User
from app.services import messenger
from tests.test_messenger_bot import (  # noqa: F401 — the fixtures, shared
    FakeResult, _link, _msg, _proposal, _relay, _run, _sent, _start, _tap, bot_api, co, fake_turn, relay)

PASSWORD = "Secret#12345"


def _t(key, lang="en", **kw):
    return messenger._t(lang, key, **kw)


def _chat_link(db, chat_id=5550001):
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        db.expire_all()
        return db.execute(select(MessengerLink).where(MessengerLink.chat_id == str(chat_id))).scalars().first()


def _sign_in_row(db, chat_id=5550001):
    db.expire_all()
    return db.execute(select(MessengerSignIn).where(MessengerSignIn.chat_id == str(chat_id))).scalars().first()


def _username(db, uid):
    return db.get(User, uuid.UUID(uid)).username


@pytest.fixture()
def open_signup(monkeypatch, db):
    """Sign-up on, no mail server; the accounts it makes go afterwards."""
    from app.api import auth
    from app.core.config import settings
    from app.services import email_verification
    monkeypatch.setattr(settings, "allow_self_signup", True)
    monkeypatch.setattr(auth, "verification_required", lambda: False)
    monkeypatch.setattr(email_verification, "verification_required", lambda: False)
    made: list[str] = []
    yield made
    db.rollback()
    from tests.test_admin_audit import _purge_company
    for name in made:
        user = db.execute(select(User).where(User.username == name)).scalars().first()
        if user is not None:
            from app.db.tenant import tenant_bypass
            with tenant_bypass():
                db.query(MessengerLink).filter(MessengerLink.user_id == user.id).delete()
                db.commit()
            _purge_company(db, str(user.company_id))


# ─── CU1. Signing in in the chat ─────────────────────────────────────────────────────

def test_cu1_the_greeting_offers_sign_in(co, bot_api, db):
    calls, _ = bot_api
    _run(db, _msg("/start"))
    hello = _sent(calls)[-1]
    assert "/login" in hello["text"] and "/signup" not in hello["text"]          # sign-up is off on this server
    assert [b["callback_data"] for b in hello["reply_markup"]["inline_keyboard"][0]] == ["auth:login"]


def test_cu1_username_then_password_signs_the_chat_in(co, bot_api, db):
    from app.models.audit_log import AuditLog
    cid, uid = co
    calls, _ = bot_api
    _run(db, _msg("/login"))
    assert _sent(calls)[-1]["text"] == _t("ask_username")
    _run(db, _msg(_username(db, uid), message_id=900))
    assert _sent(calls)[-1]["text"] == _t("ask_password")
    # every answer of a sign-in leaves the chat: a password retyped where the username is asked mustn't stay
    assert ("deleteMessage", {"chat_id": "5550001", "message_id": 900}) in calls
    _run(db, _msg(PASSWORD, message_id=901))
    # the password left the chat before anything else was said
    assert ("deleteMessage", {"chat_id": "5550001", "message_id": 901}) in calls
    assert _sent(calls)[-1]["text"].startswith("Signed in to Sterling Ltd as")
    link = _chat_link(db)
    user = db.get(User, uuid.UUID(uid))
    assert (link.via, link.token_version, str(link.user_id), str(link.company_id)) == \
        ("password", int(user.token_version or 0), uid, cid)
    assert _sign_in_row(db) is None
    row = db.execute(select(AuditLog).where(AuditLog.action == "login", AuditLog.user_id == uid)).scalars().first()
    assert row is not None and row.detail == "Telegram chat (password)" and row.ip_address == "telegram:5550001"


def test_cu1_the_greeting_button_starts_the_sign_in(co, bot_api, db):
    calls, _ = bot_api
    _tap(db, "auth:login")
    assert _sent(calls)[-1]["text"] == _t("ask_username") and _sign_in_row(db).step == "username"


def test_cu1_a_wrong_password_asks_again_and_is_counted(co, bot_api, db):
    from app.models.audit_log import AuditLog
    _cid, uid = co
    calls, _ = bot_api
    name = _username(db, uid)
    _run(db, _msg("/login"))
    _run(db, _msg(name))
    _run(db, _msg("not-it", message_id=902))
    assert ("deleteMessage", {"chat_id": "5550001", "message_id": 902}) in calls   # a wrong one goes too
    assert _sent(calls)[-1]["text"] == _t("bad_login") and _sign_in_row(db).step == "username"
    assert _chat_link(db) is None
    assert db.execute(select(AuditLog).where(AuditLog.action == "login_failed")).scalars().first() is not None
    for _ in range(4):                                           # five wrong in all: the web's limit, per username
        _run(db, _msg(name))
        _run(db, _msg("not-it"))
    _run(db, _msg(name))
    _run(db, _msg(PASSWORD))                                     # even the right one waits now
    assert _sent(calls)[-1]["text"] == _t("too_many") and _chat_link(db) is None


def test_cu1_the_first_password_is_refused(co, bot_api, db):
    from app.core.auth import hash_password
    cid, _uid = co
    calls, _ = bot_api
    h, salt = hash_password("admin")
    u = User(username=f"first-{uuid.uuid4().hex[:6]}", password_hash=h, password_salt=salt, role="owner",
             is_active=True, company_id=uuid.UUID(cid))
    db.add(u)
    db.commit()
    _run(db, _msg("/login"))
    _run(db, _msg(u.username))
    _run(db, _msg("admin"))
    assert _sent(calls)[-1]["text"] == _t("first_password") and _chat_link(db) is None


def test_cu1_a_suspended_company_is_told(co, bot_api, db):
    from app.models.company import Company
    cid, uid = co
    calls, _ = bot_api
    db.get(Company, uuid.UUID(cid)).status = "suspended"
    db.commit()
    try:
        _run(db, _msg("/login"))
        _run(db, _msg(_username(db, uid)))
        _run(db, _msg(PASSWORD))
        assert _sent(calls)[-1]["text"] == _t("suspended") and _chat_link(db) is None
    finally:
        db.get(Company, uuid.UUID(cid)).status = "active"
        db.commit()


# ─── CU2. Two-step sign-in ─────────────────────────────────────────────────────────────

def test_cu2_two_factor_asks_for_the_code(co, bot_api, db):
    from app.core.secrets import encrypt_secret
    from app.core.two_factor import code_at, current_step, new_secret
    _cid, uid = co
    calls, _ = bot_api
    secret = new_secret()
    user = db.get(User, uuid.UUID(uid))
    user.totp_secret, user.totp_enabled_at = encrypt_secret(secret), datetime.now(timezone.utc)
    db.commit()
    _run(db, _msg("/login"))
    _run(db, _msg(user.username))
    _run(db, _msg(PASSWORD))
    assert _sent(calls)[-1]["text"] == _t("ask_code") and _sign_in_row(db).step == "code"
    right = code_at(secret, current_step())
    _run(db, _msg("111111" if right != "111111" else "222222", message_id=903))
    assert ("deleteMessage", {"chat_id": "5550001", "message_id": 903}) in calls
    assert _sent(calls)[-1]["text"] == _t("bad_2fa") and _chat_link(db) is None
    _run(db, _msg(right))
    assert _sent(calls)[-1]["text"].startswith("Signed in to") and _chat_link(db).via == "password"


# ─── CU3. A new account in the chat ─────────────────────────────────────────────────────

def test_cu3_a_new_account_is_made_and_signed_in(co, bot_api, db, open_signup, fake_turn):
    from app.models.company import Company
    _cid, uid = co
    calls, _ = bot_api
    _run(db, _msg("/start"))
    assert [b["callback_data"] for b in _sent(calls)[-1]["reply_markup"]["inline_keyboard"][0]] == \
        ["auth:login", "auth:signup"]
    _tap(db, "auth:signup")
    assert _sent(calls)[-1]["text"] == _t("choose_username")
    _run(db, _msg("two words"))
    assert _sent(calls)[-1]["text"] == _t("bad_username")
    _run(db, _msg(_username(db, uid)))                         # someone has it
    assert _sent(calls)[-1]["text"] == _t("username_taken")
    name = f"sara-{uuid.uuid4().hex[:6]}"
    open_signup.append(name)
    _run(db, _msg(name))
    assert _sent(calls)[-1]["text"] == _t("choose_password")
    _run(db, _msg("short", message_id=904))
    assert ("deleteMessage", {"chat_id": "5550001", "message_id": 904}) in calls
    assert "Choose another password" in _sent(calls)[-1]["text"]
    _run(db, _msg("Long#Password1", message_id=905))
    assert ("deleteMessage", {"chat_id": "5550001", "message_id": 905}) in calls
    assert _sent(calls)[-1]["text"].startswith(f"Signed in to Sara as {name}")
    user = db.execute(select(User).where(User.username == name)).scalars().one()
    company = db.get(Company, user.company_id)
    assert (company.kind, user.role, user.preferred_language) == ("personal", "personal", "en")
    assert _chat_link(db).via == "signup"
    seen = fake_turn(FakeResult("Lunch, 7?"))                  # the new books talk to the assistant at once
    _run(db, _msg("paid 7 for lunch"))
    assert seen[-1]["mode"] == "personal" and _sent(calls)[-1]["text"] == "Lunch, 7?"


def test_cu3_a_persian_telegram_makes_a_persian_account(co, bot_api, db, open_signup):
    calls, _ = bot_api
    name = f"ali-{uuid.uuid4().hex[:6]}"
    open_signup.append(name)
    fa = {"from": {"id": 5550001, "language_code": "fa"}}
    _run(db, _msg("/signup", **fa))
    assert _sent(calls)[-1]["text"] == _t("choose_username", "fa")
    _run(db, _msg(name, **fa))
    _run(db, _msg("Long#Password1", **fa))
    assert _sent(calls)[-1]["text"].startswith("به ")
    assert db.execute(select(User.preferred_language).where(User.username == name)).scalar_one() == "fa"


def test_cu3_with_mail_the_address_is_asked_and_confirmed_first(co, bot_api, db, open_signup, monkeypatch):
    from app.api import auth
    from app.services import email_verification
    calls, _ = bot_api
    sent: list = []
    monkeypatch.setattr(auth, "verification_required", lambda: True)
    monkeypatch.setattr(email_verification, "verification_required", lambda: True)
    monkeypatch.setattr(auth, "send_verification_email", lambda user: sent.append(user.email))
    name = f"mina-{uuid.uuid4().hex[:6]}"
    open_signup.append(name)
    _run(db, _msg("/signup"))
    _run(db, _msg(name))
    assert _sent(calls)[-1]["text"] == _t("ask_email")
    _run(db, _msg("not an address"))
    assert _sent(calls)[-1]["text"] == _t("bad_email")
    _run(db, _msg("mina@example.com"))
    _run(db, _msg("Long#Password1"))
    assert _sent(calls)[-1]["text"] == _t("created_verify", email="mina@example.com") and sent == ["mina@example.com"]
    assert _chat_link(db) is None                              # in only once the address is confirmed
    user = db.execute(select(User).where(User.username == name)).scalars().one()
    assert user.verification_token and user.email == "mina@example.com"


def test_cu3_sign_up_off_says_so(co, bot_api, db):
    calls, _ = bot_api
    _run(db, _msg("/signup"))
    assert _sent(calls)[-1]["text"] == _t("signup_off") and _sign_in_row(db) is None


# ─── CU4. Stopping, timing out, a chat already in ──────────────────────────────────────────

def test_cu4_cancel_and_a_stale_sign_in(co, bot_api, db):
    _cid, uid = co
    calls, _ = bot_api
    _run(db, _msg("/login"))
    _run(db, _msg("/cancel"))
    assert _sent(calls)[-1]["text"] == _t("stopped") and _sign_in_row(db) is None
    _run(db, _msg("/login"))
    row = _sign_in_row(db)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    _run(db, _msg(_username(db, uid)))                         # ten minutes on: the greeting, not a password prompt
    assert "/login" in _sent(calls)[-1]["text"] and _sign_in_row(db) is None


def test_cu4_a_signed_in_chat_says_who_it_is(co, bot_api, db):
    _cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    _run(db, _msg("/login"))
    assert _sent(calls)[-1]["text"] == _t("already_in", user=_username(db, uid))


def test_cu4_start_in_a_signed_in_chat_starts_a_new_conversation(co, bot_api, db):
    _cid, uid = co
    link = _link(db, uid)
    link.session_id = uuid.uuid4()
    db.commit()
    _run(db, _msg("/start"))
    assert _chat_link(db).session_id is None


# ─── CU5/CU6. The sign-in ends with the user's other sessions ────────────────────────────────

def test_cu6_a_new_password_signs_the_chat_out(co, bot_api, db, fake_turn):
    _cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    seen = fake_turn(FakeResult("never"))
    user = db.get(User, uuid.UUID(uid))
    user.token_version = int(user.token_version or 0) + 1      # what a password change or "sign out everywhere" does
    db.commit()
    _run(db, _msg("paid 20"))
    assert _sent(calls)[-1]["text"] == _t("signed_out") and seen == [] and _chat_link(db) is None
    _run(db, _msg("/login"))
    assert _sent(calls)[-1]["text"] == _t("ask_username")


# ─── CU7. The web chat's own handler ──────────────────────────────────────────────────────

def test_cu7_cards_are_drawn_from_their_lines_and_choices_listed(co, bot_api, db, fake_turn):
    _cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    token = str(uuid.uuid4())
    fake_turn(FakeResult(
        "Which account?",
        proposals=[{"confirmation_token": token, "tool_name": "propose_create_transaction",
                    "summary": "a first summary the card no longer says",
                    "preview": {"description": "Lunch", "date": "2026-11-20", "currency": "GBP", "lines": [
                        {"account_code": "7400", "debit": 20, "credit": 0},
                        {"account_code": "1200", "debit": 0, "credit": 20}]}}],
        tool_calls=[{"name": "ask_with_choices", "input": {}, "result": {"options": ["Cash", "Card"]}}]))
    _run(db, _msg("paid 20 for lunch"))
    words, card = _sent(calls)[-2:]
    assert words["text"] == "Which account?\n• Cash\n• Card"
    assert card["text"].startswith("📝 Lunch\n20 GBP") and "Dr 7400: 20" in card["text"] and "Cr 1200: 20" in card["text"]
    assert [b["callback_data"] for b in card["reply_markup"]["inline_keyboard"][0]] == [f"ok:{token}", f"no:{token}"]


def test_cu7_a_bare_file_path_is_refused_before_the_model(co, bot_api, db, fake_turn):
    """One of the web chat's own steps before the model: the bot runs them too."""
    _cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    seen = fake_turn(FakeResult("never"))
    _run(db, _msg("/Users/sara/Downloads/statement.pdf"))
    assert seen == [] and _sent(calls)[-1]["text"]


def test_cu7_cancel_withdraws_the_approval_it_asked_for(co, bot_api, db):
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    cid, uid = co
    _link(db, uid)
    token = _proposal(db, cid, uid)
    with use_company(cid):
        p = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalars().one()
        p.approval_status = "requested"
        db.commit()
    _tap(db, f"no:{token}")
    with use_company(cid):
        db.expire_all()
        p = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalars().one()
        assert (p.status, p.approval_status) == ("cancelled", "withdrawn")


# ─── CU8. Files in, files out ─────────────────────────────────────────────────────────────

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 300
PDF = b"%PDF-1.4\n" + b"1 0 obj << >> endobj\n" * 10 + b"%%EOF\n"


def _attachment(db, att_id):
    from app.db.tenant import tenant_bypass
    from app.models.transaction import TransactionAttachment
    with tenant_bypass():
        return db.get(TransactionAttachment, uuid.UUID(att_id))


def test_cu8_a_photo_goes_in_as_an_attachment_with_its_caption(co, bot_api, db, fake_turn):
    _cid, uid = co
    calls, _ = bot_api
    bot_api_files = messenger._TRANSPORT.handler.files
    bot_api_files["P2"] = JPEG
    _link(db, uid)
    seen = fake_turn(FakeResult("A receipt from Pret, 7.20 — lunch?"))
    photo = [{"file_id": "P1", "file_size": 90, "width": 90}, {"file_id": "P2", "file_size": len(JPEG), "width": 800}]
    _run(db, _msg(photo=photo, caption="lunch receipt"))
    assert ("getFile", {"file_id": "P2"}) in calls                 # the largest size
    turn = seen[-1]
    assert turn["user_message"] == "lunch receipt" and len(turn["attachment_ids"]) == 1
    att = _attachment(db, turn["attachment_ids"][0])
    assert (att.content_type, att.size_bytes) == ("image/jpeg", len(JPEG))
    assert _sent(calls)[-1]["text"] == "A receipt from Pret, 7.20 — lunch?"


def test_cu8_a_pdf_without_a_caption_and_what_is_refused(co, bot_api, db, fake_turn):
    _cid, uid = co
    calls, _ = bot_api
    files = messenger._TRANSPORT.handler.files
    files["D1"] = PDF
    _link(db, uid)
    seen = fake_turn(FakeResult("A bill from British Gas."))
    _run(db, _msg(document={"file_id": "D1", "file_name": "bill.pdf", "mime_type": "application/pdf",
                            "file_size": len(PDF)}))
    assert seen[-1]["user_message"] == "" and _attachment(db, seen[-1]["attachment_ids"][0]).file_name == "bill.pdf"
    n = len(seen)
    _run(db, _msg(document={"file_id": "D2", "file_name": "big.pdf", "mime_type": "application/pdf",
                            "file_size": messenger.MAX_FILE_BYTES + 1}))
    assert _sent(calls)[-1]["text"] == _t("file_too_large") and ("getFile", {"file_id": "D2"}) not in calls
    files["X1"] = b"MZ" + b"\x00" * 100
    _run(db, _msg(document={"file_id": "X1", "file_name": "run.exe", "mime_type": "application/x-msdownload",
                            "file_size": 102}))
    assert "type" in _sent(calls)[-1]["text"].lower() and len(seen) == n     # not the assistant's to read


def _journal_block(db, cid):
    from app.db.tenant import use_company
    from app.services.ai_accountant.files_out import journal_file
    with use_company(cid):
        return journal_file(db, date(2026, 1, 1), date(2026, 3, 31))


def test_cu8_the_journal_comes_back_as_a_document(co, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    block = _journal_block(db, cid)
    assert block["path"] == "/api/mobile/v1/documents/journal?from_date=2026-01-01&to_date=2026-03-31"
    fake_turn(FakeResult("Here is the journal for the first quarter.",
                         tool_calls=[{"name": "get_file", "input": {"kind": "journal_excel"},
                                      "result": {"kind": "journal_excel", "file": block}}]))
    _run(db, _msg("send me the journal in excel for Q1"))
    assert _sent(calls)[-1]["text"] == "Here is the journal for the first quarter."
    [upload] = [b for m, b in calls if m == "sendDocument"]
    body = upload["_multipart"]
    assert b'name="document"' in body and block["name"].encode() in body and b"PK" in body   # the .xlsx itself


def test_cu8_through_the_relay_files_come_and_go_with_their_bytes(client, co, relay, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    out = _start(db, uid)
    _relay(client, relay, "update", {"update": _msg(f"/start {out['code']}", update_id=4600)})
    seen = fake_turn(FakeResult("A receipt — lunch?"))
    photo = [{"file_id": "R1", "file_size": len(JPEG), "width": 800}]
    r = client.post("/bots/telegram/relay/update", headers={"Authorization": f"Bearer {relay}"},
                    data={"update": json.dumps(_msg(photo=photo, caption="lunch", update_id=4601))},
                    files={"f:R1": ("blob", JPEG, "application/octet-stream")})
    assert r.status_code == 200 and [c["params"]["text"] for c in r.json()["calls"]] == ["A receipt — lunch?"]
    assert _attachment(db, seen[-1]["attachment_ids"][0]).size_bytes == len(JPEG)
    block = _journal_block(db, cid)
    fake_turn(FakeResult("Here it is.", tool_calls=[{"name": "get_file", "input": {}, "result": {"file": block}}]))
    r = _relay(client, relay, "update", {"update": _msg("journal please", update_id=4602)})
    doc = next(c for c in r.json()["calls"] if c["method"] == "sendDocument")
    assert doc["params"]["chat_id"] == "5550001" and doc["files"]["document"]["name"] == block["name"]
    assert base64.b64decode(doc["files"]["document"]["b64"]).startswith(b"PK")
    assert calls == []                                            # the server never called the platform itself


def test_cu8_get_file_hands_over_the_journal_and_the_close_pack(co, db):
    import asyncio

    from app.core.auth import SessionUser
    from app.core.request_context import clear_current_user, set_current_user
    from app.db.tenant import use_company
    from app.services.ai_accountant import blocks as B
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.files_out import GetFile, GetFileInput, file_bytes
    cid, uid = co
    tool = GetFile()
    with use_company(cid):
        set_current_user(SessionUser(user_id=uid, username="o", is_admin=True, company_id=cid, role="owner"))
        try:
            ctx = ToolContext(db=db, user_id=uid)
            out = asyncio.run(tool.run(ctx, GetFileInput(kind="journal_excel", from_date=date(2026, 4, 1),
                                                         to_date=date(2026, 6, 30))))
            assert out["file"]["id"] == "file:journal:2026-04-01:2026-06-30"
            assert out["file"]["web_path"].startswith("/manager-reports/books/general-journal/export?")
            [block] = B.data_blocks([{"name": "get_file", "result": out}])
            assert block["type"] == "file" and block["tool"] == "get_file"
            name, mime, data = file_bytes(db, block)
            assert name.endswith(".xlsx") and data[:2] == b"PK"
            pack = asyncio.run(tool.run(ctx, GetFileInput(kind="close_pack", month="2026-09", format="pdf")))
            assert pack["file"]["id"] == "file:close-pack:2026-09:pdf" and pack["file"]["mime"] == "application/pdf"
            set_current_user(SessionUser(user_id=uid, username="e", is_admin=False, company_id=cid, role="employee"))
            with pytest.raises(ToolError, match="can't open"):
                asyncio.run(tool.run(ctx, GetFileInput(kind="journal_excel")))
        finally:
            clear_current_user()


def test_cu8_the_phone_downloads_the_same_files(client, co, db):
    from tests.test_messenger_bot import _login
    cid, uid = co
    api = _login(client, cid, uid)
    r = api.get("/api/mobile/v1/documents/journal", params={"from_date": "2026-01-01", "to_date": "2026-03-31"})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert api.get("/api/mobile/v1/documents/close-pack", params={"month": "2026-09", "format": "xlsx"}).status_code == 200


def test_cu8_get_file_is_routed_for_a_company_not_a_person():
    from app.services.ai_accountant.tool_routing import route
    assert "files" in route("دفتر روزنامه رو اکسل بفرست") and "files" in route("send me the close pack for August")
    assert "files" not in route("send me the journal in excel", personal=True)


# ─── CU9. An update is handled to the end, or again ──────────────────────────────────────────

def test_cu9_an_update_that_failed_is_handled_on_the_next_try(co, bot_api, db, monkeypatch):
    calls, _ = bot_api
    real = messenger._on_message
    failures = [RuntimeError("the server stopped half way")]

    async def flaky(*a, **kw):
        if failures:
            raise failures.pop()
        return await real(*a, **kw)
    monkeypatch.setattr(messenger, "_on_message", flaky)
    update = _msg("/start", update_id=777001)
    with pytest.raises(RuntimeError):
        _run(db, update)
    assert _run(db, update) is not None and "/login" in _sent(calls)[-1]["text"]
    assert _run(db, update) is None                               # handled to the end: not again


def test_cu9_one_begun_long_ago_and_never_finished_is_handled_again(co, bot_api, db):
    calls, _ = bot_api
    now = datetime.now(timezone.utc)
    db.add(MessengerUpdate(platform="telegram", update_id=777002, received_at=now - timedelta(minutes=11)))
    db.add(MessengerUpdate(platform="telegram", update_id=777003, received_at=now))     # being handled right now
    db.commit()
    assert _run(db, _msg("/start", update_id=777002)) is not None
    assert _run(db, _msg("/start", update_id=777003)) is None

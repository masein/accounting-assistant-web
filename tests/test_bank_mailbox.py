"""Bank statements by e-mail (roadmap 2026-09 §4.1, part 2).

The mailbox is a fake IMAP server: nothing here resolves a real name or
opens a socket. What is pinned: the password is stored encrypted and never
returned, nor sent to another server; only the owner (or a personal user)
sets it up; only allowed senders are read; the folder is opened read-only and
nothing is ever written to it; each message is imported once; statements
land for review (``origin="email"``, nothing posted); failures become codes,
and a mailbox that keeps failing rings the bell; the connection goes to the
checked public address with the certificate checked against the name.
"""
from __future__ import annotations

import imaplib
import re
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import make_msgid

import pytest

from app.services import statement_mailbox as mb

HOST = "imap.bank-mail.example"
PASSWORD = "app-pass-" + uuid.uuid4().hex[:8]
MELLAT = "statements@bankmellat.ir"
CSV_A = b"Date,Description,Amount\n2026-09-20,POS SNAPP TEHRAN,-250000\n2026-09-21,Salary September,90000000\n"
CSV_B = b"Date,Description,Amount\n2026-09-22,Card fee,-12000\n2026-09-23,Transfer from Aria,4500000\n"
WRITES = {"STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "DELETE", "CREATE", "RENAME"}


def _mail(sender, subject, files=(), *, message_id=None):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "books@example.com"
    m["Subject"] = subject
    m["Date"] = "Mon, 28 Sep 2026 10:00:00 +0330"
    m["Message-ID"] = message_id or make_msgid(domain="bank.example")
    m.set_content("Your statement is attached.")
    for name, data, mime in files:
        maintype, subtype = mime.split("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m.as_bytes(policy=SMTP)


class FakeIMAP:
    """Enough of imaplib.IMAP4 for the reader, recording every command."""

    def __init__(self, messages, *, password=PASSWORD, folders=("INBOX",)):
        self.messages = {i + 1: raw for i, raw in enumerate(messages)}
        self.password, self.folders, self.calls = password, folders, []

    def login(self, user, password):
        self.calls.append(("LOGIN", user))
        if password != self.password:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"Logged in"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("SELECT", mailbox, readonly))
        if mailbox.strip('"') not in self.folders:
            return "NO", [b"Mailbox doesn't exist"]
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        self.calls.append(("UID", command) + tuple(a for a in args if a is not None))
        if command == "SEARCH":
            froms = re.findall(r'FROM "([^"]+)"', " ".join(a for a in args if a))
            hits = [u for u, raw in self.messages.items()
                    if not froms or any(f in _from(raw) for f in froms)]
            return "OK", [" ".join(map(str, hits)).encode()]
        if command == "FETCH":
            uid, what = int(args[0]), args[1]
            raw = self.messages[uid]
            if "HEADER.FIELDS" in what:
                head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                return "OK", [(f"1 (UID {uid} RFC822.SIZE {len(raw)} BODY[HEADER.FIELDS] {{{len(head)}}}".encode(),
                               head), b")"]
            return "OK", [(f"1 (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        raise AssertionError(f"unexpected UID {command}")

    def __getattr__(self, name):                   # store/copy/expunge/… would be a write
        raise AssertionError(f"the reader called {name}")

    def logout(self):
        self.calls.append(("LOGOUT",))
        return "BYE", [b""]


def _from(raw: bytes) -> str:
    m = re.search(rb"^From: (.*)$", raw, re.M)
    return m.group(1).decode().lower() if m else ""


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    table = {HOST: ["93.184.216.34"], "imap.other.example": ["93.184.216.35"], "internal.example": ["10.0.0.5"]}

    def resolve(host, port):
        if host not in table:
            raise OSError("no such host")
        return table[host]
    monkeypatch.setattr(mb, "_resolve", resolve)


def _session(client, cid, role):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username=role, is_admin=role == "owner", role=role, company_id=cid))
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    _api, cid = _company(client, db, "ir", "IRR")
    yield (lambda role="owner": _session(client, cid, role)), cid
    client.cookies.clear()
    _purge_company(db, cid)


SETUP = {"enabled": True, "host": HOST, "port": 993, "username": "books@example.com", "password": PASSWORD,
         "folder": "INBOX", "senders": f"{MELLAT}, Mellat\n@bank-saman.ir, Saman"}


def _setup(api, **over):
    r = api().put("/bank-mailbox", json={**SETUP, **over})
    assert r.status_code == 200, r.text
    return r.json()


def _serve(monkeypatch, fake):
    monkeypatch.setattr(mb, "_connect", lambda host, port: fake)
    return fake


# ─── settings ─────────────────────────────────────────────────────────────

def test_the_password_is_stored_encrypted_and_never_returned(co, db):
    from sqlalchemy import select

    from app.db.tenant import use_company
    from app.models.app_setting import AppSetting
    api, cid = co
    out = _setup(api)
    assert out["has_password"] is True and "password" not in out and out["can_manage"] is True
    assert out["senders"] == [{"match": MELLAT, "bank_name": "Mellat"}, {"match": "@bank-saman.ir", "bank_name": "Saman"}]
    got = api().get("/bank-mailbox").json()
    assert "password" not in got and PASSWORD not in str(got)
    with use_company(cid):
        raw = db.execute(select(AppSetting.value).where(AppSetting.key == mb.SETTINGS_KEY)).scalar()
    assert PASSWORD not in raw                                                       # Fernet token, not the text
    # a blank password keeps the saved one…
    assert _setup(api, password="")["has_password"] is True
    # …but a new account needs its own: the saved one is never carried to it
    r = api().put("/bank-mailbox", json={**SETUP, "username": "other@example.com", "password": ""})
    assert r.status_code == 422 and "password" in r.json()["detail"]
    assert _setup(api, username="other@example.com", password="", enabled=False)["has_password"] is False


@pytest.mark.parametrize("over, words", [
    ({"host": "bad host"}, "server name"), ({"port": 0}, "port"), ({"username": "two words"}, "username"),
    ({"folder": 'In"box'}, "folder"), ({"senders": "not-an-address, X"}, "isn't an e-mail"),
    # the match goes into SEARCH FROM "…": nothing that could close the quote
    ({"senders": 'a"b@bank.ir, X'}, "isn't an e-mail"), ({"senders": "a\\b@bank.ir, X"}, "isn't an e-mail"),
    ({"senders": "(x)@bank.ir, X"}, "isn't an e-mail"),
    ({"senders": "\n".join(f"s{i}@bank.ir, B" for i in range(21))}, "At most"),
    ({"host": "internal.example"}, "not a public address"), ({"host": "nowhere.example"}, "can't resolve"),
    ({"senders": ""}, "at least one sender"), ({"password": "a\r\nb"}, "password"),
])
def test_bad_settings_are_refused(co, over, words):
    api, _ = co
    r = api().put("/bank-mailbox", json={**SETUP, **over})
    assert r.status_code == 422 and words in r.json()["detail"], (over, r.text)


def test_only_the_owner_or_a_personal_user_sets_it_up(co):
    from app.core.permissions import ROLE_PERMISSIONS, Perm, role_can
    api, _ = co
    _setup(api)
    assert sorted(r for r in ROLE_PERMISSIONS if role_can(r, Perm.BANK_MAIL)) == ["owner", "personal"]
    acc = api("accountant")
    got = acc.get("/bank-mailbox")
    assert got.status_code == 200 and got.json()["can_manage"] is False and got.json()["host"] == HOST
    assert acc.put("/bank-mailbox", json=SETUP).status_code == 403
    assert api("accountant").post("/bank-mailbox/test", json={}).status_code == 403
    assert api("cfo").put("/bank-mailbox", json=SETUP).status_code == 403
    for role in ("viewer", "employee", "manager"):
        assert api(role).get("/bank-mailbox").status_code == 403, role
        assert api(role).get("/bank-mailbox/messages").status_code == 403, role


# ─── reading the mailbox ─────────────────────────────────────────────────────

def _inbox():
    return [
        _mail(f"Bank Mellat <{MELLAT}>", "صورتحساب شهریور", [("mellat-1405-06.csv", CSV_A, "text/csv")]),
        _mail("Someone <someone@else.example>", "Not a bank", [("list.csv", CSV_B, "text/csv")]),
        _mail("Saman <noreply@mail.bank-saman.ir>", "Statement", [("saman.csv", CSV_B, "text/csv")]),
        _mail(f"Bank Mellat <{MELLAT}>", "Your OTP", []),
        _mail(f"Bank Mellat <{MELLAT}>", "Notice", [("notice.html", b"<b>hi</b>", "text/html")]),
    ]


def test_a_check_files_the_banks_statements_for_review(co, db, monkeypatch):
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatement
    api, cid = co
    _setup(api)
    fake = _serve(monkeypatch, FakeIMAP(_inbox()))
    r = api("accountant").post("/bank-mailbox/check")                     # books:write may check now
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["found"] == 4 and out["imported"] == 2, out
    # read-only: the folder is opened read-only, nothing is written to the mailbox
    assert ("SELECT", '"INBOX"', True) in fake.calls
    assert not [c for c in fake.calls if c[0] in WRITES or (c[0] == "UID" and c[1] in WRITES)]
    search = next(c for c in fake.calls if c[:2] == ("UID", "SEARCH"))
    assert 'FROM "bankmellat.ir"' in " ".join(search) or f'FROM "{MELLAT}"' in " ".join(search)
    assert 'FROM "bank-saman.ir"' in " ".join(search)                     # filtered on the server
    # the someone@else message was never opened
    opened = [c[2] for c in fake.calls if c[:2] == ("UID", "FETCH") and c[3] == "(BODY.PEEK[])"]
    assert b"2" not in opened and 2 not in opened
    with use_company(cid):
        stmts = db.query(BankStatement).filter(BankStatement.origin == "email").all()
        assert sorted(s.bank_name for s in stmts) == ["Mellat", "Saman"]
        assert {s.status for s in stmts} == {"parsed"}                     # waiting for review, nothing posted
        assert sorted(s.source_filename for s in stmts) == ["mellat-1405-06.csv", "saman.csv"]
    log = api().get("/bank-mailbox/messages").json()["messages"]
    assert sorted(m["status"] for m in log) == ["imported", "imported", "no_attachment", "no_attachment"]
    assert all(m["sender"] != "someone@else.example" for m in log)
    assert {m["subject"] for m in log if m["status"] == "imported"} == {"صورتحساب شهریور", "Statement"}
    status = api().get("/bank-mailbox").json()["status"]
    assert status["ok"] is True and status["imported"] == 2 and status["failures"] == 0


def test_nothing_is_imported_twice(co, db, monkeypatch):
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatement
    api, cid = co
    _setup(api)
    inbox = _inbox()
    _serve(monkeypatch, FakeIMAP(inbox))
    assert api().post("/bank-mailbox/check").json()["imported"] == 2
    again = api().post("/bank-mailbox/check").json()
    assert again["found"] == 0 and again["imported"] == 0                 # every message already read
    # the same file sent again in a new e-mail is the upload's duplicate check
    _serve(monkeypatch, FakeIMAP(inbox + [_mail(MELLAT, "Resent", [("again.csv", CSV_A, "text/csv")])]))
    third = api().post("/bank-mailbox/check").json()
    assert third["found"] == 1 and third["imported"] == 0
    assert api().get("/bank-mailbox/messages").json()["messages"][0]["status"] == "duplicate"
    with use_company(cid):
        assert db.query(BankStatement).filter(BankStatement.origin == "email").count() == 2


def test_big_messages_and_a_missing_message_id(co, monkeypatch):
    api, _ = co
    _setup(api)
    no_id = _mail(MELLAT, "No id", [("x.csv", CSV_A, "text/csv")]).replace(b"Message-ID:", b"X-Old-ID:")
    big = _mail(MELLAT, "Big", [("big.csv", CSV_B + b"#" * 20_000, "text/csv")])
    monkeypatch.setattr(mb, "MAX_MESSAGE_BYTES", len(big) - 1)
    _serve(monkeypatch, FakeIMAP([no_id, big]))
    out = api().post("/bank-mailbox/check").json()
    assert out["found"] == 2 and out["imported"] == 1
    statuses = {m["subject"]: m["status"] for m in api().get("/bank-mailbox/messages").json()["messages"]}
    assert statuses == {"No id": "imported", "Big": "too_large"}
    assert api().post("/bank-mailbox/check").json()["found"] == 0            # the fallback key is stable


def test_failures_are_codes_and_a_mailbox_that_keeps_failing_rings_the_bell(co, db, monkeypatch):
    from sqlalchemy import select

    from app.db.tenant import use_company
    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications, visible_to
    api, cid = co
    _setup(api)
    _serve(monkeypatch, FakeIMAP([], password="changed"))

    def bell():
        with use_company(cid):
            refresh_notifications(db)
            db.commit()
            return {n.dedupe_key: n for n in db.execute(select(Notification).where(
                Notification.kind == "bank_mail", Notification.dismissed_at.is_(None))).scalars()}

    first = api().post("/bank-mailbox/check").json()
    assert first["ok"] is False and first["error_code"] == "auth" and first["failures"] == 1
    assert PASSWORD not in str(first)
    assert "bank-mail-failing" not in bell()                              # one blip isn't worth a bell
    assert api().post("/bank-mailbox/check").json()["failures"] == 2
    row = bell()["bank-mail-failing"]
    assert "username or password" in row.message
    assert visible_to(row, user_id="x", role="accountant") and not visible_to(row, user_id="x", role="viewer")
    # it works again: the count resets and the alert clears; the statements wait for review
    _serve(monkeypatch, FakeIMAP(_inbox()))
    ok = api().post("/bank-mailbox/check").json()
    assert ok["ok"] is True and ok["failures"] == 0
    rows = bell()
    assert "bank-mail-failing" not in rows
    assert rows["bank-mail-waiting"].title == "2 bank statements arrived by e-mail"
    assert rows["bank-mail-waiting"].link_page == "bank-statements"
    # once they're approved the reminder goes
    from app.models.bank_statement import BankStatement
    with use_company(cid):
        for s in db.query(BankStatement).filter(BankStatement.origin == "email"):
            s.status = "approved"
        db.commit()
    assert "bank-mail-waiting" not in bell()


def test_a_dropped_connection_is_a_failed_check_not_a_crash(co, monkeypatch):
    api, _ = co
    _setup(api)

    class Dropping(FakeIMAP):
        def uid(self, command, *args):
            if command == "FETCH":
                raise imaplib.IMAP4.abort("socket error: EOF")
            return super().uid(command, *args)
    _serve(monkeypatch, Dropping(_inbox()))
    out = api().post("/bank-mailbox/check").json()
    assert out["ok"] is False and out["error_code"] == "protocol" and out["failures"] == 1

    class Slow(FakeIMAP):
        def uid(self, command, *args):
            raise TimeoutError("timed out")
    _serve(monkeypatch, Slow(_inbox()))
    out = api().post("/bank-mailbox/check").json()
    assert out["error_code"] == "timeout" and out["failures"] == 2
    assert api().get("/bank-mailbox").json()["status"]["failures"] == 2


def test_a_password_that_no_longer_decrypts_asks_for_it_again(co, monkeypatch):
    import app.core.secrets as secrets_mod
    api, _ = co
    _setup(api)
    tried = []
    monkeypatch.setattr(mb, "_connect", lambda host, port: tried.append(host) or FakeIMAP([]))
    monkeypatch.setattr(secrets_mod, "decrypt_secret", lambda value: "")     # AUTH_SECRET rotated
    out = api().post("/bank-mailbox/check").json()
    assert out["ok"] is False and out["error_code"] == "auth" and "enter it again" in out["error"]
    assert tried == [] and out["failures"] == 1


def test_check_now_needs_a_mailbox(co):
    api, _ = co
    assert api().post("/bank-mailbox/check").status_code == 409


# ─── testing the connection ───────────────────────────────────────────────────

def test_the_connection_test_reads_and_saves_nothing(co, monkeypatch):
    api, _ = co
    _setup(api, enabled=False)
    fake = _serve(monkeypatch, FakeIMAP(_inbox()))
    out = api().post("/bank-mailbox/test", json={}).json()
    assert out == {"ok": True, "messages": 5, "folder": "INBOX"}
    assert [c[0] for c in fake.calls] == ["LOGIN", "SELECT", "LOGOUT"]
    assert api().post("/bank-mailbox/test", json={"folder": "Statements"}).json()["error_code"] == "folder"
    assert api().post("/bank-mailbox/test", json={"password": "wrong"}).json()["error_code"] == "auth"
    assert api().get("/bank-mailbox").json()["folder"] == "INBOX"             # the form wasn't saved


def test_the_saved_password_never_goes_to_another_server(co, monkeypatch):
    api, _ = co
    _setup(api, enabled=False)
    tried = []
    monkeypatch.setattr(mb, "_connect", lambda host, port: tried.append(host) or FakeIMAP([]))
    out = api().post("/bank-mailbox/test", json={"host": "imap.other.example"}).json()
    assert out["ok"] is False and out["error_code"] == "auth" and tried == []
    out = api().post("/bank-mailbox/test", json={"username": "someone@other.example"}).json()
    assert out["ok"] is False and tried == []


def test_only_public_addresses_and_the_checked_one(monkeypatch):
    with pytest.raises(mb.MailboxError) as e:
        mb._connect("internal.example", 993)
    assert e.value.code == "private"
    with pytest.raises(mb.MailboxError) as e:
        mb._connect("nowhere.example", 993)
    assert e.value.code == "resolve"
    made = {}

    class Pinned:
        def __init__(self, host, port, *, address, ssl_context, timeout):
            made.update(host=host, port=port, address=address, verify=ssl_context.verify_mode)
    monkeypatch.setattr(mb, "_PinnedIMAP", Pinned)
    mb._connect(HOST, 993)
    import ssl
    assert made == {"host": HOST, "port": 993, "address": "93.184.216.34", "verify": ssl.CERT_REQUIRED}


def test_the_socket_goes_to_the_checked_address_with_the_name_on_the_certificate(monkeypatch):
    import socket
    seen = {}
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: seen.setdefault("addr", addr) and "sock")

    class Ctx:
        def wrap_socket(self, sock, server_hostname=None):
            seen["sni"] = server_hostname
            return sock
    conn = object.__new__(mb._PinnedIMAP)
    conn.host, conn.port, conn._address, conn.ssl_context = HOST, 993, "93.184.216.34", Ctx()
    assert conn._create_socket(5) == "sock"
    assert seen == {"addr": ("93.184.216.34", 993), "sni": HOST}


# ─── the pieces ───────────────────────────────────────────────────────────────

def test_sender_rules_and_the_search():
    rules = mb.parse_senders(f"{MELLAT.upper()}, Mellat\n@bank-saman.ir, Saman\n{MELLAT}, dup")
    assert rules == [{"match": MELLAT, "bank_name": "Mellat"}, {"match": "@bank-saman.ir", "bank_name": "Saman"}]
    assert mb.parse_senders("@bmi.ir، بانک ملی") == [{"match": "@bmi.ir", "bank_name": "بانک ملی"}]   # Persian comma
    assert mb.sender_rule(MELLAT, rules)["bank_name"] == "Mellat"
    assert mb.sender_rule("x@bank-saman.ir", rules)["bank_name"] == "Saman"
    assert mb.sender_rule("x@mail.bank-saman.ir", rules)["bank_name"] == "Saman"
    assert mb.sender_rule("x@evilbank-saman.ir", rules) is None               # a suffix isn't the domain
    assert mb.sender_rule("other@bankmellat.ir", rules) is None
    from datetime import date
    assert mb.search_criteria(date(2026, 9, 3), rules[:1]) == ["SINCE", "03-Sep-2026", f'FROM "{MELLAT}"']
    three = mb.search_criteria(date(2026, 1, 15), rules + [{"match": "@bmi.ir", "bank_name": ""}])
    assert three == ["SINCE", "15-Jan-2026",
                     f'OR FROM "{MELLAT}" OR FROM "bank-saman.ir" FROM "bmi.ir"']


def test_how_far_back_a_check_reads():
    from datetime import date
    today = date(2026, 9, 29)
    assert mb._since({}, today) == date(2026, 9, 15)
    assert mb._since({"checked_at": "2026-09-28T10:00:00+00:00"}, today) == date(2026, 9, 26)


def test_the_job_reads_only_mailboxes_that_are_on(co, db, monkeypatch):
    from app.db.tenant import use_company
    from app.jobs import scheduler as sched
    api, cid = co
    _setup(api, enabled=False)
    calls = []
    monkeypatch.setattr(mb, "check_mailbox", lambda db, now=None: calls.append(1) or {"ok": True, "found": 0,
                                                                                      "imported": 0})
    with use_company(cid):
        assert sched.job_bank_mailbox(db, datetime.now(timezone.utc).date()) == {}
    _setup(api)
    with use_company(cid):
        assert sched.job_bank_mailbox(db, datetime.now(timezone.utc).date())["ok"] is True
    assert calls == [1]


def _pdf(tag: str) -> bytes:
    import io

    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.add_metadata({"/Title": tag})                                  # distinct bytes: not a duplicate
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_a_daily_limit_keeps_the_ai_allowance_for_the_company(co, db, monkeypatch):
    """PDFs and images are read by the AI model from the company's allowance: a
    flood of forged "bank" mail must not use it up and stop the chat for the
    day. Over the limit a message isn't logged — tomorrow's check reads it.
    CSV and Excel files cost nothing and are never held back."""
    from datetime import timedelta

    import app.services.ocr_extract as ocr
    from app.db.tenant import use_company
    api, cid = co
    _setup(api)
    monkeypatch.setattr(mb, "MAX_AI_READS_PER_DAY", 2)
    reads = []

    async def rows(path, content_type):
        reads.append(path)
        return [{"date": "2026-09-20", "description": f"POS {len(reads)}", "amount": 1000 * len(reads),
                 "balance": None, "direction": "debit"}]
    monkeypatch.setattr(ocr, "extract_statement_rows", rows)
    inbox = [_mail(MELLAT, f"PDF {i}", [(f"s{i}.pdf", _pdf(f"s{i}"), "application/pdf")]) for i in range(3)]
    inbox.append(_mail(MELLAT, "CSV", [("s.csv", CSV_A, "text/csv")]))
    _serve(monkeypatch, FakeIMAP(inbox))
    day1 = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    with use_company(cid):
        out = mb.check_mailbox(db, now=day1)
    assert (out["imported"], out["deferred"], out["ai_reads"], len(reads)) == (3, 1, 2, 2), out
    with use_company(cid):                                             # later the same day: still waiting
        out = mb.check_mailbox(db, now=day1 + timedelta(hours=2))
    assert (out["imported"], out["deferred"], len(reads)) == (0, 1, 2), out
    assert api().get("/bank-mailbox").json()["status"]["deferred"] == 1
    with use_company(cid):                                             # tomorrow's allowance reads it
        out = mb.check_mailbox(db, now=day1 + timedelta(days=1))
    assert (out["imported"], out["deferred"], out["ai_reads"], len(reads)) == (1, 0, 1, 3), out
    assert sorted(m["subject"] for m in api().get("/bank-mailbox/messages").json()["messages"]) == \
        ["CSV", "PDF 0", "PDF 1", "PDF 2"]

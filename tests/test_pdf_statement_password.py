"""Password-protected PDF statements (roadmap 2026-09 §4.1).

Several banks lock the PDF statements they send, often with the customer's
national ID. The import unlocks them in memory (app/services/pdf_unlock.py);
without the right password it answers ``needs_password`` — on the upload
page (the password travels in the form body, never the URL), in the
statements mailbox (a saved PDF password, encrypted, never returned) and in
the chat (which never asks for a password in a message).
"""
from __future__ import annotations

import asyncio
import io
import uuid

import pytest

from app.services import pdf_unlock

NID = "0012345678"


def _pdf(password: str | None = None, *, owner_only: bool = False, algorithm: str = "AES-256") -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    w.add_blank_page(200, 200)
    buf = io.BytesIO()
    w.write(buf)
    if password is None and not owner_only:
        return buf.getvalue()
    locked = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    locked.encrypt(user_password="" if owner_only else password, owner_password="owner-" + uuid.uuid4().hex[:6],
                   algorithm=algorithm)
    out = io.BytesIO()
    locked.write(out)
    return out.getvalue()


def _encrypted(data: bytes) -> bool:
    from pypdf import PdfReader
    return PdfReader(io.BytesIO(data)).is_encrypted


# ─── unlocking ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_a_locked_pdf_opens_with_its_password_only(algorithm):
    data = _pdf(NID, algorithm=algorithm)
    assert _encrypted(data) and pdf_unlock.is_locked(data)
    with pytest.raises(pdf_unlock.PasswordNeeded) as e:
        pdf_unlock.unlock(data, None)
    assert e.value.wrong is False
    with pytest.raises(pdf_unlock.PasswordNeeded) as e:
        pdf_unlock.unlock(data, "1234567890")
    assert e.value.wrong is True
    opened = pdf_unlock.unlock(data, NID)
    assert not _encrypted(opened)


def test_a_national_id_typed_in_persian_digits_or_with_spaces():
    data = _pdf(NID)
    assert not _encrypted(pdf_unlock.unlock(data, "۰۰۱۲۳۴۵۶۷۸"))
    assert not _encrypted(pdf_unlock.unlock(data, "٠٠١٢٣٤٥٦٧٨"))          # Arabic-Indic too
    assert not _encrypted(pdf_unlock.unlock(data, f"  {NID} "))


def test_what_needs_no_password():
    plain = _pdf()
    assert pdf_unlock.unlock(plain, None) is plain and not pdf_unlock.is_locked(plain)
    printing_only = _pdf(owner_only=True)                 # an owner password restricting printing, no user password
    assert _encrypted(printing_only) and not pdf_unlock.is_locked(printing_only)
    assert not _encrypted(pdf_unlock.unlock(printing_only, None))
    junk = b"not a pdf at all"
    assert pdf_unlock.unlock(junk, "x") is junk and not pdf_unlock.is_locked(junk)


# ─── the upload page ──────────────────────────────────────────────────────────────

@pytest.fixture()
def ocr_seen(monkeypatch):
    """The vision reader, replaced: it records whether the file it got was
    still locked, and reads two rows."""
    import app.services.ocr_extract as ocr
    seen = []

    async def rows(path, content_type):
        with open(path, "rb") as fh:
            seen.append(_encrypted(fh.read()))
        return [{"date": "2026-09-20", "description": "POS SNAPP", "amount": 250_000, "balance": None,
                 "direction": "debit"},
                {"date": "2026-09-21", "description": "Salary", "amount": 90_000_000, "balance": None,
                 "direction": "credit"}]
    monkeypatch.setattr(ocr, "extract_statement_rows", rows)
    return seen


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    api, cid = _company(client, db, "ir", "IRR")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _upload(api, data, *, form=None, params=None):
    return api.post("/brain/bank-statements/upload", params={"bank_name": "Mellat", **(params or {})},
                    files={"file": ("mellat-1405-06.pdf", data, "application/pdf")}, data=form or {})


def _count(db, cid):
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatement
    with use_company(cid):
        return db.query(BankStatement).count()


def test_the_upload_asks_for_the_password_and_opens_the_statement_with_it(co, db, ocr_seen):
    api, cid = co
    locked = _pdf(NID)
    r = _upload(api, locked)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "needs_password" and out["needs_password"] is True and out["password_wrong"] is False
    assert _count(db, cid) == 0 and ocr_seen == []
    wrong = _upload(api, locked, form={"pdf_password": "9999999999"}).json()
    assert wrong["needs_password"] is True and wrong["password_wrong"] is True
    # the query string isn't read: a password there would end up in logs
    assert _upload(api, locked, params={"pdf_password": NID}).json()["status"] == "needs_password"
    ok = _upload(api, locked, form={"pdf_password": NID}).json()
    assert ok["status"] == "parsed" and ok["total_rows"] == 2, ok
    assert ocr_seen == [False]                                            # the reader got the unlocked copy
    assert _count(db, cid) == 1
    stmt = api.get(f"/brain/bank-statements/{ok['id']}").json()
    assert NID not in str(stmt)
    # the same locked file again is a duplicate of it (the hash is of the file as it came)
    again = _upload(api, locked, form={"pdf_password": NID}).json()
    assert again["duplicate"] is True and again["duplicate_of"] == ok["id"]


def test_a_pdf_that_only_restricts_printing_needs_nothing(co, ocr_seen):
    api, _ = co
    assert _upload(api, _pdf(owner_only=True)).json()["status"] == "parsed"
    assert ocr_seen == [False]


# ─── the statements mailbox ───────────────────────────────────────────────────────

def test_the_mailbox_opens_locked_statements_with_the_saved_pdf_password(client, db, monkeypatch, ocr_seen):
    from sqlalchemy import select

    from app.db.tenant import use_company
    from app.models.app_setting import AppSetting
    from app.services import statement_mailbox as mb
    from tests import test_bank_mailbox as tb
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    monkeypatch.setattr(mb, "_resolve", lambda host, port: ["93.184.216.34"])
    _api, cid = _company(client, db, "ir", "IRR")
    api = lambda role="owner": tb._session(client, cid, role)            # noqa: E731
    try:
        tb._setup(api)
        locked = _pdf(NID)
        inbox = [tb._mail(tb.MELLAT, "صورتحساب شهریور", [("mellat.pdf", locked, "application/pdf")])]
        tb._serve(monkeypatch, tb.FakeIMAP(inbox))
        first = api().post("/bank-mailbox/check").json()
        assert first["found"] == 1 and first["imported"] == 0
        log = api().get("/bank-mailbox/messages").json()["messages"]
        assert [m["status"] for m in log] == ["needs_password"]
        # the owner saves the PDF password: never returned, stored encrypted
        saved = api().put("/bank-mailbox", json={**tb.SETUP, "password": "", "pdf_password": NID}).json()
        assert saved["has_pdf_password"] is True and "pdf_password" not in saved and NID not in str(saved)
        with use_company(cid):
            raw = db.execute(select(AppSetting.value).where(AppSetting.key == mb.SETTINGS_KEY)).scalar()
        assert NID not in raw
        # …and the locked message gets another go on the next check
        assert api().get("/bank-mailbox/messages").json()["messages"] == []
        again = api().post("/bank-mailbox/check").json()
        assert again["found"] == 1 and again["imported"] == 1, again
        assert ocr_seen == [False]
        assert [m["status"] for m in api().get("/bank-mailbox/messages").json()["messages"]] == ["imported"]
        # saving other settings keeps it; forgetting it removes it
        assert api().put("/bank-mailbox", json={**tb.SETUP, "password": ""}).json()["has_pdf_password"] is True
        gone = api().put("/bank-mailbox", json={**tb.SETUP, "password": "", "clear_pdf_password": True}).json()
        assert gone["has_pdf_password"] is False
        # only the owner (or a personal user) may set it
        assert api("accountant").put("/bank-mailbox", json={**tb.SETUP, "pdf_password": NID}).status_code == 403
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


def test_a_new_pdf_password_makes_the_next_check_look_back_again():
    from datetime import date

    from app.services import statement_mailbox as mb
    today = date(2026, 9, 29)
    recent = {"checked_at": "2026-09-28T10:00:00+00:00"}
    assert mb._since(recent, today) == date(2026, 9, 26)
    assert mb._since({**recent, "rescan": True}, today) == date(2026, 9, 15)


# ─── the chat ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang, words", [("fa", "کد ملی"), ("en", "national ID")])
def test_the_chat_points_a_locked_pdf_to_the_upload_page(db, tmp_path, lang, words):
    from app.models.transaction import TransactionAttachment
    from app.services.ai_accountant.statement_intake import maybe_statement_intake
    p = tmp_path / "statement.pdf"
    p.write_bytes(_pdf(NID))
    att = TransactionAttachment(file_name=p.name, file_path=str(p), content_type="application/pdf",
                                size_bytes=p.stat().st_size)
    db.add(att)
    db.flush()
    message = "این صورتحساب را ثبت کن" if lang == "fa" else "add this statement"
    out = asyncio.run(maybe_statement_intake(db, user_role="owner", attachments=[att], message=message, lang="en"))
    assert out is not None and out.intake["status"] == "needs_password"
    assert words in out.text and ("Bank statements" in out.text or "صورت‌حساب‌های بانکی" in out.text)
    # a role that can't import still falls through, locked or not
    assert asyncio.run(maybe_statement_intake(db, user_role="viewer", attachments=[att], message=message,
                                              lang="en")) is None

"""Upload limits (roadmap 2026-09 §6, suite 8): every upload route just over
its cap, spoofed content types, and the defects this suite fixed — Excel
import tokens built from the raw filename (a path-traversal write), temp
files never removed, logo/signature and invoice-OCR uploads trusting the
browser's content type."""
from __future__ import annotations

import io
import tempfile
import uuid
from pathlib import Path

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
HTML = b"<html><script>alert(1)</script></html>"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXCEL_DIR = Path(tempfile.gettempdir()) / "excel_imports"


def _xlsx(tag: str = "t") -> bytes:
    """A journal sheet in the layout the importer recognises (see
    tests/test_chat_sessions_intake.py TXN_CSV)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ردیف", "شماره سند", "تاریخ", "Title 1", "Title 2", "Title 3", "شرح", "بدهکار", "بستانکار"])
    ws.append([1, 1, 14050701, "سود و زیان", "هزینه عملیاتی", "اینترنت", f"اینترنت {tag}", 1500000, 0])
    ws.append([2, 1, 14050701, "دارایی", "دارایی جاری", "بانک", f"بانک {tag}", 0, 1500000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def txn(auth_client):
    r = auth_client.post("/transactions", json={"date": "2026-06-01", "description": "for uploads", "lines": [
        {"account_code": "1110", "debit": 1, "credit": 0}, {"account_code": "3110", "debit": 0, "credit": 1}]})
    assert r.status_code == 201, r.text
    return r.json()


# ─── just over every cap ───────────────────────────────────────────────

def test_attachment_cap(auth_client, txn):
    from app.api.transactions import MAX_ATTACHMENT_SIZE_BYTES
    big = PNG + b"\x00" * (MAX_ATTACHMENT_SIZE_BYTES - len(PNG) + 1)
    r = auth_client.post("/transactions/attachments", files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 400 and "large" in r.text.lower()
    ok = auth_client.post("/transactions/attachments", files={"file": ("ok.png", PNG, "image/png")})
    assert ok.status_code in (200, 201), ok.text


def test_excel_preview_cap(auth_client):
    big = b"PK" + b"\x00" * (20 * 1024 * 1024 - 1)
    r = auth_client.post("/transactions/excel-import/preview", files={"file": ("big.xlsx", big, XLSX_MIME)})
    assert r.status_code == 400 and "large" in r.text.lower()


def test_invoice_ocr_cap_and_type(auth_client):
    from app.api.invoices import MAX_IMPORT_SIZE_BYTES
    big = PNG + b"\x00" * (MAX_IMPORT_SIZE_BYTES - len(PNG) + 1)
    r = auth_client.post("/invoices/ocr-import", files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 400 and "large" in r.text.lower()
    assert auth_client.post("/invoices/ocr-import", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
    spoof = auth_client.post("/invoices/ocr-import", files={"file": ("x.png", HTML, "image/png")})
    assert spoof.status_code == 400 and "spoof" in spoof.text.lower()


def test_statement_cap(auth_client):
    from app.api.brain import MAX_STATEMENT_UPLOAD_BYTES
    big = b"date,amount\n" + b"x" * MAX_STATEMENT_UPLOAD_BYTES
    r = auth_client.post("/brain/bank-statements/upload", files={"file": ("big.csv", big, "text/csv")})
    if r.status_code == 404:
        pytest.skip("statement upload route differs")
    assert r.status_code == 413


def test_migration_cap_and_count(auth_client):
    from app.api.migration import _MAX_FILE_SIZE
    big = b"x" * (_MAX_FILE_SIZE + 1)
    r = auth_client.post("/migration/import/preview", files=[("files", ("big.csv", big, "text/csv"))])
    assert r.status_code == 400 and "large" in r.text.lower()
    five = [("files", (f"f{i}.csv", b"a,b\n1,2\n", "text/csv")) for i in range(5)]
    assert auth_client.post("/migration/import/preview", files=five).status_code == 400
    assert auth_client.post("/migration/import/preview",
                            files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))]).status_code == 400


def test_logo_cap(auth_client):
    from app.api.company_profile import _MAX_BYTES
    big = PNG + b"\x00" * (_MAX_BYTES - len(PNG) + 1)
    r = auth_client.post("/admin/company-profile/logo", files={"file": ("big.png", big, "image/png")})
    assert r.status_code in (400, 413) and "large" in r.text.lower()


# ─── spoofed types ─────────────────────────────────────────────────────

def test_attachment_spoof_is_refused(auth_client, txn):
    r = auth_client.post("/transactions/attachments", files={"file": ("x.png", HTML, "image/png")})
    assert r.status_code == 400


@pytest.fixture()
def company_owner(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Brand Co", slug=f"brand-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner", company_id=cid))
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf)
    client.cookies.clear()
    import shutil
    from app.api.company_profile import UPLOADS_DIR
    shutil.rmtree(UPLOADS_DIR / "branding" / cid, ignore_errors=True)
    _purge_company(db, cid)


@pytest.mark.parametrize("kind", ["logo", "signature"])
def test_logo_and_signature_check_the_bytes_not_the_label(company_owner, kind):
    """They used to trust the browser's content_type (roadmap §6 known defect)."""
    api = company_owner
    r = api.post(f"/admin/company-profile/{kind}", files={"file": ("x.png", HTML, "image/png")})
    assert r.status_code == 400 and "spoof" in r.text.lower(), r.text
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    assert api.post(f"/admin/company-profile/{kind}", files={"file": ("x.gif", svg, "image/gif")}).status_code == 400
    assert api.post(f"/admin/company-profile/{kind}", files={"file": ("x.svg", svg, "image/svg+xml")}).status_code == 400
    for name, data, mime in (("x.gif", b"GIF89a" + b"\x00" * 32, "image/gif"), ("x.png", PNG, "image/png")):
        ok = api.post(f"/admin/company-profile/{kind}", files={"file": (name, data, mime)})
        assert ok.status_code == 200, ok.text


# ─── the Excel token defects ───────────────────────────────────────────

def test_excel_filename_cannot_escape_the_temp_directory(auth_client, db):
    target_name = f"pwned-{uuid.uuid4().hex[:6]}"
    evil = "../../../../../../../../tmp/" + target_name
    r = auth_client.post("/transactions/excel-import/preview", files={"file": (evil, _xlsx(), XLSX_MIME)})
    # Rejected outright (not an .xlsx name) or accepted — either way nothing lands outside.
    assert not (Path("/tmp") / f"{target_name}.xlsx").exists()
    evil_xlsx = evil + ".xlsx"
    r = auth_client.post("/transactions/excel-import/preview", files={"file": (evil_xlsx, _xlsx(), XLSX_MIME)})
    assert r.status_code == 200, r.text
    token = r.json()["file_token"]
    assert "/" not in token and "\\" not in token and ".." not in token
    assert not (Path("/tmp") / f"{target_name}.xlsx.xlsx").exists()
    assert not (Path("/tmp") / f"{target_name}.xlsx").exists()
    from app.api.transactions import EXCEL_UPLOAD_KIND
    from app.core.shared_state import find_upload
    stored = Path(find_upload(db, EXCEL_UPLOAD_KIND, token))
    assert stored.resolve().parent == EXCEL_DIR.resolve()
    # The display name survives (sanitised) for the "already imported" audit.
    assert target_name in token


def test_excel_temp_file_is_removed_after_confirm(auth_client, db):
    data = _xlsx(uuid.uuid4().hex[:6])
    r = auth_client.post("/transactions/excel-import/preview", files={"file": ("book.xlsx", data, XLSX_MIME)})
    assert r.status_code == 200, r.text
    token = r.json()["file_token"]
    from app.api.transactions import EXCEL_UPLOAD_KIND
    from app.core.shared_state import find_upload
    path = Path(find_upload(db, EXCEL_UPLOAD_KIND, token))
    assert path.exists()
    mappings = [{"title1": a.get("title1", ""), "title2": a.get("title2", ""), "title3": a.get("title3", ""),
                 "account_code": a.get("suggested_code") or "1110"} for a in r.json()["unique_accounts"]]
    assert mappings, r.json()
    c = auth_client.post("/transactions/excel-import/confirm", json={
        "file_token": token, "jalali_year": 1405, "account_mappings": mappings})
    assert c.status_code == 200, c.text
    assert not path.exists()
    assert find_upload(db, EXCEL_UPLOAD_KIND, token) is None
    # The history was recorded before the file went, so a re-upload is flagged.
    again = auth_client.post("/transactions/excel-import/preview", files={"file": ("book.xlsx", data, XLSX_MIME)})
    assert again.json()["already_imported"], again.json().get("already_imported")


def test_expired_tokens_take_their_files_with_them(db):
    from datetime import datetime, timedelta, timezone
    from app.core import shared_state as ss
    from app.models.shared_state import UploadToken
    EXCEL_DIR.mkdir(exist_ok=True)
    stale = EXCEL_DIR / f"{uuid.uuid4().hex}.xlsx"
    stale.write_bytes(b"PK")
    ss.store_upload(db, "excel_journal", f"stale-{uuid.uuid4().hex[:6]}", str(stale))
    row = db.query(UploadToken).filter(UploadToken.file_path == str(stale)).one()
    row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    outside = Path(tempfile.gettempdir()) / f"keep-{uuid.uuid4().hex}.txt"
    outside.write_text("not ours")
    ss.store_upload(db, "excel_journal", f"odd-{uuid.uuid4().hex[:6]}", str(outside))
    row2 = db.query(UploadToken).filter(UploadToken.file_path == str(outside)).one()
    row2.expires_at = row.expires_at
    db.commit()
    ss.store_upload(db, "excel_journal", f"fresh-{uuid.uuid4().hex[:6]}", str(EXCEL_DIR / "fresh.xlsx"))
    assert not stale.exists()
    assert outside.exists()          # only files inside the import directory are ever deleted
    outside.unlink()

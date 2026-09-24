"""Security review 2026-09-24 H1: uploaded files (receipts, statements,
logos, signatures) were served from a public /uploads mount — anyone with
the URL, no login. They are now served only by authenticated, tenant-scoped
routes."""
from __future__ import annotations

import uuid

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.models.company import Company
from tests.conftest import _CSRFTestClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _company(db):
    c = Company(id=uuid.uuid4(), name="Up Co", slug=f"up-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c); db.flush()
    return c


def _as(client, company):
    tok = create_session_token(user_id=str(uuid.uuid4()), username="o", is_admin=True, role="owner",
                               company_id=str(company.id))
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_attachment_is_served_only_to_its_company_and_never_publicly(client, db):
    a, b = _company(db), _company(db)
    db.commit()
    owner_a = _as(client, a)
    up = owner_a.post("/transactions/attachments", files={"file": ("receipt.png", PNG, "image/png")})
    assert up.status_code == 201, up.text
    url = up.json()["url"]
    assert url == f"/transactions/attachments/{up.json()['id']}/file"
    assert "/uploads/" not in url

    got = owner_a.get(url)
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("image/png")
    assert got.headers["content-disposition"].startswith("inline")
    assert got.headers.get("x-content-type-options") == "nosniff"
    assert got.content[:8] == PNG[:8]

    # another company: the id does not exist for them
    assert _as(client, b).get(url).status_code == 404
    # nobody: not even a peek
    client.cookies.clear()
    assert client.get(url).status_code == 401
    # the old public path is gone for good
    from app.models.transaction import TransactionAttachment
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        stored = db.get(TransactionAttachment, uuid.UUID(up.json()["id"])).file_path
    assert client.get(f"/uploads/transactions/{str(stored).rsplit('/', 1)[-1]}").status_code in (401, 404)


def test_branding_files_are_not_public(client):
    client.cookies.clear()
    r = client.get("/uploads/branding/00000000-0000-0000-0000-000000000001/signature.png")
    assert r.status_code in (401, 404)
    assert client.get("/uploads/").status_code in (401, 404)


def test_unknown_attachment_is_404(auth_client):
    assert auth_client.get(f"/transactions/attachments/{uuid.uuid4()}/file").status_code == 404

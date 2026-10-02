"""The Companies console lists each company with what the super-admin needs to
read it: whether it has a logo (the page asked for every one, and each without
was a 404), its region and its kind (deep browser test, 2026-10-02, #5, #6)."""
from __future__ import annotations

import uuid
from pathlib import Path

from app.api.companies import _UPLOADS_DIR


def test_the_list_says_which_companies_have_a_logo_and_their_kind(superadmin_client, db):
    from tests.test_admin_audit import _purge_company
    r = superadmin_client.post("/admin/companies", json={"name": f"Console {uuid.uuid4().hex[:4]}", "locale": "ir",
                                                          "kind": "personal", "username": f"con-{uuid.uuid4().hex[:6]}",
                                                          "password": "Strong#Pass2026"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    logo = _UPLOADS_DIR / "branding" / cid / "logo.png"
    try:
        row = next(c for c in superadmin_client.get("/admin/companies").json() if c["id"] == cid)
        assert row["has_logo"] is False and row["kind"] == "personal" and row["locale"] == "ir"
        assert superadmin_client.get(f"/admin/companies/{cid}/logo").status_code == 404
        logo.parent.mkdir(parents=True, exist_ok=True)
        logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        row = next(c for c in superadmin_client.get("/admin/companies").json() if c["id"] == cid)
        assert row["has_logo"] is True
        assert superadmin_client.get(f"/admin/companies/{cid}/logo").status_code == 200
    finally:
        if logo.exists():
            logo.unlink()
            Path(logo.parent).rmdir()
        _purge_company(db, cid)

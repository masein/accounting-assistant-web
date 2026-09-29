"""Downloads named from the data — a Persian employee, client, invoice number —
used to answer 500: HTTP headers are latin-1 and the name went into
Content-Disposition as it was."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from urllib.parse import unquote

from app.core.http_headers import ascii_filename, content_disposition
from tests.test_payroll_lifecycle_http import _employee, _run, co  # noqa: F401 — fixture


def _real_name(header: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;]+)", header)
    return unquote(m.group(1)) if m else re.search(r'filename="([^"]*)"', header).group(1)


def test_the_header_is_latin1_and_keeps_the_real_name():
    for name in ("payslip-سارا_احمدی.pdf", "statement-شرکت آریا.pdf", "invoice-۱۴۰۵-۰۱۲.pdf", "Crème brûlée.csv"):
        h = content_disposition(name, inline=True)
        h.encode("latin-1")                                                   # what used to raise
        assert h.startswith('inline; filename="') and _real_name(h) == name
    assert ascii_filename("invoice-۱۴۰۵-۰۱۲.pdf") == "invoice-1405-012.pdf"   # Persian digits kept, as digits
    assert ascii_filename("payslip-سارا_احمدی.pdf") == "payslip-_.pdf"
    assert ascii_filename("Crème brûlée.csv") == "Creme_brulee.csv"
    # a plain name is left alone
    assert content_disposition("close-pack-1405-06.zip") == 'attachment; filename="close-pack-1405-06.zip"'
    # no way out of the quotes or onto a new header line
    h = content_disposition('a"; filename="evil.exe\r\nSet-Cookie: x=1')
    assert "\r" not in h and "\n" not in h and h.count('"') == 2           # one quoted fallback…
    assert len(h.split("; ")) == 3                                           # …and nothing after filename*
    assert content_disposition("") == 'attachment; filename="download"'


def test_persian_names_download(co):
    session, _cid = co
    api = session()
    emp = _employee(api, "سارا احمدی")
    run = _run(api).json()
    r = api.get(f"/payroll/runs/{run['id']}/payslip/{emp['id']}/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert _real_name(r.headers["content-disposition"]) == "payslip-سارا_احمدی.pdf"

    client = api.post("/entities", json={"type": "client", "name": "شرکت آریا"}).json()
    r = api.get(f"/entities/{client['id']}/statement.pdf")
    assert r.status_code == 200 and _real_name(r.headers["content-disposition"]) == "statement-شرکت_آریا.pdf"

    number = f"۱۴۰۵-{uuid.uuid4().hex[:4]}"
    inv = api.post("/invoices", json={"number": number, "kind": "sales", "status": "issued", "issue_date": "2026-09-01",
                                      "due_date": "2026-09-30", "amount": 1_000_000, "currency": "IRR",
                                      "entity_id": client["id"]})
    assert inv.status_code == 201, inv.text
    r = api.get(f"/invoices/{inv.json()['id']}/pdf")
    assert r.status_code == 200 and _real_name(r.headers["content-disposition"]) == f"invoice-{number}.pdf"


def test_no_download_header_is_built_by_hand():
    for p in Path("app/api").glob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "Content-Disposition" in line:
                assert "content_disposition(" in line, f"{p}:{i} builds Content-Disposition itself"

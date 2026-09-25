"""سامانه مودیان export (roadmap §3.1, phase 1): the 22-char tax number,
the packet builder and its readiness checks, the export/result workflow,
the confirmed-invoice lock and the 12-day deadline notifications."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.services.moadian import taxid as tx
from app.services.moadian.builder import TEHRAN, build, indatim_ms


# ─── 1. The unique tax number ──────────────────────────────────────────

def test_verhoeff_known_vectors():
    assert tx.verhoeff_digit("236") == 3            # the textbook example
    assert tx.verhoeff_valid("2363") and not tx.verhoeff_valid("2364")
    assert tx.verhoeff_digit("12345") == 1 and tx.verhoeff_valid("123451")


def _sdk_reference(memory_id: str, d: date, serial: int) -> str:
    """Transcribed from github.com/arjavand/moadian utils/unique_tax_id.py
    (an independent implementation) to pin conformance."""
    days = round(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() / 86400)
    date_utf8 = f"{days:06d}"
    date_hex = ("0" + "0" * (4 - len(hex(days)[2:])) + hex(days)[2:]).upper()
    serial_norm = f"{serial:012d}"
    serial_hex = format(int(serial_norm), "010X")
    utf8 = "".join(str(ord(c)) if not c.isnumeric() else c for c in memory_id) + date_utf8 + serial_norm
    d_t = tx._D
    p_t = tx._P
    c = 0
    for i, item in enumerate(reversed(utf8)):
        c = d_t[c][p_t[(i + 1) % 8][int(item)]]
    return memory_id + date_hex + serial_hex + str(tx._INV[c])


@pytest.mark.parametrize("mid,d,serial", [
    ("A11XY9", date(2026, 9, 25), 1),
    ("ZZ0042", date(2025, 3, 21), 4095),
    ("123456", date(2027, 1, 1), 16 ** 10 - 1),
])
def test_taxid_layout_and_check_digit(mid, d, serial):
    t = tx.generate_taxid(mid.lower(), d, serial)
    assert len(t) == 22 and t == t.upper() and t.startswith(mid)
    assert int(t[6:11], 16) == (d - date(1970, 1, 1)).days
    assert int(t[11:21], 16) == serial
    assert tx.taxid_is_valid(t)
    assert t == _sdk_reference(mid, d, serial)
    # a single wrong character is caught
    bad = t[:15] + ("0" if t[15] != "0" else "1") + t[16:]
    assert not tx.taxid_is_valid(bad)


def test_taxid_rejects_bad_input():
    for mid in ("", "ABC12", "ABC1234", "AB-123"):
        with pytest.raises(ValueError):
            tx.generate_taxid(mid, date(2026, 1, 1), 1)
    for serial in (0, 16 ** 10):
        with pytest.raises(ValueError):
            tx.generate_taxid("A11XY9", date(2026, 1, 1), serial)
    assert not tx.taxid_is_valid("short") and not tx.taxid_is_valid("A11XY9" + "Z" * 16)


def test_indatim_is_noon_tehran_on_the_issue_day():
    ms = indatim_ms(date(2026, 9, 25))
    dt = datetime.fromtimestamp(ms / 1000, tz=TEHRAN)
    assert (dt.date(), dt.hour) == (date(2026, 9, 25), 12)
    assert datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date() == date(2026, 9, 25)


def test_line_goods_id_validation():
    from pydantic import ValidationError
    from app.schemas.invoice import InvoiceItemCreate
    assert InvoiceItemCreate(product_name="x", sstid="۲۷۲۰۰۰۰۱۱۴۵۴۲").sstid == "2720000114542"  # Persian digits
    assert InvoiceItemCreate(product_name="x", sstid=" ").sstid is None
    with pytest.raises(ValidationError):
        InvoiceItemCreate(product_name="x", sstid="123456789012")


# ─── 2. A private Iranian company with an owner session ────────────────

@pytest.fixture()
def co(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.tenant import use_company
    from app.models.company import Company
    from app.models.company_profile import CompanyProfile
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Moadian Co", slug=f"moa-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        db.add(CompanyProfile(legal_name="شرکت آزمون", economic_code="14001234567", national_id="14001234567"))
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf), cid
    client.cookies.clear()
    _purge_company(db, cid)


def _setup(api, **settings):
    body = {"memory_id": "a11xy9", "default_sstid": "2720000114542", "default_mu": "1627", **settings}
    r = api.put("/moadian/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _customer(api, **over):
    body = {"type": "client", "name": f"خریدار {uuid.uuid4().hex[:4]}", "national_id": "10101010101",
            "economic_code": "10101010101", "postal_code": "1234567890", **over}
    r = api.post("/entities", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(api, entity, *, status="issued", issue=None, items=None, currency="IRR", amount=0):
    issue = issue or date.today().isoformat()
    r = api.post("/invoices", json={
        "number": f"M-{uuid.uuid4().hex[:6]}", "kind": "sales", "status": status, "issue_date": issue,
        "due_date": issue, "amount": amount, "currency": currency, "entity_id": entity["id"] if entity else None,
        "items": items if items is not None else [
            {"product_name": "طراحی سایت", "quantity": 2, "unit_price": 50_000_000, "tax_rate": 10},
            {"product_name": "میزبانی", "quantity": 1, "unit_price": 20_000_000, "taxable": False,
             "sstid": "2330000000009", "mu": "164"},
        ],
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_settings_validation_and_owner_only(co, client):
    api, _cid = co
    got = api.get("/moadian/settings").json()
    assert got["deadline_days"] == 12 and got["memory_id"] == ""
    for bad in ({"memory_id": "ABC"}, {"default_sstid": "123"}, {"default_mu": "pcs"}, {"deadline_days": 0}):
        assert api.put("/moadian/settings", json=bad).status_code == 422, bad
    saved = _setup(api)
    assert saved["memory_id"] == "A11XY9"


def test_packet_for_a_b2b_invoice(co, db):
    api, cid = co
    _setup(api)
    ent = _customer(api)
    inv = _invoice(api, ent)
    pv = api.get(f"/moadian/invoices/{inv['id']}/preview").json()
    assert pv["ready"] is True and pv["problems"] == [] and pv["provisional"] is True
    h, body = pv["packet"]["header"], pv["packet"]["body"]
    assert h["inty"] == 1 and h["inp"] == 1 and h["ins"] == 1
    assert h["tins"] == "14001234567" and h["tob"] == 2 and h["bid"] == "10101010101"
    assert h["tinb"] == "10101010101" and h["bpc"] == "1234567890"
    assert h["tprdis"] == 120_000_000 and h["tvam"] == 10_000_000 and h["tbill"] == 130_000_000 == inv["amount"]
    assert h["setm"] == 2 and h["cap"] == 0 and h["insp"] == 130_000_000       # nothing paid yet
    assert tx.taxid_is_valid(h["taxid"]) and h["inno"] == h["taxid"][11:21]
    assert body[0]["sstid"] == "2720000114542" and body[0]["mu"] == "1627"      # company defaults
    assert body[0]["vra"] == 10 and body[0]["vam"] == 10_000_000 and body[0]["tsstam"] == 110_000_000
    assert body[1]["sstid"] == "2330000000009" and body[1]["mu"] == "164" and body[1]["vra"] == 0


def test_b2c_and_payment_split(co):
    api, _ = co
    _setup(api)
    anon = _customer(api, national_id=None, economic_code=None, postal_code=None)
    inv = _invoice(api, anon)
    assert api.post(f"/invoices/{inv['id']}/payments", json={"amount": 30_000_000, "method": "bank"}).status_code == 201
    pv = api.get(f"/moadian/invoices/{inv['id']}/preview").json()
    h = pv["packet"]["header"]
    assert pv["ready"] and h["inty"] == 2 and h["bid"] is None and h["tob"] is None
    assert any("نوع دوم" in w for w in pv["warnings"])
    assert h["setm"] == 3 and h["cap"] == 30_000_000 and h["insp"] == 100_000_000


def test_problems_block_the_export(co):
    api, _ = co
    ent = _customer(api, postal_code="12345")
    inv = _invoice(api, ent, items=[{"product_name": "کالا", "quantity": 3, "unit_price": 1000, "line_total": 2500}])
    pv = api.get(f"/moadian/invoices/{inv['id']}/preview").json()
    text = " | ".join(pv["problems"])
    assert pv["ready"] is False
    assert "memory id" in text and "goods/service id" in text and "measurement-unit" in text
    assert "postal code" in text and "differs from the line total" in text
    gbp = _invoice(api, ent, currency="GBP")
    assert any("rial" in p for p in api.get(f"/moadian/invoices/{gbp['id']}/preview").json()["problems"])
    draft = _invoice(api, ent, status="draft")
    assert any("issue it first" in p for p in api.get(f"/moadian/invoices/{draft['id']}/preview").json()["problems"])


def test_export_assigns_stable_serials_and_skips_what_is_not_ready(co, db):
    api, _ = co
    _setup(api)
    ent = _customer(api)
    a, b = _invoice(api, ent), _invoice(api, ent)
    broken = _invoice(api, ent, items=[{"product_name": "x", "quantity": 1, "unit_price": 10, "line_total": 11}])
    r = api.post("/moadian/export", json={"invoice_ids": [a["id"], b["id"], broken["id"], a["id"]]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["count"] == 2 and out["file_name"].startswith("moadian-") and out["file_name"].endswith(".json")
    assert [s["number"] for s in out["skipped"]] == [broken["number"]]
    serials = [x["serial"] for x in out["invoices"]]
    assert serials[1] == serials[0] + 1
    taxids = {x["invoice_id"]: x["taxid"] for x in out["invoices"]}
    assert all(tx.taxid_is_valid(t) for t in taxids.values()) and len(set(taxids.values())) == 2
    assert {p["header"]["taxid"] for p in out["packets"]} == set(taxids.values())
    assert all("_meta" not in p for p in out["packets"])

    pending = api.get("/moadian/invoices", params={"state": "pending"}).json()["invoices"]
    assert [x["number"] for x in pending] == [broken["number"]]
    exported = api.get("/moadian/invoices", params={"state": "exported"}).json()["invoices"]
    assert {x["id"] for x in exported} == {a["id"], b["id"]}
    # Re-export keeps the same serial and tax number.
    again = api.post("/moadian/export", json={"invoice_ids": [a["id"]]}).json()
    assert again["invoices"][0]["taxid"] == taxids[a["id"]]
    assert api.get(f"/moadian/invoices/{a['id']}/preview").json()["provisional"] is False


def test_results_and_the_confirmed_lock(co):
    api, _ = co
    _setup(api)
    ent = _customer(api)
    inv = _invoice(api, ent)
    assert api.patch(f"/moadian/invoices/{inv['id']}", json={"status": "confirmed"}).status_code == 409  # not exported
    api.post("/moadian/export", json={"invoice_ids": [inv["id"]]})
    rej = api.patch(f"/moadian/invoices/{inv['id']}", json={"status": "rejected", "error": "خطای کد اقتصادی"}).json()
    assert rej["moadian_status"] == "rejected" and rej["error"] == "خطای کد اقتصادی"
    ok = api.patch(f"/moadian/invoices/{inv['id']}", json={"status": "confirmed", "reference": "REF-778"}).json()
    assert ok["moadian_status"] == "confirmed" and ok["reference"] == "REF-778" and ok["error"] is None
    assert api.patch(f"/moadian/invoices/{inv['id']}", json={"status": "sent"}).status_code == 422
    # A confirmed invoice can't be re-exported or have its figures changed.
    skipped = api.post("/moadian/export", json={"invoice_ids": [inv["id"]]}).json()["skipped"]
    assert skipped and "confirmed" in skipped[0]["problems"][0]
    r = api.patch(f"/invoices/{inv['id']}", json={"amount": 1})
    assert r.status_code == 409 and "اصلاحی" in r.json()["detail"]
    assert api.patch(f"/invoices/{inv['id']}", json={"description": "note"}).status_code == 200
    # The edit dialog re-sends unchanged figures; that must still save.
    same = {"amount": inv["amount"], "issue_date": inv["issue_date"], "currency": "irr", "number": inv["number"],
            "description": "note 2"}
    assert api.patch(f"/invoices/{inv['id']}", json=same).status_code == 200
    assert api.patch(f"/invoices/{inv['id']}", json={"issue_date": "2020-01-01"}).status_code == 409
    assert api.patch(f"/invoices/{inv['id']}", json={"items": []}).status_code == 409
    listed = next(i for i in api.get("/invoices").json() if i["id"] == inv["id"])
    assert listed["moadian_status"] == "confirmed" and listed["moadian_reference"] == "REF-778"


def test_deadline_notifications(co, db):
    from app.db.tenant import use_company
    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications
    api, cid = co
    ent = _customer(api)
    today = date.today()
    old = _invoice(api, ent, issue=(today - timedelta(days=14)).isoformat())
    near = _invoice(api, ent, issue=(today - timedelta(days=10)).isoformat())
    fresh = _invoice(api, ent, issue=(today - timedelta(days=2)).isoformat())
    with use_company(cid):
        refresh_notifications(db, today=today)
        assert db.execute(select(Notification).where(Notification.kind == "moadian")).scalars().all() == []  # not enabled
    _setup(api)
    with use_company(cid):
        refresh_notifications(db, today=today)
        rows = {n.dedupe_key: n for n in db.execute(select(Notification).where(Notification.kind == "moadian")).scalars().all()}
        assert rows[f"moadian-{old['id']}"].level == "high"
        assert rows[f"moadian-{near['id']}"].level == "warning"
        assert f"moadian-{fresh['id']}" not in rows
    api.post("/moadian/export", json={"invoice_ids": [old["id"]]})
    with use_company(cid):
        refresh_notifications(db, today=today)
        db.expire_all()
        row = db.execute(select(Notification).where(Notification.dedupe_key == f"moadian-{old['id']}")).scalars().one()
        assert row.dismissed_at is not None          # exported → the alert closes itself
        still = db.execute(select(Notification).where(Notification.dedupe_key == f"moadian-{near['id']}")).scalars().one()
        assert still.dismissed_at is None


def test_other_roles(co, client):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    api, cid = co
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="acc", is_admin=False, role="accountant", company_id=cid))
    client.cookies.set(CSRF_COOKIE, csrf)
    acc = _CSRFTestClient(client, csrf)
    assert acc.get("/moadian/invoices").status_code == 200
    assert acc.put("/moadian/settings", json={"deadline_days": 20}).status_code == 403

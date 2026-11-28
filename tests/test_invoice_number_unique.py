"""Invoice numbers and سامانه مؤدیان serials unique in the database (review
2026-11-28, scenarios CV4–CV5, migration 088).

Numbers were unique per (company, kind) only in ``_assert_number_free``: two
requests at the same moment both passed it, and the OCR import never ran it.
Production has one issued + voided pair sharing a number, so the index leaves
voided and canceled invoices out, and the API follows the same rule. The
Moadian serial was max + 1 with no lock: two exports could give one tax
number to two invoices."""
from __future__ import annotations

import importlib.util
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db.tenant import use_company
from app.models.invoice import Invoice
from tests.conftest import IS_SQLITE
from tests.test_moadian import _customer, _invoice, _setup, co  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def _post(api, number, *, kind="sales", status="issued", entity=None):
    return api.post("/invoices", json={
        "number": number, "kind": kind, "status": status, "issue_date": "2026-11-01",
        "due_date": "2026-11-30", "amount": 1_000_000, "currency": "IRR",
        "entity_id": entity["id"] if entity else None})


# ─── CV4: one live invoice per number ─────────────────────────────────────────

def test_cv4_a_voided_invoice_gives_its_number_up(co):  # noqa: F811
    """CV4: the issued + voided pair production holds is allowed; a second
    live one is not; a purchase may share a sales number."""
    api, _cid = co
    ent = _customer(api)
    number = f"INV-{uuid.uuid4().hex[:5]}"
    first = _post(api, number, entity=ent)
    assert first.status_code == 201, first.text
    assert _post(api, number, entity=ent).status_code == 409
    assert api.post(f"/invoices/{first.json()['id']}/void").status_code == 200
    again = _post(api, number, entity=ent)
    assert again.status_code == 201, again.text
    third = _post(api, number, entity=ent)
    assert third.status_code == 409 and f"Sales invoice number '{number}' already exists" in third.json()["detail"]
    assert _post(api, number, kind="purchase").status_code == 201


def test_cv4_the_database_refuses_what_a_race_lets_through(co, db, monkeypatch):  # noqa: F811
    """CV4: with the API check out of the way (as two requests at once both
    pass it), the unique index answers — the same 409, never a 500."""
    from app.api import invoices as inv_api
    api, cid = co
    number = f"RACE-{uuid.uuid4().hex[:5]}"
    assert _post(api, number).status_code == 201
    monkeypatch.setattr(inv_api, "_assert_number_free", lambda *a, **k: None)
    r = _post(api, number)
    assert r.status_code == 409 and number in r.json()["detail"], r.text
    assert api.get("/invoices").status_code == 200                       # the session is usable after
    with use_company(cid):
        db.expire_all()
        assert len(db.execute(select(Invoice).where(Invoice.number == number)).scalars().all()) == 1
        # straight at the database: a voided twin is fine, a live one is not
        db.add(Invoice(number=number, kind="sales", status="voided", issue_date=_d(), due_date=_d()))
        db.flush()
        db.add(Invoice(number=number, kind="sales", status="draft", issue_date=_d(), due_date=_d()))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def _d():
    from datetime import date
    return date(2026, 11, 1)


def test_cv4_an_edit_cannot_take_a_live_number(co):  # noqa: F811
    """CV4: bringing a canceled invoice back, or changing a draft's kind,
    claims the number among live invoices."""
    api, _cid = co
    number = f"ED-{uuid.uuid4().hex[:5]}"
    assert _post(api, number).status_code == 201
    canceled = _post(api, number, status="canceled")
    assert canceled.status_code == 201, canceled.text                    # a canceled one holds no number
    r = api.patch(f"/invoices/{canceled.json()['id']}", json={"status": "draft"})
    assert r.status_code == 409, r.text
    bill = _post(api, number, kind="purchase", status="draft")
    assert bill.status_code == 201
    assert api.patch(f"/invoices/{bill.json()['id']}", json={"kind": "sales"}).status_code == 409
    assert api.patch(f"/invoices/{bill.json()['id']}", json={"number": number, "description": "same"}).status_code == 200


# ─── CV5: one serial per tax number ────────────────────────────────────────────

def test_cv5_a_serial_another_export_took_is_not_given_twice(co, db, monkeypatch):  # noqa: F811
    """CV5: the export reads a top that is already stale (another export took
    that serial in the meantime): the database refuses it and the export takes
    the next free one."""
    from app.services.moadian import builder
    api, cid = co
    _setup(api)
    ent = _customer(api)
    a, b = _invoice(api, ent), _invoice(api, ent)
    first = api.post("/moadian/export", json={"invoice_ids": [a["id"]]}).json()["invoices"][0]["serial"]
    real, calls = builder.next_serial, []

    def stale(db):
        calls.append(1)
        return first if len(calls) == 1 else real(db)

    monkeypatch.setattr(builder, "next_serial", stale)
    out = api.post("/moadian/export", json={"invoice_ids": [b["id"]]}).json()
    assert out["skipped"] == [] and out["invoices"][0]["serial"] == first + 1 and len(calls) == 2
    with use_company(cid):
        db.expire_all()
        serials = sorted(s for (s,) in db.execute(select(Invoice.moadian_serial).where(
            Invoice.id.in_([uuid.UUID(a["id"]), uuid.UUID(b["id"])]))).all())
    assert serials == [first, first + 1]


@pytest.mark.skipif(IS_SQLITE, reason="needs a second connection that really waits on the first (PostgreSQL)")
def test_cv5_two_exports_at_the_same_moment_get_two_serials(co):  # noqa: F811
    """CV5, for real: two sessions read the same top; the second one's write
    waits for the first export's commit, fails on the index, and takes the
    next serial."""
    import threading
    import time

    from app.services.moadian import builder
    from tests.conftest import _TestSession
    api, cid = co
    ent = _customer(api)
    a, b = _invoice(api, ent), _invoice(api, ent)
    s1, s2 = _TestSession(), _TestSession()
    got = {}
    try:
        with use_company(cid):
            first = builder.assign_serial(s1, s1.get(Invoice, uuid.UUID(a["id"])))     # not committed yet

        def other_export():
            with use_company(cid):
                got["serial"] = builder.assign_serial(s2, s2.get(Invoice, uuid.UUID(b["id"])))
                s2.commit()

        t = threading.Thread(target=other_export)
        t.start()
        time.sleep(0.5)
        assert t.is_alive()                         # waiting on the first export's serial
        s1.commit()
        t.join(10)
        assert got["serial"] == first + 1
    finally:
        s1.close()
        s2.close()


def test_cv5_the_database_refuses_one_serial_twice_in_a_company(co, db):  # noqa: F811
    api, cid = co
    ent = _customer(api)
    a, b = _invoice(api, ent), _invoice(api, ent)
    with use_company(cid):
        rows = {str(r.id): r for r in db.execute(select(Invoice).where(
            Invoice.id.in_([uuid.UUID(a["id"]), uuid.UUID(b["id"])]))).scalars()}
        rows[a["id"]].moadian_serial = 900_001
        db.flush()
        rows[b["id"]].moadian_serial = 900_001
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_cv5_an_export_that_keeps_losing_is_skipped_not_a_500(co, monkeypatch):  # noqa: F811
    from app.services.moadian import builder
    api, _cid = co
    _setup(api)
    ent = _customer(api)
    a, b = _invoice(api, ent), _invoice(api, ent)
    taken = api.post("/moadian/export", json={"invoice_ids": [a["id"]]}).json()["invoices"][0]["serial"]
    monkeypatch.setattr(builder, "next_serial", lambda db: taken)
    out = api.post("/moadian/export", json={"invoice_ids": [b["id"]]})
    assert out.status_code == 200, out.text
    assert out.json()["invoices"] == [] and "try again" in out.json()["skipped"][0]["problems"][0]


# ─── migration 088 ───────────────────────────────────────────────────────────────

def _migrate(eng, direction="upgrade"):
    spec = importlib.util.spec_from_file_location("m088", ROOT / "alembic/versions/088_invoice_number_unique.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(mod, direction)()


def _scratch(rows):
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE invoices (id INTEGER PRIMARY KEY, company_id TEXT, kind TEXT, number TEXT, "
                       "status TEXT, moadian_serial BIGINT)"))
        for r in rows:
            c.execute(text("INSERT INTO invoices (company_id, kind, number, status, moadian_serial) "
                           "VALUES (:c, :k, :n, :s, :m)"), r)
    return eng


def _indexes(eng):
    with eng.connect() as c:
        return {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))}


def test_cv4_the_migration_accepts_production_s_issued_and_voided_pair():
    """CV4: production's one duplicate group — an issued and a voided sales
    invoice sharing a number — does not stop the migration."""
    eng = _scratch([
        {"c": "co", "k": "sales", "n": "1001", "s": "issued", "m": None},
        {"c": "co", "k": "sales", "n": "1001", "s": "voided", "m": None},
        {"c": "co", "k": "purchase", "n": "1001", "s": "issued", "m": None},
        {"c": "co", "k": "sales", "n": "1002", "s": "issued", "m": 1},
    ])
    _migrate(eng)
    _migrate(eng)                                                         # safe twice
    assert {"uq_invoices_company_kind_number", "uq_invoices_company_moadian_serial"} <= _indexes(eng)
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(text("INSERT INTO invoices (company_id, kind, number, status) VALUES ('co', 'sales', '1001', 'draft')"))
    _migrate(eng, "downgrade")
    assert not {"uq_invoices_company_kind_number", "uq_invoices_company_moadian_serial"} & _indexes(eng)


def test_cv4_live_duplicates_skip_that_index_with_a_warning_not_a_failed_deploy(caplog):
    eng = _scratch([
        {"c": "co", "k": "sales", "n": "1001", "s": "issued", "m": None},
        {"c": "co", "k": "sales", "n": "1001", "s": "paid", "m": None},
    ])
    with caplog.at_level(logging.WARNING):
        _migrate(eng)
    assert "uq_invoices_company_kind_number" not in _indexes(eng)
    assert "uq_invoices_company_moadian_serial" in _indexes(eng)
    assert "uq_invoices_company_kind_number not created" in caplog.text

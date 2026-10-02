"""Codes typed on a Persian keyboard are stored in 0–9.

A party code typed as «۱۰۱» was stored that way, so a lookup or an export by
101 missed it; a barcode typed as «۶۲۹۱…» never matched a scanner, which sends
0–9 (deep browser test, 2026-10-02, findings #8 and #9). An account code typed
in a voucher line or a new account had the same problem."""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import select

from app.models.entity import Entity
from app.models.inventory import InventoryItem

ROOT = Path(__file__).resolve().parents[1]


def test_a_partys_code_is_stored_in_latin_digits(auth_client):
    r = auth_client.post("/entities", json={"type": "client", "name": f"کافه {uuid.uuid4().hex[:6]}", "code": "۱۰۱"})
    assert r.status_code == 201, r.text
    assert r.json()["code"] == "101"
    r = auth_client.patch(f"/entities/{r.json()['id']}", json={"code": "٢٠٢"})       # Arabic-Indic too
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "202"


def test_a_barcode_and_sku_are_stored_in_latin_digits_and_found_either_way(auth_client):
    code = "۶۲۹" + "".join("۰۱۲۳۴۵۶۷۸۹"[int(d)] for d in str(uuid.uuid4().int)[:10])
    latin = code.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    r = auth_client.post("/manager-reports/inventory/items", json={"name": "کاغذ A4", "sku": "ک-۱۲", "barcode": code})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["barcode"] == latin and item["sku"] == "ک-12"
    for typed in (latin, code):                      # a scanner's 0–9, or typed on a Persian keyboard
        found = auth_client.get(f"/manager-reports/inventory/items/by-barcode/{typed}")
        assert found.status_code == 200 and found.json()["id"] == item["id"], typed
    r = auth_client.patch(f"/manager-reports/inventory/items/{item['id']}", json={"sku": "ک-۳۴"})
    assert r.status_code == 200 and r.json()["sku"] == "ک-34", r.text


def test_an_account_code_typed_in_persian_digits_posts_to_that_account(auth_client):
    accounts = {a["code"] for a in auth_client.get("/accounts").json()}
    dr, cr = sorted(c for c in accounts if c.isdigit())[:2]
    persian = lambda c: "".join("۰۱۲۳۴۵۶۷۸۹"[int(d)] for d in c)
    r = auth_client.post("/transactions", json={"date": "2026-09-01", "description": "کدهای فارسی", "lines": [
        {"account_code": persian(dr), "debit": 500, "credit": 0},
        {"account_code": persian(cr), "debit": 0, "credit": 500}]})
    assert r.status_code in (200, 201), r.text
    assert sorted(ln["account_code"] for ln in r.json()["lines"]) == sorted([dr, cr])


def test_the_migration_rewrites_codes_already_stored(db):
    party = Entity(type="client", name=f"قدیمی {uuid.uuid4().hex[:6]}", code="۱۰۱۹")
    stock = InventoryItem(name=f"قدیمی {uuid.uuid4().hex[:6]}", sku="SK-۷", barcode="۶۲۶۰۰۰۰۰۰۰۰۱۷", unit="unit")
    db.add_all([party, stock])
    db.commit()
    try:
        _migrate_and_check(db)
    finally:
        db.delete(party)
        db.delete(stock)
        db.commit()


def _migrate_and_check(db):
    spec = importlib.util.spec_from_file_location("m069", ROOT / "alembic/versions/069_latin_codes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    for _ in range(2):                                # safe to run twice
        with db.get_bind().begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
    db.expire_all()
    assert db.execute(select(Entity).where(Entity.code == "1019")).scalars().first() is not None
    item = db.execute(select(InventoryItem).where(InventoryItem.barcode == "6260000000017")).scalars().first()
    assert item is not None and item.sku == "SK-7"
    assert db.execute(select(Entity).where(Entity.code == "۱۰۱۹")).scalars().first() is None

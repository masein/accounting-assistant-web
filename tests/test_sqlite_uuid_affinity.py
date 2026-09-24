"""UUID columns must have TEXT affinity on SQLite (app/db/base.py). Without it
a hex id shaped like a number (1e9999…) is stored as the float inf and every
read of that table crashes — the intermittent CI failure of 2026-09-24."""
from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text

from app.models.account import Account, AccountLevel
from tests.conftest import IS_SQLITE

pytestmark = pytest.mark.skipif(not IS_SQLITE, reason="SQLite affinity rules only")


def test_uuid_columns_are_char32_on_sqlite(db):
    cols = {row[1]: row[2] for row in db.execute(text("PRAGMA table_info(accounts)")).all()}
    assert cols["id"].upper() == "CHAR(32)", cols["id"]
    assert cols["parent_id"].upper() == "CHAR(32)", cols["parent_id"]


def test_numeric_looking_uuid_round_trips(db):
    scary = uuid.UUID("1e999999999999999999999999999999")  # SQLite would read this as inf under NUMERIC affinity
    acc = Account(id=scary, code=f"99{uuid.uuid4().hex[:4]}", name="affinity probe", level=AccountLevel.GENERAL)
    db.add(acc); db.commit()
    db.expire_all()
    stored = db.execute(text("SELECT typeof(id), id FROM accounts WHERE code = :c"), {"c": acc.code}).one()
    assert stored[0] == "text"
    assert db.get(Account, scary).name == "affinity probe"
    db.delete(db.get(Account, scary)); db.commit()

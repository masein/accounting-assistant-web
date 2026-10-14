"""Parties made from the chart's own accounts (scenarios X1–X6).

A school's hand-kept journal keeps «دبیرستان منادی حق» as an account under
بدهکاران and «خانم آیت الهی» under بستانکاران; the import made them accounts, so
اشخاص had only the bank. Now they can become parties tied to their account: the
window reads the account, new entries link themselves, the statement prints it."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from tests.test_inventory_ledger import _books

ir = _books(("ir", "IRR"))
TODAY = date.today()
EARLY = TODAY - timedelta(days=90)


def _school_chart(db, cid):
    """The import's shape: ledgers named for their line, the people under them."""
    from app.models.account import Account, AccountLevel
    with use_company(cid):
        by = {a.code: a for a in db.execute(select(Account)).scalars()}

        def add(code, name, parent, level=AccountLevel.SUB, line=None):
            a = Account(code=code, name=name, level=level, parent_id=by[parent].id if parent else None,
                        statement_line=line)
            db.add(a)
            db.flush()
            by[code] = a
        add("1152", "بدهکاران", "11", AccountLevel.GENERAL, "ca_trade_receivables")
        add("115201", "دبیرستان منادی حق", "1152")
        add("115202", "آقای صنعتی", "1152")
        add("2196", "بستانکاران", "21", AccountLevel.GENERAL, "cl_trade_payables")
        add("219603", "خانم آیت الهی", "2196")
        add("6133", "هزینه اجاره", "61", AccountLevel.GENERAL)
        db.commit()


def _post(api, day, lines, desc="school"):
    r = api.post("/transactions", json={
        "date": day.isoformat(), "description": desc, "reference": f"S-{uuid.uuid4().hex[:6]}",
        "lines": [{"account_code": c, "debit": d, "credit": k} for c, d, k in lines]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _school(api, db, cid):
    _school_chart(db, cid)
    _post(api, EARLY, [("115201", 9_838_487_452, 0), ("1110", 0, 9_838_487_452)], "سهم اجاره")
    _post(api, EARLY + timedelta(days=10), [("1110", 600_000_000, 0), ("115201", 0, 600_000_000)], "دریافت از منادی حق")
    _post(api, EARLY + timedelta(days=5), [("115202", 10_000_000_000, 0), ("1110", 0, 10_000_000_000)])
    _post(api, EARLY + timedelta(days=20), [("6133", 8_541_459_013, 0), ("219603", 0, 8_541_459_013)], "اجاره مهر")
    _post(api, EARLY + timedelta(days=21), [("6133", 4_050_000_000, 0), ("1110", 0, 4_050_000_000)], "اجاره نقد")
    _post(api, EARLY + timedelta(days=22), [("6133", 1_000_000, 0), ("2180", 0, 1_000_000)], "a role's account")


def _offered(api):
    r = api.get("/entities/account-candidates")
    assert r.status_code == 200, r.text
    return {a["code"]: a for a in r.json()["accounts"]}


def test_x1_the_debtor_and_creditor_accounts_are_offered_and_nothing_else(ir, db):
    api, cid = ir
    _school(api, db, cid)
    offered = _offered(api)
    assert set(offered) == {"115201", "115202", "219603"}, offered          # not rent, bank, wages payable
    assert offered["115201"]["side"] == "receivable" and offered["115201"]["balance"] == 9_238_487_452
    assert offered["219603"]["side"] == "payable" and offered["219603"]["balance"] == 8_541_459_013
    assert offered["219603"]["suggested_type"] == "supplier" and offered["115201"]["group"] == "بدهکاران"


def test_x2_x3_x4_made_parties_read_their_account(ir, db):
    from app.models.entity import Entity, TransactionEntity
    api, cid = ir
    _school(api, db, cid)
    offered = _offered(api)
    r = api.post("/entities/from-accounts", json={"accounts": [
        {"account_id": offered["115201"]["account_id"], "type": "client"},
        {"account_id": offered["219603"]["account_id"], "type": "supplier"}]})
    assert r.status_code == 201, r.text
    made = {p["name"]: p for p in r.json()["created"]}
    assert set(made) == {"دبیرستان منادی حق", "خانم آیت الهی"}                # X2
    assert set(_offered(api)) == {"115202"}                                      # offered no more
    school, landlady = made["دبیرستان منادی حق"], made["خانم آیت الهی"]
    with use_company(cid):
        links = db.execute(select(TransactionEntity).where(
            TransactionEntity.entity_id == uuid.UUID(school["id"]))).scalars().all()
    assert len(links) == 2 and {ln.role for ln in links} == {"client"}           # its past entries, linked

    rows = api.get(f"/reports/entities/{school['id']}/transactions").json()       # X3
    assert [(r["entity_paid"], r["entity_received"]) for r in rows] == [(0, 9_838_487_452), (600_000_000, 0)]
    assert rows[-1]["entity_balance"] == 9_238_487_452                            # what the school owes us
    assert rows[-1]["entity_control_account"] == "115201"
    rows = api.get(f"/reports/entities/{landlady['id']}/transactions").json()
    assert rows[-1]["entity_balance"] == 8_541_459_013                            # what we owe her

    tid = _post(api, TODAY, [("115201", 150_000_000, 0), ("1110", 0, 150_000_000)], "by hand")   # X4
    rows = api.get(f"/reports/entities/{school['id']}/transactions").json()
    assert rows[-1]["id"] == tid and rows[-1]["entity_balance"] == 9_388_487_452
    with use_company(cid):
        assert db.execute(select(TransactionEntity).where(
            TransactionEntity.transaction_id == uuid.UUID(tid),
            TransactionEntity.entity_id == uuid.UUID(school["id"]))).scalar_one().role == "client"
        assert db.get(Entity, uuid.UUID(school["id"])).account_id is not None


def test_x5_the_statement_prints_the_account_after_what_was_brought_forward(ir, db):
    from app.models.entity import Entity
    from app.services.document_mail import statement_events
    api, cid = ir
    _school(api, db, cid)
    offered = _offered(api)
    made = api.post("/entities/from-accounts", json={"accounts": [
        {"account_id": offered["115201"]["account_id"], "type": "client"},
        {"account_id": offered["219603"]["account_id"], "type": "supplier"}]}).json()["created"]
    ids = {p["name"]: uuid.UUID(p["id"]) for p in made}
    with use_company(cid):
        school = db.get(Entity, ids["دبیرستان منادی حق"])
        events, _ccy = statement_events(db, school, EARLY + timedelta(days=1), TODAY)
        assert [(e["debit"], e["credit"]) for e in events] == [(9_838_487_452, 0), (0, 600_000_000)]
        assert events[0]["description"] == "مانده ابتدای دوره"                       # brought forward
        landlady = db.get(Entity, ids["خانم آیت الهی"])
        events, _ccy = statement_events(db, landlady, EARLY, TODAY)
        assert [(e["debit"], e["credit"]) for e in events] == [(8_541_459_013, 0)]  # what we owe, as a balance
    r = api.get(f"/entities/{ids['دبیرستان منادی حق']}/statement.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_only_offered_accounts_and_known_types_can_be_made(ir, db):
    api, cid = ir
    _school(api, db, cid)
    from app.models.account import Account
    with use_company(cid):
        rent = db.execute(select(Account.id).where(Account.code == "6133")).scalar()
    r = api.post("/entities/from-accounts", json={"accounts": [{"account_id": str(rent), "type": "supplier"}]})
    assert r.status_code == 422 and "can't be made a party" in r.json()["detail"]
    offered = _offered(api)
    r = api.post("/entities/from-accounts", json={"accounts": [
        {"account_id": offered["115201"]["account_id"], "type": "bank"}]})
    assert r.status_code == 422
    assert set(_offered(api)) == {"115201", "115202", "219603"}                  # nothing half-made


@pytest.mark.parametrize("role", ["viewer", "employee"])
def test_x6_only_the_books_writers_make_parties(ir, db, role):
    from app.core.auth import create_session_token
    from app.core.config import settings
    api, cid = ir
    _school(api, db, cid)
    offered = _offered(api)
    api.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username=role, is_admin=False, role=role, company_id=cid))
    r = api.post("/entities/from-accounts", json={"accounts": [
        {"account_id": offered["115201"]["account_id"], "type": "client"}]})
    assert r.status_code == 403

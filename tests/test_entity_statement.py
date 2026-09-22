"""Statement-of-account view of an entity's transactions (QA finding 3).

The entity "View transactions" table must read like a bank / supplier
statement: **Debtor** = the entity paid or gave something, **Creditor** = the
entity received something, **Remaining** = running balance after the row.
Movements are read off the entity's control account on the active chart;
journals without one fall back to the link's recorded share, then to a
cash-settled-on-the-spot reading, and otherwise stay blank.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction
from app.services.entity_statement import (
    build_entity_statement,
    control_account_code,
    control_spec_for,
)


def _entity(db, etype, **kw):
    e = Entity(type=etype, name=f"{etype}-{uuid.uuid4().hex[:6]}", **kw)
    db.add(e)
    db.commit()
    return e


def _link(db, txn, entity, role, amount=None):
    db.add(TransactionEntity(transaction_id=txn.id, entity_id=entity.id, role=role, amount=amount))
    db.commit()


def _statement(db, entity):
    ids = select(TransactionEntity.transaction_id).where(TransactionEntity.entity_id == entity.id)
    txns = db.execute(
        select(Transaction).where(Transaction.id.in_(ids)).order_by(Transaction.date, Transaction.id)
    ).scalars().all()
    return build_entity_statement(db, entity, txns)


# ---------------------------------------------------------------------------
# Bank: its own GL account is the control account
# ---------------------------------------------------------------------------

def test_bank_statement_paid_received_and_running_balance(db, make_transaction):
    code = "1118"
    from app.services.account_resolver import _ensure_account
    _ensure_account(db, code, "بانک صورتحساب — bank", "ir")
    bank = _entity(db, "bank", code=code)

    deposit = make_transaction([(code, 5_000_000, 0), ("4110", 0, 5_000_000)], tx_date=date(2026, 3, 1))
    payment = make_transaction([("6112", 1_200_000, 0), (code, 0, 1_200_000)], tx_date=date(2026, 3, 2))
    _link(db, deposit, bank, "bank")
    _link(db, payment, bank, "bank")

    rows = _statement(db, bank)
    assert [(r.paid, r.received, r.balance) for r in rows] == [
        (0, 5_000_000, 5_000_000),          # money landed in the bank → Creditor
        (1_200_000, 0, 3_800_000),          # the bank paid out → Debtor
    ]
    assert all(r.placed for r in rows)


def test_bank_without_own_account_uses_chart_bank_account(db, make_transaction):
    bank = _entity(db, "bank")   # no code
    spec = control_spec_for(db, bank)
    assert "1110" in spec.codes
    assert control_account_code(db, bank) == "1110"


# ---------------------------------------------------------------------------
# Client: trade debtors
# ---------------------------------------------------------------------------

def test_client_invoice_then_settlement(db, make_transaction):
    client = _entity(db, "client")
    invoice = make_transaction([("1112", 900_000, 0), ("4110", 0, 900_000)], tx_date=date(2026, 4, 1))
    settle = make_transaction([("1110", 900_000, 0), ("1112", 0, 900_000)], tx_date=date(2026, 4, 9))
    _link(db, invoice, client, "client")
    _link(db, settle, client, "client")

    rows = _statement(db, client)
    # Took goods on credit → Creditor (owes 900k); paid → Debtor (owes 0).
    assert [(r.paid, r.received, r.balance) for r in rows] == [
        (0, 900_000, 900_000),
        (900_000, 0, 0),
    ]
    assert control_account_code(db, client) == "1112"


def test_cash_sale_linked_to_client_shows_both_sides_and_leaves_balance(db, make_transaction):
    client = _entity(db, "client")
    cash_sale = make_transaction([("1110", 250_000, 0), ("4110", 0, 250_000)], tx_date=date(2026, 4, 3))
    _link(db, cash_sale, client, "client")
    (row,) = _statement(db, client)
    assert (row.paid, row.received, row.balance, row.placed) == (250_000, 250_000, 0, True)


# ---------------------------------------------------------------------------
# Supplier / employee: trade creditors (liability side)
# ---------------------------------------------------------------------------

def test_supplier_bill_then_payment(db, make_transaction):
    supplier = _entity(db, "supplier")
    bill = make_transaction([("6112", 700_000, 0), ("2110", 0, 700_000)], tx_date=date(2026, 5, 1))
    pay = make_transaction([("2110", 700_000, 0), ("1110", 0, 700_000)], tx_date=date(2026, 5, 15))
    _link(db, bill, supplier, "supplier")
    _link(db, pay, supplier, "supplier")

    rows = _statement(db, supplier)
    # Delivered goods (gave) → Debtor, we owe 700k; got paid → Creditor, owe 0.
    assert [(r.paid, r.received, r.balance) for r in rows] == [
        (700_000, 0, 700_000),
        (0, 700_000, 0),
    ]


def test_employee_uses_payee_role_and_payables(db, make_transaction):
    emp = _entity(db, "employee")
    spec = control_spec_for(db, emp)
    assert spec.liability_side and "2110" in spec.codes and spec.matches("2160")


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------

def test_link_share_fallback_signed_amount(db, make_transaction):
    """Aggregate journals (migration opening entry) carry each entity's own
    share on the link; + is a debit on the entity's side."""
    client = _entity(db, "client")
    opening = make_transaction([("6112", 1_000, 0), ("2140", 0, 1_000)], tx_date=date(2026, 1, 1))
    _link(db, opening, client, "client", amount=400)
    (row,) = _statement(db, client)
    assert (row.paid, row.received, row.balance) == (0, 400, 400)

    supplier = _entity(db, "supplier")
    # (no 21xx payable leg, so the control-account path can't claim it)
    other = make_transaction([("6112", 1_000, 0), ("4110", 0, 1_000)], tx_date=date(2026, 1, 2))
    _link(db, other, supplier, "supplier", amount=-300)
    (row,) = _statement(db, supplier)
    assert (row.paid, row.received, row.balance) == (300, 0, 300)


def test_unplaceable_journal_stays_blank(db, make_transaction):
    client = _entity(db, "client")
    weird = make_transaction([("6112", 5_000, 0), ("2140", 0, 5_000)], tx_date=date(2026, 2, 1))
    _link(db, weird, client, "client")
    (row,) = _statement(db, client)
    assert row.placed is False
    assert (row.paid, row.received, row.balance) == (0, 0, 0)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_endpoint_returns_statement_columns(auth_client, db, make_transaction):
    code = "1119"
    from app.services.account_resolver import _ensure_account
    _ensure_account(db, code, "بانک API — bank", "ir")
    bank = _entity(db, "bank", code=code)
    make_transaction([(code, 2_000, 0), ("4110", 0, 2_000)], tx_date=date(2026, 6, 1), reference="ST-1")
    make_transaction([("6112", 500, 0), (code, 0, 500)], tx_date=date(2026, 6, 2), reference="ST-2")

    rows = auth_client.get(f"/reports/entities/{bank.id}/transactions").json()
    by_ref = {r["reference"]: r for r in rows}
    assert by_ref["ST-1"]["entity_received"] == 2_000 and by_ref["ST-1"]["entity_paid"] == 0
    assert by_ref["ST-2"]["entity_paid"] == 500
    assert by_ref["ST-2"]["entity_balance"] == 1_500
    assert by_ref["ST-1"]["entity_control_account"] == code
    assert by_ref["ST-1"]["entity_placed"] is True
    # The generic fields the editor relies on are still there.
    assert by_ref["ST-1"]["lines"] and "entity_links" in by_ref["ST-1"]

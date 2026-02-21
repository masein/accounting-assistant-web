from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.inventory import InventoryItem, InventoryMovement
from app.models.transaction import Transaction, TransactionLine


def list_accounts(db: Session) -> list[Account]:
    return db.execute(select(Account).order_by(Account.code)).scalars().all()


def account_turnovers_between(db: Session, from_date: date, to_date: date) -> list[tuple[UUID, int, int]]:
    q = (
        select(
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(Transaction.date >= from_date, Transaction.date <= to_date)
        .group_by(TransactionLine.account_id)
    )
    return [(a, int(d or 0), int(c or 0)) for a, d, c in db.execute(q).all()]


def account_turnovers_upto(db: Session, to_date: date) -> list[tuple[UUID, int, int]]:
    q = (
        select(
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(Transaction.date <= to_date)
        .group_by(TransactionLine.account_id)
    )
    return [(a, int(d or 0), int(c or 0)) for a, d, c in db.execute(q).all()]


def paged_journal_entries(db: Session, from_date: date, to_date: date, page: int, page_size: int) -> tuple[int, list[Transaction]]:
    count_q = select(func.count(Transaction.id)).where(Transaction.date >= from_date, Transaction.date <= to_date)
    total = int(db.execute(count_q).scalar() or 0)
    offset = max(0, (page - 1) * page_size)
    q = (
        select(Transaction)
        .where(Transaction.date >= from_date, Transaction.date <= to_date)
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        .offset(offset)
        .limit(page_size)
    )
    rows = db.execute(q).scalars().unique().all()
    return total, rows


def paged_account_lines(
    db: Session,
    account_code: str,
    from_date: date,
    to_date: date,
    page: int,
    page_size: int,
) -> tuple[Account | None, int, list[tuple[TransactionLine, Transaction]]]:
    acc = db.execute(select(Account).where(Account.code == account_code.strip())).scalars().one_or_none()
    if not acc:
        return None, 0, []
    count_q = (
        select(func.count(TransactionLine.id))
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(
            TransactionLine.account_id == acc.id,
            Transaction.date >= from_date,
            Transaction.date <= to_date,
        )
    )
    total = int(db.execute(count_q).scalar() or 0)
    offset = max(0, (page - 1) * page_size)
    q = (
        select(TransactionLine, Transaction)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(
            TransactionLine.account_id == acc.id,
            Transaction.date >= from_date,
            Transaction.date <= to_date,
        )
        .order_by(Transaction.date, Transaction.created_at, TransactionLine.id)
        .offset(offset)
        .limit(page_size)
    )
    rows = db.execute(q).all()
    return acc, total, rows


def opening_balance_before(
    db: Session,
    account_id: UUID,
    before_date: date,
) -> tuple[int, int]:
    q = (
        select(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(TransactionLine.account_id == account_id, Transaction.date < before_date)
    )
    d, c = db.execute(q).one()
    return int(d or 0), int(c or 0)


def trial_balance_rows(db: Session, from_date: date, to_date: date) -> list[tuple[str, str, int, int]]:
    q = (
        select(
            Account.code,
            Account.name,
            func.coalesce(func.sum(TransactionLine.debit), 0).label("debit_turnover"),
            func.coalesce(func.sum(TransactionLine.credit), 0).label("credit_turnover"),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(Transaction.date >= from_date, Transaction.date <= to_date)
        .group_by(Account.code, Account.name)
        .order_by(Account.code)
    )
    return [(code, name, int(d or 0), int(c or 0)) for code, name, d, c in db.execute(q).all()]


def debtor_creditor_movements(db: Session, from_date: date, to_date: date) -> list[tuple[date, str, UUID | None, str, int]]:
    """
    Return tuple:
    (txn_date, role, entity_id, entity_name, delta)
    role: debtor | creditor
    delta positive means increase, negative decrease.
    """
    # Receivable (1112): debit increases debtors, credit decreases.
    ar_q = (
        select(
            Transaction.date,
            TransactionEntity.entity_id,
            Entity.name,
            (func.coalesce(func.sum(TransactionLine.debit), 0) - func.coalesce(func.sum(TransactionLine.credit), 0)).label("delta"),
        )
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(TransactionEntity, TransactionEntity.transaction_id == Transaction.id)
        .join(Entity, Entity.id == TransactionEntity.entity_id)
        .where(
            Transaction.date >= from_date,
            Transaction.date <= to_date,
            TransactionLine.account_id == select(Account.id).where(Account.code == "1112").scalar_subquery(),
            TransactionEntity.role.in_(("client",)),
        )
        .group_by(Transaction.date, TransactionEntity.entity_id, Entity.name)
    )
    # Payable (21xx): credit increases creditors, debit decreases.
    ap_q = (
        select(
            Transaction.date,
            TransactionEntity.entity_id,
            Entity.name,
            (func.coalesce(func.sum(TransactionLine.credit), 0) - func.coalesce(func.sum(TransactionLine.debit), 0)).label("delta"),
        )
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(TransactionEntity, TransactionEntity.transaction_id == Transaction.id)
        .join(Entity, Entity.id == TransactionEntity.entity_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            Transaction.date >= from_date,
            Transaction.date <= to_date,
            Account.code.like("21%"),
            TransactionEntity.role.in_(("supplier", "payee")),
        )
        .group_by(Transaction.date, TransactionEntity.entity_id, Entity.name)
    )
    out: list[tuple[date, str, UUID | None, str, int]] = []
    for d, eid, name, delta in db.execute(ar_q).all():
        out.append((d, "debtor", eid, name or "Unassigned", int(delta or 0)))
    for d, eid, name, delta in db.execute(ap_q).all():
        out.append((d, "creditor", eid, name or "Unassigned", int(delta or 0)))
    return out


def list_inventory_items(db: Session) -> list[InventoryItem]:
    return db.execute(select(InventoryItem).order_by(InventoryItem.name)).scalars().all()


def paged_inventory_movements(
    db: Session,
    from_date: date,
    to_date: date,
    page: int,
    page_size: int,
    item_id: UUID | None = None,
) -> tuple[int, list[tuple[InventoryMovement, InventoryItem]]]:
    base = select(InventoryMovement, InventoryItem).join(InventoryItem, InventoryItem.id == InventoryMovement.item_id).where(
        InventoryMovement.movement_date >= from_date,
        InventoryMovement.movement_date <= to_date,
    )
    if item_id:
        base = base.where(InventoryMovement.item_id == item_id)
    count_q = select(func.count()).select_from(base.subquery())
    total = int(db.execute(count_q).scalar() or 0)
    offset = max(0, (page - 1) * page_size)
    rows = db.execute(
        base.order_by(InventoryMovement.movement_date.desc(), InventoryMovement.created_at.desc()).offset(offset).limit(page_size)
    ).all()
    return total, rows


def inventory_movements_for_balance(db: Session, to_date: date) -> list[tuple[InventoryMovement, InventoryItem]]:
    q = (
        select(InventoryMovement, InventoryItem)
        .join(InventoryItem, InventoryItem.id == InventoryMovement.item_id)
        .where(InventoryMovement.movement_date <= to_date)
        .order_by(InventoryMovement.movement_date, InventoryMovement.created_at, InventoryMovement.id)
    )
    return db.execute(q).all()


def sales_items_between(db: Session, from_date: date, to_date: date) -> list[tuple[InvoiceItem, Invoice]]:
    q = (
        select(InvoiceItem, Invoice)
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .where(
            Invoice.kind == "sales",
            Invoice.issue_date >= from_date,
            Invoice.issue_date <= to_date,
        )
        .order_by(Invoice.issue_date.desc(), Invoice.number.desc())
    )
    return db.execute(q).all()


def purchase_items_between(db: Session, from_date: date, to_date: date) -> list[tuple[InvoiceItem, Invoice]]:
    q = (
        select(InvoiceItem, Invoice)
        .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .where(
            Invoice.kind == "purchase",
            Invoice.issue_date >= from_date,
            Invoice.issue_date <= to_date,
        )
        .order_by(Invoice.issue_date.desc(), Invoice.number.desc())
    )
    return db.execute(q).all()


def invoices_between(db: Session, from_date: date, to_date: date, *, kind: str | None = None) -> list[Invoice]:
    q = (
        select(Invoice)
        .where(Invoice.issue_date >= from_date, Invoice.issue_date <= to_date)
        .order_by(Invoice.issue_date.desc(), Invoice.number.desc())
    )
    if kind:
        q = q.where(Invoice.kind == kind)
    return db.execute(q).scalars().all()


def latest_transaction(db: Session) -> Transaction | None:
    return db.execute(
        select(Transaction)
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        .limit(1)
    ).scalars().first()


def transactions_with_lines_between(db: Session, from_date: date, to_date: date) -> list[Transaction]:
    q = (
        select(Transaction)
        .where(Transaction.date >= from_date, Transaction.date <= to_date)
        .order_by(Transaction.date, Transaction.created_at, Transaction.id)
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.entity_links).selectinload(TransactionEntity.entity),
        )
    )
    return db.execute(q).scalars().unique().all()

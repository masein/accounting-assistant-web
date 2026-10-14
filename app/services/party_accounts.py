"""Parties made from the chart's own debtor and creditor accounts (scenarios X1–X6).

A hand-kept journal keeps its people and organisations as accounts: «دبیرستان منادی حق»
under بدهکاران, «خانم آیت الهی» under بستانکاران. The import made them accounts, so they
never reached اشخاص, and a party's window, statement and search had nothing to show.

``candidates`` lists the accounts that could be parties: leaf accounts with entries on a
receivable, payable, advance or loan line of the balance sheet, that aren't a bank's,
aren't an account the posting roles use (trade debtors, VAT, wages payable: they hold
many parties at once) and aren't a party's already. Expenses and income are never offered.
Cash funds named for a bank or a person are offered too (X7, X8): «تنخواه گردان حساب بانک
شهر» as a bank (its account its code, so the bank statement import offers it), «تنخواه گردان
خانم آیت الهی» as an employee holding cash; a plain «صندوق» stays cash.

``make_parties`` turns the ones picked into parties tied to their account
(``Entity.account_id``) and links every past entry on it. ``_link_new_entries`` links each
new one, whatever path posts it, so the dashboards, the assistant and the window all see
the party. The window reads debtor, creditor and balance off the account itself
(``entity_statement.control_spec_for``).
"""
from __future__ import annotations

import uuid

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.money import plain
from app.models.account import Account, AccountLevel
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine

PARTY_TYPES = ("client", "supplier", "employee", "shareholder")
# what a cash fund can become (scenarios X7, X8): a bank account kept as petty cash, or cash a person holds
FUND_TYPES = ("bank", "employee")
# a party's role on the entries it is linked to (the voucher form's roles)
_ROLE = {"client": "client", "supplier": "supplier", "employee": "payee", "shareholder": "shareholder", "bank": "bank"}

# The balance-sheet lines whose accounts can be people or organisations.
_IR_LINES = frozenset({"ca_trade_receivables", "ca_other", "nca_lt_receivables", "cl_trade_payables", "cl_advances",
                       "cl_st_loans", "cl_other", "ncl_lt_payables", "ncl_lt_loans"})
_UK_LINES = frozenset({"ca_debtors", "cl_creditors", "ncl_creditors"})
_LIABILITY_SECTIONS = ("current_liabilities", "non_current_liabilities", "equity")
_CASH_LINES = frozenset({"ca_cash"})
# a fund's name says whose money it is: a bank's account, or a person holding cash
_BANK_WORDS = ("بانک", "بنک", "بنك", "bank", "banco")
_PERSON_WORDS = ("خانم", "آقای", "اقای", "آقا ", "دکتر", "مهندس", "mr ", "mr.", "mrs", "ms ", "ms.", "miss ",
                 "sr.", "sra.", "السيد", "السيدة")


def _fund_kind(name: str) -> str | None:
    """«تنخواه گردان حساب بانک شهر» → bank; «تنخواه گردان خانم آیت الهی» → employee; «صندوق» → None."""
    folded = (name or "").replace("ي", "ی").replace("ك", "ک").lower() + " "
    if any(w in folded for w in _BANK_WORDS):
        return "bank"
    if any(w in folded for w in _PERSON_WORDS):
        return "employee"
    return None


def _line_of(db: Session, code: str, *, uk: bool, lines: dict) -> tuple[str, str] | None:
    """(section, line) of an account on the balance sheet, as the company's statements place it."""
    if uk:
        from app.services.reporting.uk_statement_service import _bs_bucket_for_code
        return _bs_bucket_for_code(code)
    from app.services.reporting.iran_statement_service import _bs_bucket_for_code
    return _bs_bucket_for_code(code, lines)


def liability_side(db: Session, code: str) -> bool:
    """True when the account is credit-normal: a positive balance is what we owe."""
    from app.services.locale_service import get_reporting_locale
    from app.services.reporting.statement_lines import line_map
    uk = get_reporting_locale(db) == "uk"
    found = _line_of(db, code, uk=uk, lines={} if uk else line_map(db))
    return bool(found) and found[0] in _LIABILITY_SECTIONS


def _role_codes(db: Session) -> set[str]:
    """The accounts the posting roles use: control accounts, never one party's."""
    from app.services.account_resolver import find_account_code
    from app.services.posting_roles import CATEGORIES
    out: set[str] = set()
    for cat in CATEGORIES:
        try:
            code = find_account_code(db, cat)
        except Exception:  # noqa: BLE001 — a category this chart doesn't know
            code = None
        if code:
            out.add(code)
    return out


def candidates(db: Session) -> list[dict]:
    """The accounts that could be parties, by code, with their balances (X1)."""
    from app.services.locale_service import get_reporting_locale
    from app.services.reporting.statement_lines import lines_and_banks

    uk = get_reporting_locale(db) == "uk"
    lines, banks = ({}, set()) if uk else lines_and_banks(db)
    if uk:
        banks = {c for (c,) in db.execute(select(Entity.code).where(Entity.type == "bank", Entity.code.is_not(None)))}
    roles = _role_codes(db)
    taken = {a for (a,) in db.execute(select(Entity.account_id).where(Entity.account_id.is_not(None)))}
    parents = {p for (p,) in db.execute(select(Account.parent_id).where(Account.parent_id.is_not(None)).distinct())}
    names = dict(db.execute(select(Account.id, Account.name)).all())
    sums = {
        aid: (n, plain(d or 0) - plain(c or 0))
        for aid, n, d, c in db.execute(
            select(TransactionLine.account_id, func.count(TransactionLine.id),
                   func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(Transaction.deleted_at.is_(None))
            .group_by(TransactionLine.account_id))
    }
    out = []
    for acc in db.execute(select(Account).where(Account.is_active.is_(True)).order_by(Account.code)).scalars():
        if acc.level == AccountLevel.GROUP or acc.id in parents or acc.id in taken:
            continue
        if acc.code in roles or acc.code in banks or acc.id not in sums:
            continue
        found = _line_of(db, acc.code, uk=uk, lines=lines)
        n, net = sums[acc.id]
        group = names.get(acc.parent_id) if acc.parent_id else None
        if found and found[1] in _CASH_LINES:
            # a cash fund named for a bank or a person (X7); a plain cash box stays cash
            kind = _fund_kind(acc.name)
            if kind:
                out.append({"account_id": str(acc.id), "code": acc.code, "name": acc.name, "group": group,
                            "side": "cash", "balance": net, "entries": n, "suggested_type": kind})
            continue
        if not found or found[1] not in (_UK_LINES if uk else _IR_LINES):
            continue
        owes_us = found[0] not in _LIABILITY_SECTIONS
        out.append({
            "account_id": str(acc.id), "code": acc.code, "name": acc.name, "group": group,
            "side": "receivable" if owes_us else "payable",
            # in the account's natural sense: what they owe us, or what we owe them
            "balance": net if owes_us else -net,
            "entries": n,
            "suggested_type": "client" if owes_us else "supplier",
        })
    return out


class PartyAccountError(ValueError):
    pass


def make_parties(db: Session, picks: list[dict]) -> list[Entity]:
    """Each picked account becomes a party tied to it, its past entries linked (X2).
    ``picks``: ``{"account_id", "type", "name"?}``. Only offered accounts can be picked."""
    offered = {c["account_id"]: c for c in candidates(db)}
    made: list[Entity] = []
    for pick in picks:
        aid = str(pick.get("account_id") or "")
        typ = (pick.get("type") or "").strip().lower()
        if aid not in offered:
            raise PartyAccountError(f"Account {aid} can't be made a party: it isn't a debtor or creditor "
                                    "account with entries, or it is a party's already.")
        allowed = FUND_TYPES if offered[aid]["side"] == "cash" else PARTY_TYPES
        if typ not in allowed:
            raise PartyAccountError(f"Account {offered[aid]['code']} can become a {' or '.join(allowed)}, not a {typ}.")
        acc = db.get(Account, uuid.UUID(aid))
        name = (pick.get("name") or "").strip() or acc.name
        # a bank's code is its ledger account: the statement import and the forms find it by it (X8)
        entity = Entity(type=typ, name=name[:256], account_id=acc.id, code=acc.code if typ == "bank" else None)
        db.add(entity)
        db.flush()
        txn_ids = {t for (t,) in db.execute(
            select(TransactionLine.transaction_id).where(TransactionLine.account_id == acc.id).distinct())}
        linked = {t for (t,) in db.execute(
            select(TransactionEntity.transaction_id).where(TransactionEntity.entity_id == entity.id))}
        for tid in sorted(txn_ids - linked, key=str):
            db.add(TransactionEntity(transaction_id=tid, entity_id=entity.id, role=_ROLE[typ]))
        made.append(entity)
        offered.pop(aid)
    db.flush()
    return made


@event.listens_for(Session, "before_flush")
def _link_new_entries(session: Session, _ctx, _instances) -> None:
    """A new entry on a party's own account is linked to the party (X4), whichever path
    posts it: the voucher form, the chat, an invoice, an import."""
    new_lines = [o for o in session.new if isinstance(o, TransactionLine) and o.account_id is not None]
    if not new_lines:
        return
    with session.no_autoflush:
        owners: dict = {}
        for eid, aid, typ in session.execute(
                select(Entity.id, Entity.account_id, Entity.type)
                .where(Entity.account_id.in_({ln.account_id for ln in new_lines}))):
            owners.setdefault(aid, []).append((eid, typ))
        if not owners:
            return
        have = {(o.transaction_id, o.entity_id) for o in session.new if isinstance(o, TransactionEntity)}
        for line in new_lines:
            for eid, typ in owners.get(line.account_id, ()):
                txn = line.transaction if line.transaction_id is None else None
                if txn is not None and txn.id is None:
                    txn.id = uuid.uuid4()
                tid = line.transaction_id or (txn.id if txn is not None else None)
                if tid is None or (tid, eid) in have:
                    continue
                stored = session.execute(select(TransactionEntity.id).where(
                    TransactionEntity.transaction_id == tid, TransactionEntity.entity_id == eid)).first()
                if stored is None:
                    session.add(TransactionEntity(transaction_id=tid, entity_id=eid, role=_ROLE.get(typ, typ),
                                                  company_id=line.company_id))
                have.add((tid, eid))

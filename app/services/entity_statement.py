"""Statement of account for a counterparty (صورتحساب طرف حساب).

The entity "View transactions" table used to show the journal's *total*
debit and credit plus an opaque "this entity" share. A tester rightly asked
for what every bank statement and supplier statement shows instead:

* **Debtor** (بدهکار) — the entity paid / gave something in this journal:
  money left the bank, the customer settled an invoice, the supplier
  delivered goods we now owe for.
* **Creditor** (بستانکار) — the entity received something: money landed in
  the bank, the customer took goods on credit, the supplier got paid.
* **Remaining** (مانده) — the running balance after the journal, in the
  entity's natural sense: the bank's balance, what a customer owes us,
  what we owe a supplier / employee / shareholder.

Each movement is read off the lines on the entity's *control account* —
the bank's own GL account, trade debtors for a client, trade creditors for
suppliers and payees, the equity claim accounts for a shareholder — so it
follows the active chart (UK or Iranian) instead of hardcoded codes. When a
journal is linked to the entity but has no control-account line (a cash
sale linked to a customer, say) we fall back to the link's recorded share,
then to the direction of the journal's cash leg; a journal we can't place
shows blank columns rather than a guessed figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.entity import Entity
from app.models.transaction import Transaction

# Entities whose control account is credit-normal: a positive remaining
# balance means "we owe them".
_LIABILITY_SIDE = frozenset({"supplier", "employee", "payee", "shareholder"})


@dataclass(frozen=True)
class EntityMovement:
    paid: int          # Debtor column — the entity paid / gave
    received: int      # Creditor column — the entity received
    balance: int       # Remaining — running balance after this journal
    placed: bool       # False when neither control line nor fallback applied


@dataclass(frozen=True)
class ControlSpec:
    """How to recognise the entity's own lines in a journal."""
    codes: frozenset[str]        # exact account codes
    prefixes: tuple[str, ...]    # or code prefixes (AP sub-ledger)
    liability_side: bool         # credit-normal control account

    def matches(self, code: str) -> bool:
        return code in self.codes or (bool(self.prefixes) and code.startswith(self.prefixes))


def control_spec_for(db: Session, entity: Entity) -> ControlSpec:
    """Resolve the control account(s) for ``entity`` on the active chart."""
    from app.services.account_resolver import resolve_account_code

    etype = (entity.type or "").strip().lower()
    if etype == "bank":
        own = (entity.code or "").strip()
        if own and db.execute(select(Account.id).where(Account.code == own)).first():
            return ControlSpec(frozenset({own}), (), False)
        return ControlSpec(frozenset({resolve_account_code(db, "bank")}), (), False)
    if etype == "client":
        return ControlSpec(frozenset({resolve_account_code(db, "ar")}), (), False)
    if etype in ("supplier", "employee", "payee"):
        ap = resolve_account_code(db, "ap")
        codes = {ap}
        for cat in ("paye_payable", "social_security_payable", "wages_payable", "net_wages_payable"):
            try:
                codes.add(resolve_account_code(db, cat))
            except Exception:  # category unknown on this chart
                pass
        return ControlSpec(frozenset(codes), (ap[:2],), True)
    if etype == "shareholder":
        codes: set[str] = set()
        for cat in ("share_capital", "dividends_payable", "shareholder_current"):
            try:
                codes.add(resolve_account_code(db, cat))
            except Exception:
                pass
        return ControlSpec(frozenset(codes), (), True)
    # Unknown type: nothing to match on; fallbacks decide.
    return ControlSpec(frozenset(), (), False)


def _movement_for(
    txn: Transaction,
    entity_id,
    spec: ControlSpec,
    is_cash: Callable[[str], bool],
) -> tuple[int, int, bool]:
    """Return (paid, received, placed) for one journal, before running balance."""
    debit = credit = 0
    hit = False
    for line in txn.lines:
        code = line.account.code if line.account is not None else ""
        if code and spec.matches(code):
            hit = True
            debit += int(line.debit or 0)
            credit += int(line.credit or 0)
    if hit:
        # Asset-side control (bank, client): a credit means value left the
        # entity's account (bank paid / customer settled) → Debtor; a debit
        # means it came in (bank received / customer took goods) → Creditor.
        # Liability-side (supplier, payee): a credit means they gave us value
        # (a bill) → Debtor; a debit means they got paid → Creditor. Both
        # cases read the same way off the lines.
        return credit, debit, True

    # Fallback 1: the link's recorded share (+ = debit on the entity's side),
    # e.g. a migration opening journal split across counterparties.
    for link in txn.entity_links or []:
        if link.entity_id == entity_id and link.amount is not None:
            amt = int(link.amount)
            return (abs(amt), 0, True) if amt < 0 else (0, amt, True)

    # Fallback 2: a journal linked to the entity with no receivable/payable
    # leg is a cash deal settled on the spot — a cash sale to a customer or a
    # cash purchase from a supplier. The statement shows both sides (they
    # paid and they received) and the balance is unchanged.
    cash_moved = 0
    for line in txn.lines:
        code = line.account.code if line.account is not None else ""
        if code and is_cash(code):
            cash_moved += abs(int(line.debit or 0) - int(line.credit or 0))
    if cash_moved:
        return cash_moved, cash_moved, True
    return 0, 0, False


def build_entity_statement(
    db: Session,
    entity: Entity,
    transactions: Iterable[Transaction],
) -> list[EntityMovement]:
    """Per-journal Debtor / Creditor / Remaining for ``transactions``, which
    must already be in statement order (date, then id). Balance is the
    entity's natural balance: asset-side entities grow with "received",
    liability-side entities grow with "paid" (what they gave us)."""
    from app.services.cash_service import cash_account_predicate
    from app.services.locale_service import get_reporting_locale

    spec = control_spec_for(db, entity)
    is_cash = cash_account_predicate(get_reporting_locale(db))
    running = 0
    out: list[EntityMovement] = []
    for txn in transactions:
        paid, received, placed = _movement_for(txn, entity.id, spec, is_cash)
        if placed:
            running += (paid - received) if spec.liability_side else (received - paid)
        out.append(EntityMovement(paid=paid, received=received, balance=running, placed=placed))
    return out


def control_account_code(db: Session, entity: Entity) -> str | None:
    """The single best control account for prefilling a manual entry."""
    spec = control_spec_for(db, entity)
    if len(spec.codes) == 1:
        return next(iter(spec.codes))
    from app.services.account_resolver import resolve_account_code

    etype = (entity.type or "").strip().lower()
    cat = {"supplier": "ap", "employee": "ap", "payee": "ap", "client": "ar", "bank": "bank"}.get(etype)
    if cat:
        try:
            return resolve_account_code(db, cat)
        except Exception:
            return None
    return None

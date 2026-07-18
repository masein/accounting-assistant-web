"""Shareholder equity postings — contributions (آورده), capital increases
(افزایش سرمایه), dividends (سود سهام), and shareholder current accounts
(حساب جاری). Each produces a balanced double-entry GL transaction, tags an
EquityEvent (so the changes-in-equity statement shows real movements), links the
shareholder via a role="shareholder" TransactionEntity (the تفضیلی sub-ledger),
and keeps the company's registered capital in step.

All amounts are integer minor units. Account codes are resolved per active
locale via ``account_resolver`` (Iran + UK), never hardcoded.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.equity import EquityEvent, Shareholding
from app.models.transaction import Transaction
from app.schemas.entity import EntityLink
from app.schemas.transaction import TransactionCreate, TransactionLineCreate
from app.services.account_resolver import resolve_account_code


class EquityError(Exception):
    """Business-rule failure in an equity operation (surfaced as 422/ToolError)."""


@dataclass
class EquityPostingResult:
    transaction_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)  # human-readable DR/CR
    allocations: list[dict] = field(default_factory=list)   # [{entity_id, entity_name, amount}]
    registered_capital: int | None = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _company_row(db: Session):
    from app.services.fx_service import _current_company_row
    return _current_company_row(db)


def _reporting_currency(db: Session) -> str | None:
    try:
        from app.services.fx_service import get_reporting_currency
        return get_reporting_currency(db)
    except Exception:
        return None


def _require_entity(db: Session, entity_id, *, want_shareholder: bool = False) -> Entity:
    ent = db.get(Entity, entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id)))
    if ent is None:
        raise EquityError(f"Shareholder not found: {entity_id}")
    if want_shareholder and (ent.type or "").lower() != "shareholder":
        raise EquityError(
            f"'{ent.name}' is type '{ent.type}', not a shareholder. Register them as a "
            f"shareholder first (they are equity holders, never employees)."
        )
    return ent


def _post(
    db: Session,
    *,
    txn_date: date_type,
    description: str,
    reference: str | None,
    lines: list[tuple[str, int, int, str | None]],  # (account_code, debit, credit, line_desc)
    shareholder_id=None,
) -> Transaction:
    """Build + persist one balanced journal via the canonical posting path."""
    from app.api.transactions import _create_transaction_from_payload

    payload = TransactionCreate(
        date=txn_date,
        reference=reference,
        description=description,
        currency=_reporting_currency(db),
        lines=[
            TransactionLineCreate(account_code=c, debit=d, credit=cr, line_description=ld)
            for (c, d, cr, ld) in lines
        ],
        entity_links=(
            [EntityLink(role="shareholder", entity_id=shareholder_id)] if shareholder_id else []
        ),
    )
    return _create_transaction_from_payload(db, payload)


def _tag(
    db: Session,
    *,
    event_type: str,
    txn_date: date_type,
    amount: int,
    transaction: Transaction | None,
    entity_id=None,
    funded_from: str | None = None,
    group_ref: str | None = None,
    description: str | None = None,
) -> EquityEvent:
    ev = EquityEvent(
        event_type=event_type,
        date=txn_date,
        amount=int(amount),
        entity_id=entity_id,
        transaction_id=(transaction.id if transaction is not None else None),
        funded_from=funded_from,
        group_ref=group_ref,
        description=description,
    )
    db.add(ev)
    db.flush()
    return ev


def _bump_registered_capital(db: Session, delta: int) -> int | None:
    company = _company_row(db)
    if company is None:
        return None
    company.registered_capital = int(company.registered_capital or 0) + int(delta)
    db.flush()
    return company.registered_capital


def _positive(amount: int, what: str = "Amount") -> int:
    amount = int(amount or 0)
    if amount <= 0:
        raise EquityError(f"{what} must be a positive number.")
    return amount


# --------------------------------------------------------------------------- #
# cap-table allocation
# --------------------------------------------------------------------------- #
def allocate_by_cap_table(db: Session, total: int) -> list[tuple[uuid.UUID, int]]:
    """Split ``total`` across shareholders by ownership (percent, else shares).

    Rounds to whole minor units; the largest holder absorbs the rounding
    remainder so the allocation sums to exactly ``total``.
    """
    total = _positive(total, "Dividend total")
    holdings = db.execute(select(Shareholding)).scalars().all()
    weights: list[tuple[uuid.UUID, float]] = []
    for h in holdings:
        w = float(h.percent) if h.percent is not None else (float(h.shares) if h.shares else 0.0)
        if w > 0:
            weights.append((h.entity_id, w))
    if not weights:
        raise EquityError(
            "No shareholdings with ownership to allocate to. Add shareholders to the cap "
            "table (with a percent or share count) first."
        )
    weights.sort(key=lambda x: x[1], reverse=True)  # largest holder absorbs the remainder
    tw = sum(w for _, w in weights)
    allocs: list[tuple[uuid.UUID, int]] = []
    allocated = 0
    for i, (eid, w) in enumerate(weights):
        if i == len(weights) - 1:
            amt = total - allocated
        else:
            amt = int(round(total * w / tw))
            allocated += amt
        allocs.append((eid, amt))
    return allocs


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
def contribution(
    db: Session,
    *,
    entity_id,
    amount: int,
    txn_date: date_type,
    to_capital: bool = True,
    asset_account_code: str | None = None,
    reference: str | None = None,
) -> EquityPostingResult:
    """آورده — a shareholder injects cash (or an asset) into the company.

    to_capital=True  → DR bank/asset  / CR share capital (raises registered capital)
    to_capital=False → DR bank/asset  / CR shareholder current account (not yet capitalised)
    """
    amount = _positive(amount)
    ent = _require_entity(db, entity_id, want_shareholder=True)
    debit_code = (asset_account_code or "").strip() or resolve_account_code(db, "bank")
    if to_capital:
        credit_code = resolve_account_code(db, "share_capital")
        credit_desc = f"Capital contribution — {ent.name}"
    else:
        credit_code = resolve_account_code(db, "shareholder_current")
        credit_desc = f"Shareholder contribution (uncapitalised) — {ent.name}"

    txn = _post(
        db, txn_date=txn_date, reference=reference,
        description=f"Shareholder contribution — {ent.name}",
        lines=[
            (debit_code, amount, 0, f"Contribution received — {ent.name}"),
            (credit_code, 0, amount, credit_desc),
        ],
        shareholder_id=ent.id,
    )
    ev = _tag(db, event_type="contribution", txn_date=txn_date, amount=amount,
              transaction=txn, entity_id=ent.id, funded_from="cash",
              description=f"Contribution — {ent.name}")
    reg = _bump_registered_capital(db, amount) if to_capital else None
    return EquityPostingResult(
        transaction_ids=[str(txn.id)], event_ids=[str(ev.id)],
        summary_lines=[f"DR {debit_code} {amount:,}", f"CR {credit_code} {amount:,}"],
        allocations=[{"entity_id": str(ent.id), "entity_name": ent.name, "amount": amount}],
        registered_capital=reg,
    )


def capital_increase(
    db: Session,
    *,
    amount: int,
    txn_date: date_type,
    source: str = "retained_earnings",
    entity_id=None,
    reference: str | None = None,
) -> EquityPostingResult:
    """افزایش سرمایه — raise registered share capital.

    source="retained_earnings" → DR retained earnings / CR share capital
    source="cash"              → DR bank             / CR share capital (optionally per shareholder)
    """
    amount = _positive(amount)
    if source not in ("retained_earnings", "cash", "revaluation_surplus"):
        raise EquityError("Capital increase source must be retained_earnings, cash, or revaluation_surplus.")
    capital_code = resolve_account_code(db, "share_capital")
    if source == "cash":
        debit_code = resolve_account_code(db, "bank")
        debit_desc = "Cash for capital increase"
    elif source == "revaluation_surplus":
        # revaluation reserve → share capital (uk 3020; ir falls back via resolver)
        debit_code = "3020"
        debit_desc = "Revaluation surplus capitalised"
    else:
        debit_code = resolve_account_code(db, "retained_earnings")
        debit_desc = "Retained earnings capitalised"

    ent = _require_entity(db, entity_id, want_shareholder=True) if entity_id else None
    txn = _post(
        db, txn_date=txn_date, reference=reference,
        description="Capital increase",
        lines=[
            (debit_code, amount, 0, debit_desc),
            (capital_code, 0, amount, "Increase in share capital"),
        ],
        shareholder_id=(ent.id if ent else None),
    )
    ev = _tag(db, event_type="capital_increase", txn_date=txn_date, amount=amount,
              transaction=txn, entity_id=(ent.id if ent else None), funded_from=source,
              description=f"Capital increase from {source}")
    reg = _bump_registered_capital(db, amount)
    return EquityPostingResult(
        transaction_ids=[str(txn.id)], event_ids=[str(ev.id)],
        summary_lines=[f"DR {debit_code} {amount:,}", f"CR {capital_code} {amount:,}"],
        registered_capital=reg,
    )


def declare_dividend(
    db: Session,
    *,
    total_amount: int,
    txn_date: date_type,
    allocations: list[tuple] | None = None,
    reference: str | None = None,
) -> EquityPostingResult:
    """سود سهام مصوب — declare a dividend from retained earnings, allocated by
    the cap table (or an explicit allocation). Posts ONE transaction per
    shareholder (DR retained earnings / CR dividends payable) so each holder's
    تفضیلی sub-ledger attributes their share exactly. Grouped by a shared ref.
    """
    total_amount = _positive(total_amount, "Dividend total")
    if allocations:
        allocs = [(uuid.UUID(str(e)) if not isinstance(e, uuid.UUID) else e, int(a)) for e, a in allocations]
        if sum(a for _, a in allocs) != total_amount:
            raise EquityError("Explicit dividend allocations must sum to the declared total.")
    else:
        allocs = allocate_by_cap_table(db, total_amount)

    retained_code = resolve_account_code(db, "retained_earnings")
    payable_code = resolve_account_code(db, "dividends_payable")
    group_ref = reference or f"DIV-{uuid.uuid4().hex[:8].upper()}"

    result = EquityPostingResult()
    for eid, amt in allocs:
        if amt <= 0:
            continue
        ent = _require_entity(db, eid, want_shareholder=True)
        txn = _post(
            db, txn_date=txn_date, reference=group_ref,
            description=f"Dividend declared — {ent.name}",
            lines=[
                (retained_code, amt, 0, f"Dividend to {ent.name}"),
                (payable_code, 0, amt, f"Dividend payable — {ent.name}"),
            ],
            shareholder_id=ent.id,
        )
        ev = _tag(db, event_type="dividend_declared", txn_date=txn_date, amount=amt,
                  transaction=txn, entity_id=ent.id, group_ref=group_ref,
                  description=f"Dividend declared — {ent.name}")
        result.transaction_ids.append(str(txn.id))
        result.event_ids.append(str(ev.id))
        result.allocations.append({"entity_id": str(ent.id), "entity_name": ent.name, "amount": amt})

    result.summary_lines = [
        f"DR {retained_code} {total_amount:,} (retained earnings)",
        f"CR {payable_code} {total_amount:,} (dividends payable, split "
        + ", ".join(f"{a['entity_name']} {a['amount']:,}" for a in result.allocations) + ")",
    ]
    return result


def pay_dividend(
    db: Session,
    *,
    entity_id,
    amount: int,
    txn_date: date_type,
    bank_account_code: str | None = None,
    reference: str | None = None,
) -> EquityPostingResult:
    """Pay a declared dividend: DR dividends payable / CR bank, linked to the
    shareholder (reduces their outstanding dividend)."""
    amount = _positive(amount)
    ent = _require_entity(db, entity_id, want_shareholder=True)
    payable_code = resolve_account_code(db, "dividends_payable")
    bank_code = (bank_account_code or "").strip() or resolve_account_code(db, "bank")
    txn = _post(
        db, txn_date=txn_date, reference=reference,
        description=f"Dividend paid — {ent.name}",
        lines=[
            (payable_code, amount, 0, f"Dividend settled — {ent.name}"),
            (bank_code, 0, amount, f"Dividend paid — {ent.name}"),
        ],
        shareholder_id=ent.id,
    )
    ev = _tag(db, event_type="dividend_paid", txn_date=txn_date, amount=amount,
              transaction=txn, entity_id=ent.id, description=f"Dividend paid — {ent.name}")
    return EquityPostingResult(
        transaction_ids=[str(txn.id)], event_ids=[str(ev.id)],
        summary_lines=[f"DR {payable_code} {amount:,}", f"CR {bank_code} {amount:,}"],
        allocations=[{"entity_id": str(ent.id), "entity_name": ent.name, "amount": amount}],
    )


def shareholder_current_account(
    db: Session,
    *,
    entity_id,
    amount: int,
    txn_date: date_type,
    direction: str,
    bank_account_code: str | None = None,
    reference: str | None = None,
) -> EquityPostingResult:
    """حساب جاری شرکا — a shareholder lends to ("in") or withdraws from ("out")
    the company, settled against the bank."""
    amount = _positive(amount)
    direction = (direction or "").strip().lower()
    if direction not in ("in", "out"):
        raise EquityError("Current-account direction must be 'in' (lends) or 'out' (withdraws).")
    ent = _require_entity(db, entity_id, want_shareholder=True)
    current_code = resolve_account_code(db, "shareholder_current")
    bank_code = (bank_account_code or "").strip() or resolve_account_code(db, "bank")
    if direction == "in":  # shareholder lends → DR bank / CR current account
        lines = [
            (bank_code, amount, 0, f"Loan from {ent.name}"),
            (current_code, 0, amount, f"Owed to {ent.name} (current account)"),
        ]
        event_type = "current_account_in"
    else:  # shareholder withdraws → DR current account / CR bank
        lines = [
            (current_code, amount, 0, f"Withdrawal by {ent.name} (current account)"),
            (bank_code, 0, amount, f"Paid to {ent.name}"),
        ]
        event_type = "current_account_out"
    txn = _post(
        db, txn_date=txn_date, reference=reference,
        description=f"Shareholder current account ({direction}) — {ent.name}",
        lines=lines, shareholder_id=ent.id,
    )
    ev = _tag(db, event_type=event_type, txn_date=txn_date, amount=amount,
              transaction=txn, entity_id=ent.id, description=f"Current account {direction} — {ent.name}")
    return EquityPostingResult(
        transaction_ids=[str(txn.id)], event_ids=[str(ev.id)],
        summary_lines=[f"DR {lines[0][0]} {amount:,}", f"CR {lines[1][0]} {amount:,}"],
        allocations=[{"entity_id": str(ent.id), "entity_name": ent.name, "amount": amount}],
    )

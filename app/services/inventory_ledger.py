"""Stock in the books: perpetual inventory postings (docs/PLAN_INVENTORY_GL.md).

Stock is valued by ``inventory_costing`` (weighted average or FIFO, recomputed
from every movement). Once a company switches this on, from a date it picks,
the ledger's stock account follows that valuation:

* the stock on hand the day before is brought onto the books once (``open``);
* each month (in the company's calendar) gets at most two journals per sync:
  the cost of what invoices sold (``cogs``: cost of sales debit, stock
  credit), and everything else that moved the stock's value (``adjust``:
  movements entered by hand, production's rounding, an invoice's posted
  value against the engine's) through the stock adjustment account;
* re-running posts only the difference against ``inventory_postings``, so a
  back-dated movement is corrected in every open month. A closed month is
  never touched; whatever changed there (a costing method switch) is caught
  up in the first open month.

A purchase invoice's own stock lines (phase G2) post their cost with the
invoice and keep it on the movement (``ledger_value``); the sync leaves that
part alone. So in every open month: stock's change in the books = what
invoices posted + ``adjust`` − ``cogs`` = the valuation's change.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import plain, to_decimal
from app.models.app_setting import AppSetting
from app.models.inventory import InventoryMovement, InventoryPosting

SETTINGS_KEY = "inventory_ledger"
OPEN, COGS, ADJUST, CATCHUP = "open", "cogs", "adjust", "catchup"


# --- the switch -------------------------------------------------------------------------------------------------

def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).scalars().first()


def get_settings(db: Session) -> dict[str, Any]:
    row = _row(db)
    try:
        data = json.loads(row.value) if row and row.value else {}
    except (TypeError, ValueError):
        data = {}
    return {"enabled": bool(data.get("enabled")), "from": data.get("from")}


def start_date(db: Session) -> date | None:
    """The day stock starts posting, or None while it's off."""
    s = get_settings(db)
    if not s["enabled"] or not s["from"]:
        return None
    return date.fromisoformat(s["from"])


def _save(db: Session, enabled: bool, start: str | None) -> None:
    value = json.dumps({"enabled": enabled, "from": start})
    row = _row(db)
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    db.flush()


def set_enabled(db: Session, enabled: bool, from_date: date | None = None, *, today: date | None = None) -> dict:
    """Switch stock posting on from ``from_date`` (the first time), back on
    (from the same date: what was posted stays), or off (nothing is undone;
    later movements just stop posting). The start date can't move once set:
    the opening entry was posted at it."""
    from app.services.period_service import assert_period_open
    today = today or date.today()
    current = get_settings(db)
    if not enabled:
        _save(db, False, current["from"])
        return get_settings(db)
    if current["from"]:
        if from_date is not None and from_date.isoformat() != current["from"]:
            raise HTTPException(status_code=422, detail=(
                f"Stock has posted from {current['from']}; that date can't move. "
                "Switch it back on from the same date."))
        start = date.fromisoformat(current["from"])
    else:
        if from_date is None:
            raise HTTPException(status_code=422, detail="Pick the date stock starts posting from.")
        if from_date > today:
            raise HTTPException(status_code=422, detail="The start date can't be in the future.")
        assert_period_open(db, from_date)
        start = from_date
    _save(db, True, start.isoformat())
    sync(db, today=today)
    return get_settings(db)


def guard(db: Session, on: date) -> None:
    """While stock posts, a movement is a posting: refused in a closed period."""
    if start_date(db) is not None:
        from app.services.period_service import assert_period_open
        assert_period_open(db, on)


# --- the valuation, month by month ------------------------------------------------------------------------------

@dataclass
class _Period:
    key: str                 # "open", or the month key ("2026-10", "1405-07")
    start: date
    end: date
    value: Decimal = Decimal(0)        # stock's value at the end of the period, by the engine
    sales_cogs: Decimal = Decimal(0)   # the cost of what invoices sold
    invoiced_in: Decimal = Decimal(0)  # what invoices posted to stock themselves


def _periods(db: Session, start: date, today: date) -> list[_Period]:
    """The opening (everything before ``start``) and each month from
    ``start``'s through today's, in the company's calendar."""
    from app.services.calendar_periods import company_calendar, month_bounds_of, month_key
    cal = company_calendar(db)
    out = [_Period(OPEN, date.min, start - timedelta(days=1))]
    d = start
    while d <= today:
        m_start, m_end = month_bounds_of(d, cal)
        out.append(_Period(month_key(d, cal), max(m_start, start), m_end))
        d = m_end + timedelta(days=1)
    return out


def _replay(db: Session, periods: list[_Period], today: date) -> None:
    """Run the costing engine over every movement up to today, noting each
    period's closing value, its sales' cost and what its invoices posted."""
    from app.services import inventory_costing as ic
    method = ic.get_method(db)
    places = ic._places(db)
    apply = ic._apply_fifo if method == ic.FIFO else ic._apply_weighted
    states: dict = defaultdict(lambda: ic.ItemState(places=places))
    total = Decimal(0)
    i = 0
    for mv in ic._movements(db, today):
        while mv.movement_date > periods[i].end:
            periods[i + 1].value = total
            i += 1
        qty = ic._qty(mv.quantity)
        if qty <= 0:
            continue
        st = states[mv.item_id]
        before = Decimal(st.value)
        typ = mv.movement_type.value if hasattr(mv.movement_type, "value") else str(mv.movement_type)
        cost = Decimal(apply(st, typ, qty, Decimal(str(mv.unit_cost or 0))))
        total += Decimal(st.value) - before
        p = periods[i]
        p.value = total
        if mv.invoice_id is not None and p.key != OPEN:
            if mv.ledger_value is not None:          # a purchase's stock, or its void: the invoice posted it
                p.invoiced_in += to_decimal(mv.ledger_value)
            elif typ == ic.OUT:                      # sold
                p.sales_cogs += cost
            else:                                    # a voided sale's goods back, at their cost
                p.sales_cogs -= cost
    for j in range(i + 1, len(periods)):
        periods[j].value = total


def _effect(kind: str, amount: Decimal) -> Decimal:
    """What a posted amount did to stock: cost of sales takes it down, the rest put it up."""
    return -amount if kind == COGS else amount


def _posted(db: Session) -> dict[tuple[str, str], Decimal]:
    rows = db.execute(select(InventoryPosting.month, InventoryPosting.kind, func.sum(InventoryPosting.amount))
                      .group_by(InventoryPosting.month, InventoryPosting.kind)).all()
    return {(m, k): to_decimal(v or 0) for m, k, v in rows}


# --- posting ----------------------------------------------------------------------------------------------------

def _post(db: Session, *, on: date, kind: str, amount: Decimal, key: str, label: str):
    """One journal moving ``amount`` (signed: stock up for open, adjust and
    catchup; the cost of sales for cogs) between stock and its counterpart."""
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.account_resolver import resolve_account_code
    from app.services.book_text import bt
    from app.services.fx_base import base_currency
    from app.services.ledger_posting import create_transaction_from_payload
    stock = resolve_account_code(db, "inventory")
    other = resolve_account_code(db, "cogs" if kind == COGS else "stock_adjustment")
    up = -amount if kind == COGS else amount              # what stock went up by
    value = plain(abs(up))
    if kind == OPEN:
        text = bt(db, "stk_opening")
    elif kind == CATCHUP:
        text = bt(db, "stk_catch_up", label=label)
    else:
        text = bt(db, "stk_cogs" if kind == COGS else "stk_adjust", label=label)
    lines = ([TransactionLineCreate(account_code=stock, debit=value, credit=0, line_description=text[:512]),
              TransactionLineCreate(account_code=other, debit=0, credit=value, line_description=text[:512])]
             if up > 0 else
             [TransactionLineCreate(account_code=other, debit=value, credit=0, line_description=text[:512]),
              TransactionLineCreate(account_code=stock, debit=0, credit=value, line_description=text[:512])])
    return create_transaction_from_payload(db, TransactionCreate(
        date=on, reference="STK-OPEN" if kind == OPEN else f"STK-{key}", description=text[:2000],
        currency=base_currency(db), lines=lines))


def sync(db: Session, *, today: date | None = None) -> dict[str, Any]:
    """Post what changed since the last sync; nothing when nothing did, or
    while stock posting is off. Flushes; the caller commits.

    Targets per open period: ``open`` (the opening value), ``cogs`` (its
    sales' cost) and ``adjust`` (the rest of its change in value, less what
    its invoices posted). The first open period after a closed one also has
    ``catchup``: the valuation at the last closed period's end, less what
    invoices and the sync had posted through it — non-zero only when
    something moved a closed period's value (a costing method switch)."""
    from app.services.book_text import book_language
    from app.services.calendar_periods import company_calendar, month_label
    from app.services.period_service import get_closed_period
    today = today or date.today()
    start = start_date(db)
    if start is None:
        return {"enabled": False, "posted": []}
    periods = _periods(db, start, today)
    _replay(db, periods, today)
    posted = _posted(db)
    closed = get_closed_period(db)
    lang = "fa" if book_language(db) == "fa" else "en"
    cal = company_calendar(db)

    def on_of(p: _Period) -> date:
        return start if p.key == OPEN else min(p.end, today)

    is_closed = [closed is not None and on_of(p) <= closed for p in periods]
    targets: dict[tuple[str, str], Decimal] = {}
    invoiced = Decimal(0)            # what invoices posted to stock, through the period
    synced_closed = Decimal(0)       # what the sync posted in closed periods, as stock's change
    last_closed: _Period | None = None
    prev_value = Decimal(0)
    for p, shut in zip(periods, is_closed):
        invoiced += p.invoiced_in
        if shut:
            last_closed = p
            synced_closed += sum((_effect(k, v) for (m, k), v in posted.items() if m == p.key), Decimal(0))
            invoiced_through_closed = invoiced
            prev_value = p.value
            continue
        if p.key == OPEN:
            targets[(OPEN, OPEN)] = p.value
        else:
            targets[(p.key, COGS)] = p.sales_cogs
            targets[(p.key, ADJUST)] = p.value - prev_value + p.sales_cogs - p.invoiced_in
            if last_closed is not None:
                targets[(p.key, CATCHUP)] = last_closed.value - invoiced_through_closed - synced_closed
                last_closed = None
        prev_value = p.value

    done: list[dict[str, Any]] = []
    for p, shut in zip(periods, is_closed):
        if shut:
            continue
        on = on_of(p)
        label = p.key if p.key == OPEN else (month_label(p.key, lang) if cal == "jalali" else p.key)
        for k in (OPEN, CATCHUP, COGS, ADJUST):
            if (p.key, k) not in targets:
                continue
            diff = targets[(p.key, k)] - posted.get((p.key, k), Decimal(0))
            if diff == 0:
                continue
            txn = _post(db, on=on, kind=k, amount=diff, key=p.key, label=label)
            db.add(InventoryPosting(month=p.key, kind=k, amount=plain(diff), transaction_id=txn.id))
            done.append({"period": p.key, "kind": k, "amount": plain(diff), "date": on.isoformat()})
    db.flush()
    return {"enabled": True, "from": start.isoformat(), "posted": done}


# --- invoices (phase G2) ---------------------------------------------------------------------------------------

def stock_lines(db: Session, inv) -> list:
    """The lines of ``inv`` that move stock: those with an item, on an invoice
    dated from the start (before it, and while off, invoices work as they
    always did)."""
    start = start_date(db)
    if start is None or inv.issue_date is None or inv.issue_date < start:
        return []
    return [it for it in (inv.items or []) if getattr(it, "inventory_item_id", None)]


def assert_rate_known(db: Session, inv) -> None:
    """Stock is valued in the base currency: a purchase in another currency
    needs that day's rate before its stock lines can post."""
    from app.services.fx_base import base_currency, rate_to_base
    ccy = (inv.currency or "").upper()
    if ccy and ccy != base_currency(db).upper() and not rate_to_base(db, ccy, inv.issue_date):
        raise HTTPException(status_code=422, detail=(
            f"Add the exchange rate for {ccy} on {inv.issue_date.isoformat()} first: "
            "stock is kept in the base currency."))


def record_invoice(db: Session, inv, lines: list, txn=None) -> None:
    """The movements an invoice's stock lines make, once it is recognised.

    A purchase's lines come in at what the invoice posted to stock, in the base
    currency: read back from its journal (``fx_base`` stamped it), shared out
    over the lines, the last taking the rounding. A sale's lines go out, and
    the sync posts their cost."""
    from app.models.account import Account
    from app.models.inventory import InventoryMovementType
    from app.models.transaction import TransactionLine
    if not lines:
        return
    existing = db.execute(select(func.count()).select_from(InventoryMovement)
                          .where(InventoryMovement.invoice_id == inv.id)).scalar()
    if existing:
        return
    if inv.kind == "purchase":
        from app.services.account_resolver import resolve_account_code
        code = resolve_account_code(db, "inventory")
        base = db.execute(select(func.sum(TransactionLine.base_debit), func.count(TransactionLine.id),
                                 func.count(TransactionLine.base_debit))
                          .join(Account, Account.id == TransactionLine.account_id)
                          .where(TransactionLine.transaction_id == txn.id, Account.code == code)).one()
        if base[1] and base[2] < base[1]:
            raise HTTPException(status_code=422, detail=(
                f"Add the exchange rate for {inv.currency} on {inv.issue_date.isoformat()} first: "
                "stock is kept in the base currency."))
        total = to_decimal(base[0] or 0)
        own = [to_decimal(it.line_total or 0) for it in lines]
        whole = sum(own) or Decimal(1)
        from app.services import inventory_costing as ic
        places = ic._places(db)
        shares, given = [], Decimal(0)
        for i, amount in enumerate(own):
            share = total - given if i == len(own) - 1 else (total * amount / whole).quantize(Decimal(1).scaleb(-places))
            shares.append(share)
            given += share
        for it, value in zip(lines, shares):
            qty = to_decimal(it.quantity or 0)
            db.add(InventoryMovement(
                item_id=it.inventory_item_id, movement_date=inv.issue_date, movement_type=InventoryMovementType.IN,
                quantity=it.quantity, unit_cost=plain((value / qty).quantize(Decimal("0.0001"))) if qty else 0,
                reference=inv.number, description=it.product_name, invoice_id=inv.id, ledger_value=plain(value)))
    else:
        for it in lines:
            db.add(InventoryMovement(
                item_id=it.inventory_item_id, movement_date=inv.issue_date, movement_type=InventoryMovementType.OUT,
                quantity=it.quantity, unit_cost=0, reference=inv.number, description=it.product_name,
                invoice_id=inv.id))
    db.flush()


def reverse_invoice(db: Session, inv, *, on: date) -> None:
    """A voided invoice's stock moves back on the day it is voided, as its
    journal does: each of its movements by its opposite. A purchase's goods go
    out at what it posted, a sale's come back at what they cost, and goods
    returned on its credit notes (whose journals the void reverses too) go
    the other way again. The invoice's own movements stay, as history."""
    from app.models.inventory import InventoryMovementType
    from app.services import inventory_costing as ic
    rows = db.execute(select(InventoryMovement).where(InventoryMovement.invoice_id == inv.id)
                      .order_by(InventoryMovement.created_at)).scalars().all()
    if not rows or any(r.movement_date == on and (r.reference or "").startswith("VOID-") for r in rows):
        return
    costs = ic.movement_costs(db) if any(r.ledger_value is None for r in rows) else {}

    def typ_of(mv) -> str:
        return mv.movement_type.value if hasattr(mv.movement_type, "value") else str(mv.movement_type)

    # what comes back in goes first: taking a bill's goods out before the ones
    # returned from it come back would run stock below zero, where the
    # weighted average starts again at nothing
    for mv in sorted(rows, key=lambda r: typ_of(r) != ic.OUT):
        qty = to_decimal(mv.quantity or 0)
        back_type = InventoryMovementType.IN if typ_of(mv) == ic.OUT else InventoryMovementType.OUT
        if mv.ledger_value is not None:          # the invoice (or its credit note) posted it: take that back
            unit, ledger = mv.unit_cost, plain(-to_decimal(mv.ledger_value))
        else:                                    # a sale's goods, or a customer's return: at what they cost
            cost = to_decimal(costs.get(mv.id, 0))
            unit, ledger = plain((cost / qty).quantize(Decimal("0.0001"))) if qty else 0, None
        db.add(InventoryMovement(
            item_id=mv.item_id, movement_date=on, movement_type=back_type, quantity=mv.quantity, unit_cost=unit,
            reference=f"VOID-{inv.number}", description=mv.description, invoice_id=inv.id, ledger_value=ledger))
    db.flush()


# --- goods back on a credit note (phase G3) ---------------------------------------------------------------------

def returnable(db: Session, inv) -> dict:
    """item id → what of it ``inv`` moved, what came back on its credit notes,
    and what is left to come back. Empty for an invoice that moved no stock."""
    from app.services import inventory_costing as ic
    own = ic.IN if inv.kind == "purchase" else ic.OUT
    out: dict = {}
    for mv in db.execute(select(InventoryMovement).where(InventoryMovement.invoice_id == inv.id)).scalars():
        if (mv.reference or "").startswith("VOID-"):
            continue
        typ = mv.movement_type.value if hasattr(mv.movement_type, "value") else str(mv.movement_type)
        row = out.setdefault(mv.item_id, {"moved": Decimal(0), "back": Decimal(0)})
        if mv.credit_note_id is not None:
            row["back"] += to_decimal(mv.quantity or 0)
        elif typ == own:
            row["moved"] += to_decimal(mv.quantity or 0)
    for row in out.values():
        row["left"] = max(Decimal(0), row["moved"] - row["back"])
    return {k: v for k, v in out.items() if v["moved"] > 0}


def check_returns(db: Session, inv, returns: list, on: date) -> list[tuple]:
    """``returns`` ([{item_id, quantity}]) checked against what ``inv`` can
    take back, before anything posts: [(item, quantity, net unit price)], the
    price in the invoice's currency, from its lines."""
    from app.models.inventory import InventoryItem
    if not returns:
        return []
    if start_date(db) is None:
        raise HTTPException(status_code=422, detail=(
            "Turn on stock in the books (Inventory) to take goods back on a credit note."))
    left = returnable(db, inv)
    if not left:
        raise HTTPException(status_code=422, detail=(
            f"{inv.number} moved no stock: record goods coming back on the Inventory page."))
    guard(db, on)
    wanted: dict = defaultdict(Decimal)
    for r in returns:
        wanted[r.item_id] += to_decimal(r.quantity)
    out = []
    for item_id, qty in wanted.items():
        item = db.get(InventoryItem, item_id)
        name = item.name if item else str(item_id)
        if item_id not in left:
            raise HTTPException(status_code=422, detail=(
                f"{name} wasn't on {inv.number}, so it can't come back on its credit note."))
        if qty > left[item_id]["left"]:
            raise HTTPException(status_code=422, detail=(
                f"Only {_qty_text(left[item_id]['left'])} {name} can come back on {inv.number}."))
        lines = [it for it in (inv.items or []) if it.inventory_item_id == item_id]
        n = sum((to_decimal(it.quantity or 0) for it in lines), Decimal(0))
        price = sum((to_decimal(it.line_total or 0) for it in lines), Decimal(0)) / n if n else Decimal(0)
        out.append((item, qty, price))
    return out


def _qty_text(q: Decimal) -> str:
    return format(q.normalize(), "f") if q != q.to_integral() else str(int(q))


def record_returns(db: Session, inv, note, checked: list[tuple], *, on: date, txn=None) -> None:
    """The movements of goods back on credit note ``note``. A customer's come
    in at what they cost on that sale, and the sync takes their cost of sales
    back. Ours to a supplier go out at what the credit note took off stock, in
    the base currency (read back from its journal, as a bill's are)."""
    from app.models.account import Account
    from app.models.inventory import InventoryMovementType
    from app.models.transaction import TransactionLine
    from app.services import inventory_costing as ic
    from app.services.book_text import bt
    if not checked:
        return
    if inv.kind == "purchase":
        from app.services.account_resolver import resolve_account_code
        code = resolve_account_code(db, "inventory")
        base = db.execute(select(func.sum(TransactionLine.base_credit), func.count(TransactionLine.id),
                                 func.count(TransactionLine.base_credit))
                          .join(Account, Account.id == TransactionLine.account_id)
                          .where(TransactionLine.transaction_id == txn.id, Account.code == code)).one()
        if base[1] and base[2] < base[1]:
            raise HTTPException(status_code=422, detail=(
                f"Add the exchange rate for {inv.currency} on {on.isoformat()} first: "
                "stock is kept in the base currency."))
        total = to_decimal(base[0] or 0)
        own = [qty * price for _it, qty, price in checked]
        whole = sum(own) or Decimal(1)
        places = ic._places(db)
        given = Decimal(0)
        for i, ((item, qty, _price), amount) in enumerate(zip(checked, own)):
            value = total - given if i == len(own) - 1 else (total * amount / whole).quantize(Decimal(1).scaleb(-places))
            given += value
            db.add(InventoryMovement(
                item_id=item.id, movement_date=on, movement_type=InventoryMovementType.OUT, quantity=plain(qty),
                unit_cost=plain((value / qty).quantize(Decimal("0.0001"))), reference=inv.number,
                description=bt(db, "stk_returned_out", number=inv.number), invoice_id=inv.id,
                credit_note_id=note.id, ledger_value=plain(-value)))
    else:
        costs = ic.movement_costs(db)
        sold = db.execute(select(InventoryMovement).where(
            InventoryMovement.invoice_id == inv.id, InventoryMovement.credit_note_id.is_(None),
            InventoryMovement.movement_type == InventoryMovementType.OUT)).scalars().all()
        for item, qty, _price in checked:
            mine = [mv for mv in sold if mv.item_id == item.id and not (mv.reference or "").startswith("VOID-")]
            n = sum((to_decimal(mv.quantity or 0) for mv in mine), Decimal(0))
            cost = sum((to_decimal(costs.get(mv.id, 0)) for mv in mine), Decimal(0))
            db.add(InventoryMovement(
                item_id=item.id, movement_date=on, movement_type=InventoryMovementType.IN, quantity=plain(qty),
                unit_cost=plain((cost / n).quantize(Decimal("0.0001"))) if n else 0, reference=inv.number,
                description=bt(db, "stk_returned_in", number=inv.number), invoice_id=inv.id,
                credit_note_id=note.id))
    db.flush()


# --- a stock count (phase G3) -----------------------------------------------------------------------------------

def count_sheet(db: Session, on: date) -> list[dict[str, Any]]:
    """Every active item with what is on hand on ``on``, to count against."""
    from app.services import inventory_costing as ic
    rows = ic.valuation(db, as_of=on)["rows"]
    return [{"item_id": r["item_id"], "name": r["name"], "sku": r["sku"], "unit": r["unit"],
             "on_hand": r["on_hand"], "unit_cost": r["unit_cost"]} for r in rows if r["is_active"]]


def post_count(db: Session, on: date, counted: list) -> dict[str, Any]:
    """A count ([{item_id, counted}]) against what is on hand on ``on``: one
    movement per item that differs (stock found comes in at its unit cost,
    stock missing goes out at its cost), «COUNT-<date>»; the sync posts them
    with the month's adjustments. Items not counted are left alone."""
    from app.models.inventory import InventoryItem, InventoryMovementType
    from app.services import inventory_costing as ic
    from app.services.book_text import bt
    guard(db, on)
    sheet = {r["item_id"]: r for r in count_sheet(db, on)}
    made = []
    for c in counted:
        key = str(c.item_id)
        item = db.get(InventoryItem, c.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        row = sheet.get(key, {"on_hand": 0.0, "unit_cost": 0})
        on_hand = to_decimal(row["on_hand"])
        diff = to_decimal(c.counted) - on_hand
        if diff == 0:
            continue
        mv = InventoryMovement(
            item_id=item.id, movement_date=on,
            movement_type=InventoryMovementType.IN if diff > 0 else InventoryMovementType.OUT,
            quantity=plain(abs(diff)), unit_cost=row["unit_cost"] if diff > 0 else 0,
            reference=f"COUNT-{on.isoformat()}", description=bt(db, "stk_count"))
        db.add(mv)
        made.append((mv, item, on_hand, to_decimal(c.counted), diff))
    db.flush()
    costs = ic.movement_costs(db) if made else {}
    out = [{"item_id": str(item.id), "name": item.name, "unit": item.unit, "on_hand": plain(on_hand),
            "counted": plain(n), "difference": plain(diff),
            "value": plain(to_decimal(costs.get(mv.id, 0)) * (1 if diff > 0 else -1))}
           for mv, item, on_hand, n, diff in made]
    posted = sync(db) if made else {"enabled": start_date(db) is not None, "posted": []}
    return {"date": on.isoformat(), "adjusted": out, "posted": posted.get("posted", [])}


# --- what the page shows ----------------------------------------------------------------------------------------

def books_value(db: Session, as_of: date | None = None) -> int | Decimal:
    """The stock account's balance in the base currency."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from app.services.account_resolver import find_account_code
    code = find_account_code(db, "inventory")
    if not code:
        return 0
    debit = func.coalesce(TransactionLine.base_debit, TransactionLine.debit)
    credit = func.coalesce(TransactionLine.base_credit, TransactionLine.credit)
    q = (select(func.coalesce(func.sum(debit), 0) - func.coalesce(func.sum(credit), 0))
         .join(Transaction, Transaction.id == TransactionLine.transaction_id)
         .join(Account, Account.id == TransactionLine.account_id)
         .where(Account.code == code, Transaction.deleted_at.is_(None)))
    if as_of is not None:
        q = q.where(Transaction.date <= as_of)
    return plain(db.execute(q).scalar() or 0)


def status(db: Session, *, today: date | None = None) -> dict[str, Any]:
    from app.services import inventory_costing as ic
    today = today or date.today()
    s = get_settings(db)
    engine = plain(ic.valuation(db, as_of=today)["totals"]["value"])
    books = books_value(db, today)
    last = db.execute(select(func.max(InventoryPosting.created_at))).scalar()
    return {**s, "books_value": books, "valuation": engine,
            "difference": plain(to_decimal(books) - to_decimal(engine)) if s["from"] else None,
            "last_sync": last.isoformat() if last else None}

"""Fixed-asset register (roadmap 2026-09 §4.3): schedules, month-end runs,
disposal and the register itself.

Periods follow the company's calendar — Jalali months for an Iranian company,
Gregorian months otherwise — because the Iranian rules speak of months:
depreciation starts at the beginning of the month after the asset comes into
use (art. 6 of the art. 149 rules). Methods:

* **straight line** — (cost − residual − depreciation brought over) spread
  evenly over the months of life left, the rounding carried so the total is
  exact;
* **declining balance** — an annual rate on the book value at the start of
  each asset year (twelve months from the start); once that value is below 5 %
  of cost, all of what is left goes in that year (art. 9(b)). It stops at the
  residual value.

A run posts, for every month that has ended, one journal per month and
currency: Dr depreciation expense / Cr accumulated depreciation, a line pair
per asset. A month in a closed period is caught up on the first open day. A
posted month is recorded per asset, so a run never posts it twice.

Disposal posts the months up to the one it happens in, then clears cost and
accumulated depreciation against the proceeds; the difference is the gain or
loss on disposal (art. 14: the loss is the book value less the proceeds).
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import jdatetime
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.fixed_asset import (
    ACTIVE, DECLINING_BALANCE, DISPOSED, METHODS, STRAIGHT_LINE, FixedAsset, FixedAssetDepreciation,
)
from app.services.book_text import bt

LOW_VALUE_SHARE_BPS = 500          # 5 % of cost: below it, the rest goes in one year (art. 9(b))
MAX_LIFE_MONTHS = 100 * 12

# --- categories -------------------------------------------------------------------------------------
# Iran: the rows of the art. 149 depreciation table (1395 revision) that two
# independent sources agree on; anything else is entered as "other" with the
# company's own method and life. UK: common FRS 102 book policies — capital
# allowances, not depreciation, are what HMRC uses, so these are only defaults.

@dataclass(frozen=True)
class Category:
    key: str
    label: str
    method: str
    life_months: int | None = None
    rate_bps: int | None = None
    asset_account: str | None = None          # None → the locale default
    accumulated_account: str | None = None
    statutory: bool = False


CATEGORIES: dict[str, tuple[Category, ...]] = {
    "ir": (
        Category("building_concrete", "Concrete or steel-frame buildings", STRAIGHT_LINE, 300, statutory=True),
        Category("building_other", "Brick and other buildings", STRAIGHT_LINE, 180, statutory=True),
        Category("vehicle", "Vehicles", STRAIGHT_LINE, 72, statutory=True),
        Category("vehicle_hire", "Taxis and light cargo vans", STRAIGHT_LINE, 48, statutory=True),
        Category("computer", "Software, computer systems and related hardware", STRAIGHT_LINE, 36, statutory=True),
        Category("furniture", "Office furniture and fixtures", STRAIGHT_LINE, 60, statutory=True),
        Category("other", "Other (enter the method and life)", STRAIGHT_LINE, None),
    ),
    "uk": (
        Category("buildings", "Land and buildings", STRAIGHT_LINE, 600, asset_account="0040", accumulated_account="0041"),
        Category("plant", "Plant and machinery", DECLINING_BALANCE, rate_bps=2500,
                 asset_account="0010", accumulated_account="0011"),
        Category("motor_vehicle", "Motor vehicles", DECLINING_BALANCE, rate_bps=2500,
                 asset_account="0030", accumulated_account="0031"),
        Category("office_equipment", "Office equipment, fixtures and fittings", STRAIGHT_LINE, 60,
                 asset_account="0020", accumulated_account="0021"),
        Category("computer", "Computer equipment", STRAIGHT_LINE, 36, asset_account="0020", accumulated_account="0021"),
        Category("other", "Other (enter the method and life)", STRAIGHT_LINE, None,
                 asset_account="0010", accumulated_account="0011"),
    ),
}

DEFAULT_ACCOUNTS = {
    "ir": {"asset": "1210", "accumulated": "1219", "expense": "6120", "gain": "4310", "loss": "6220"},
    "uk": {"asset": "0010", "accumulated": "0011", "expense": "8500", "gain": "4200", "loss": "7860"},
}
ACCOUNT_NAMES = {
    "ir": {"4310": "سود حاصل از فروش دارایی‌های ثابت", "6220": "زیان حاصل از فروش دارایی‌های ثابت"},
    "uk": {"4200": "Other operating income", "7860": "Loss on disposal of fixed assets"},
}


def _locale(db: Session) -> str:
    from app.services.locale_service import get_reporting_locale
    return "uk" if (get_reporting_locale(db) or "").strip().lower() == "uk" else "ir"


def calendar_for(locale: str) -> str:
    return "jalali" if locale == "ir" else "gregorian"


def categories(locale: str) -> tuple[Category, ...]:
    return CATEGORIES["uk" if locale == "uk" else "ir"]


def category(locale: str, key: str) -> Category | None:
    return next((c for c in categories(locale) if c.key == key), None)


# --- calendar months ---------------------------------------------------------------------------------

def month_start(d: date, cal: str) -> date:
    if cal == "jalali":
        j = jdatetime.date.fromgregorian(date=d)
        return jdatetime.date(j.year, j.month, 1).togregorian()
    return d.replace(day=1)


def next_month(start: date, cal: str) -> date:
    """First day of the month after the one ``start`` is in."""
    if cal == "jalali":
        j = jdatetime.date.fromgregorian(date=start)
        y, m = (j.year + 1, 1) if j.month == 12 else (j.year, j.month + 1)
        return jdatetime.date(y, m, 1).togregorian()
    return date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)


def month_end(start: date, cal: str) -> date:
    return next_month(start, cal) - timedelta(days=1)


def month_label(start: date, cal: str) -> str:
    if cal == "jalali":
        j = jdatetime.date.fromgregorian(date=start)
        return f"{j.year}/{j.month:02d}"
    return f"{start.year}-{start.month:02d}"


def months_between(a: date, b: date, cal: str) -> int:
    """Whole months from month-start ``a`` to month-start ``b``."""
    n, cur = 0, a
    while cur < b and n <= MAX_LIFE_MONTHS * 2:
        cur, n = next_month(cur, cal), n + 1
    return n


def default_start(in_service_on: date, locale: str) -> date:
    """Iran: the month after the asset came into use. Elsewhere: its own month."""
    cal = calendar_for(locale)
    start = month_start(in_service_on, cal)
    return next_month(start, cal) if locale == "ir" else start


# --- the schedule ------------------------------------------------------------------------------------------

@dataclass
class Month:
    period_start: date
    amount: int
    closing_nbv: int


def _spread(total: int, n: int) -> list[int]:
    """``total`` over ``n`` months, rounding carried: the parts sum exactly."""
    return [(total * (i + 1)) // n - (total * i) // n for i in range(n)] if n > 0 else []


def _first_posting_month(asset, cal: str) -> date:
    start = asset.depreciation_start
    if asset.opening_date is None:
        return start
    first, n = start, 0
    while month_end(first, cal) <= asset.opening_date and n <= MAX_LIFE_MONTHS:
        first, n = next_month(first, cal), n + 1
    return first


def schedule(asset, cal: str) -> list[Month]:
    """Every month the asset depreciates in, from the first one not covered by
    depreciation brought over, to the end of its life (or its disposal)."""
    cost, residual = int(asset.cost), int(asset.residual or 0)
    opening = int(asset.opening_accumulated or 0)
    first = _first_posting_month(asset, cal)
    stop = month_start(asset.disposed_on, cal) if asset.disposed_on else None
    out: list[Month] = []
    nbv = cost - opening
    if asset.method == STRAIGHT_LINE:
        life = int(asset.life_months or 0)
        remaining = life - months_between(asset.depreciation_start, first, cal)
        base = max(0, nbv - residual)
        cur = first
        for amount in _spread(base, max(0, remaining)):
            if stop is not None and cur >= stop:
                break
            nbv -= amount
            out.append(Month(cur, amount, nbv))
            cur = next_month(cur, cal)
        return out
    # declining balance: asset years of twelve months from the start
    rate = int(asset.rate_bps or 0)
    year_start = asset.depreciation_start
    cur = first
    for _year in range(MAX_LIFE_MONTHS // 12):
        year_end = year_start
        for _ in range(12):
            year_end = next_month(year_end, cal)            # first day after this asset year
        if cur >= year_end:
            year_start = year_end
            continue
        left = nbv - residual
        if left <= 0:
            break
        months = months_between(cur, year_end, cal)
        if nbv * 10_000 < cost * LOW_VALUE_SHARE_BPS:
            charge = left                                    # art. 9(b): the rest, in this year
        else:
            charge = min(left, (nbv * rate * months + 60_000) // 120_000)   # half up; part years pro rata
        if charge <= 0:
            break
        for amount in _spread(charge, months):
            if stop is not None and cur >= stop:
                return out
            nbv -= amount
            out.append(Month(cur, amount, nbv))
            cur = next_month(cur, cal)
        year_start = year_end
    return out


def monthly_charge(asset, cal: str, on: date) -> int:
    ms = month_start(on, cal)
    return next((m.amount for m in schedule(asset, cal) if m.period_start == ms), 0)


# --- create ----------------------------------------------------------------------------------------------------

def _account_exists(db: Session, code: str) -> bool:
    from app.models.account import Account
    return db.execute(select(Account.id).where(Account.code == code)).first() is not None


def _pick_account(db: Session, explicit: str | None, preferred: list[str | None], posting_category: str) -> str:
    """An account the user named must exist. Otherwise the first preferred
    code the chart has, else the posting category's own resolution — which
    falls back across charts and self-heals (a UK-locale company can carry the
    Iranian chart, and the other way round)."""
    from app.services.account_resolver import resolve_account_code
    if explicit:
        if not _account_exists(db, explicit):
            raise HTTPException(status_code=422, detail=f"Account not found: {explicit}")
        return explicit
    for code in preferred:
        if code and _account_exists(db, code):
            return code
    return resolve_account_code(db, posting_category)


def _gain_loss_account(db: Session, locale: str, kind: str) -> str:
    """The locale's gain/loss account, else the other chart's if this company
    has that one, else create the locale's."""
    from app.services.account_resolver import _ensure_account
    other = "ir" if locale == "uk" else "uk"
    for code in (DEFAULT_ACCOUNTS[locale][kind], DEFAULT_ACCOUNTS[other][kind]):
        if _account_exists(db, code):
            return code
    code = DEFAULT_ACCOUNTS[locale][kind]
    return _ensure_account(db, code, ACCOUNT_NAMES[locale][code], locale)


def next_number(db: Session) -> str:
    numbers = db.execute(select(FixedAsset.number)).scalars().all()
    seq = [int(n.split("-")[-1]) for n in numbers if n and n.split("-")[-1].isdigit()]
    return f"FA-{(max(seq) + 1 if seq else 1):04d}"


def validate_terms(method: str, life_months: int | None, rate_bps: int | None, cost: int, residual: int,
                   opening_accumulated: int = 0) -> None:
    if method not in METHODS:
        raise HTTPException(status_code=422, detail=f"Unknown method: {method}")
    if method == STRAIGHT_LINE and not (life_months and 1 <= life_months <= MAX_LIFE_MONTHS):
        raise HTTPException(status_code=422, detail="A straight-line asset needs a life of 1–1200 months.")
    if method == DECLINING_BALANCE and not (rate_bps and 1 <= rate_bps <= 10_000):
        raise HTTPException(status_code=422, detail="A declining-balance asset needs a yearly rate above 0 and up to 100%.")
    if cost <= 0 or residual < 0 or residual >= cost:
        raise HTTPException(status_code=422, detail="Cost must be positive and the residual value below it.")
    if opening_accumulated < 0 or opening_accumulated > cost - residual:
        raise HTTPException(status_code=422, detail="Depreciation brought over can't exceed cost less residual value.")


def create_asset(db: Session, data: dict[str, Any]) -> FixedAsset:
    """Add an asset card; ``acquisition`` = none (already in the books), bank
    (paid now) or payable (bought on credit) posts the purchase."""
    from app.services.fx_service import get_reporting_currency

    locale = _locale(db)
    cat = category(locale, data.get("category") or "other") or category(locale, "other")
    method = data.get("method") or cat.method
    life = data.get("life_months") if data.get("life_months") is not None else cat.life_months
    rate = data.get("rate_bps") if data.get("rate_bps") is not None else cat.rate_bps
    if method == STRAIGHT_LINE:
        rate = None
    else:
        life = None
    cost, residual = int(data["cost"]), int(data.get("residual") or 0)
    opening = int(data.get("opening_accumulated") or 0)
    validate_terms(method, life, rate, cost, residual, opening)
    in_service = data.get("in_service_on") or data["acquired_on"]
    if in_service < data["acquired_on"]:
        raise HTTPException(status_code=422, detail="An asset can't come into use before it was acquired.")
    if opening and not data.get("opening_date"):
        raise HTTPException(status_code=422, detail="Say up to which date the brought-over depreciation runs.")
    defaults = DEFAULT_ACCOUNTS[locale]
    accounts = {
        "asset": _pick_account(db, data.get("asset_account_code"), [cat.asset_account, defaults["asset"]],
                               "fixed_assets"),
        "accumulated": _pick_account(db, data.get("accumulated_account_code"),
                                     [cat.accumulated_account, defaults["accumulated"]], "accumulated_depreciation"),
        "expense": _pick_account(db, data.get("expense_account_code"), [defaults["expense"]], "depreciation_expense"),
    }
    start = data.get("depreciation_start")
    start = month_start(start, calendar_for(locale)) if start else default_start(in_service, locale)
    asset = FixedAsset(
        id=uuid.uuid4(), number=next_number(db), name=data["name"].strip(), category=cat.key,
        description=data.get("description"), serial_number=data.get("serial_number"), location=data.get("location"),
        entity_id=data.get("entity_id"),
        currency=(data.get("currency") or get_reporting_currency(db) or "IRR").strip().upper(),
        acquired_on=data["acquired_on"], in_service_on=in_service, depreciation_start=start,
        cost=cost, residual=residual, method=method, life_months=life, rate_bps=rate,
        opening_accumulated=opening, opening_date=data.get("opening_date") if opening else None,
        asset_account_code=accounts["asset"], accumulated_account_code=accounts["accumulated"],
        expense_account_code=accounts["expense"], status=ACTIVE,
    )
    db.add(asset)
    db.flush()
    how = data.get("acquisition") or "none"
    if how != "none":
        asset.acquisition_transaction_id = _post_acquisition(db, asset, how, data.get("bank_account_code")).id
    db.flush()
    return asset


def _post(db: Session, *, on: date, reference: str, description: str, currency: str, lines, entity_links=()):
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ledger_posting import create_transaction_from_payload
    payload = TransactionCreate(
        date=on, reference=reference[:128], description=description[:2000], currency=currency,
        lines=[TransactionLineCreate(account_code=c, debit=dr, credit=cr, line_description=(desc or None) and desc[:512])
               for c, dr, cr, desc in lines if dr or cr],
        entity_links=list(entity_links),
    )
    return create_transaction_from_payload(db, payload)


def _post_acquisition(db: Session, asset: FixedAsset, how: str, bank_code: str | None):
    from app.schemas.entity import EntityLink
    from app.services.account_resolver import resolve_account_code
    if how == "bank":
        credit = bank_code or resolve_account_code(db, "bank")
        links = []
    elif how == "payable":
        credit = resolve_account_code(db, "ap")
        links = [EntityLink(role="supplier", entity_id=asset.entity_id)] if asset.entity_id else []
    else:
        raise HTTPException(status_code=422, detail=f"Unknown acquisition: {how}")
    if not _account_exists(db, credit):
        raise HTTPException(status_code=422, detail=f"Account not found: {credit}")
    return _post(db, on=asset.acquired_on, reference=f"{asset.number}-ACQ", description=bt(db, "fa_acquisition", name=asset.name),
                 currency=asset.currency, entity_links=links,
                 lines=[(asset.asset_account_code, asset.cost, 0, f"{asset.number} {asset.name}"),
                        (credit, 0, asset.cost, f"{asset.number} {asset.name}")])


def has_postings(db: Session, asset: FixedAsset) -> bool:
    return db.execute(select(FixedAssetDepreciation.id).where(FixedAssetDepreciation.asset_id == asset.id)).first() \
        is not None


# --- runs --------------------------------------------------------------------------------------------------------

def _posted(db: Session, asset_ids) -> dict:
    out: dict = defaultdict(dict)
    if not asset_ids:
        return out
    for aid, ms, amount in db.execute(select(FixedAssetDepreciation.asset_id, FixedAssetDepreciation.period_start,
                                             FixedAssetDepreciation.amount)
                                      .where(FixedAssetDepreciation.asset_id.in_(list(asset_ids)))):
        out[aid][ms] = int(amount)
    return out


def due(db: Session, through: date, *, assets=None) -> list[tuple[FixedAsset, Month]]:
    """(asset, month) pairs whose month has ended by ``through`` and that
    aren't posted yet, oldest first."""
    cal = calendar_for(_locale(db))
    if assets is None:
        assets = db.execute(select(FixedAsset).where(FixedAsset.status == ACTIVE)).scalars().all()
    posted = _posted(db, [a.id for a in assets])
    out = []
    for a in assets:
        for m in schedule(a, cal):
            if month_end(m.period_start, cal) > through:
                break
            if m.period_start not in posted.get(a.id, {}) and m.amount > 0:
                out.append((a, m))
    out.sort(key=lambda am: (am[1].period_start, am[0].number))
    return out


def run(db: Session, through: date | None = None, *, preview: bool = False, assets=None) -> dict[str, Any]:
    """Post every month that has ended and isn't posted: one journal per month
    and currency, dated the month's last day — or the first open day when the
    month is in a closed period."""
    from app.services.period_service import get_closed_period

    today = date.today()
    through = min(through or today, today)
    cal = calendar_for(_locale(db))
    closed = get_closed_period(db)
    pairs = due(db, through, assets=assets)
    groups: dict = defaultdict(list)
    for a, m in pairs:
        on = month_end(m.period_start, cal)
        caught_up = closed is not None and on <= closed
        if caught_up:
            on = closed + timedelta(days=1)
            if on > today:
                raise HTTPException(status_code=422, detail="The books are closed through today; nothing can be posted.")
        groups[(on, a.currency, m.period_start if not caught_up else None)].append((a, m))
    journals = []
    for (on, currency, period), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
        label = month_label(period, cal) if period else f"catch-up {on.isoformat()}"
        total = sum(m.amount for _a, m in rows)
        entry = {"date": on.isoformat(), "currency": currency, "label": label, "amount": total,
                 "assets": len({a.id for a, _m in rows}), "months": sorted({month_label(m.period_start, cal) for _a, m in rows}),
                 "transaction_id": None}
        if not preview:
            lines = []
            for a, m in rows:
                desc = f"{a.number} {a.name} — {month_label(m.period_start, cal)}"
                lines += [(a.expense_account_code, m.amount, 0, desc), (a.accumulated_account_code, 0, m.amount, desc)]
            txn = _post(db, on=on, reference=f"DEP-{label}"[:128], description=bt(db, "fa_depreciation", label=label),
                        currency=currency, lines=lines)
            for a, m in rows:
                db.add(FixedAssetDepreciation(asset_id=a.id, period_start=m.period_start, amount=m.amount,
                                              transaction_id=txn.id))
            db.flush()
            entry["transaction_id"] = str(txn.id)
        journals.append(entry)
    totals: dict = defaultdict(int)
    for j in journals:
        totals[j["currency"]] += j["amount"]
    if journals and not preview:
        from app.api.reports import invalidate_dashboard_cache
        invalidate_dashboard_cache()
    return {"through": through.isoformat(), "preview": preview, "journals": journals, "totals": dict(totals),
            "months": len(pairs)}


# --- disposal -----------------------------------------------------------------------------------------------------

def accumulated(db: Session, asset: FixedAsset, *, as_of: date | None = None) -> int:
    q = select(func.coalesce(func.sum(FixedAssetDepreciation.amount), 0)).where(
        FixedAssetDepreciation.asset_id == asset.id)
    if as_of is not None:
        q = q.where(FixedAssetDepreciation.period_start <= as_of)
    return int(asset.opening_accumulated or 0) + int(db.execute(q).scalar() or 0)


def dispose(db: Session, asset: FixedAsset, *, on: date, proceeds: int = 0, bank_account_code: str | None = None,
            preview: bool = False) -> dict[str, Any]:
    """Sell or scrap an asset: post its months before the disposal month, then
    Dr bank (proceeds) + Dr accumulated / Cr cost, the difference to gain or loss."""
    from app.services.account_resolver import resolve_account_code

    if asset.status != ACTIVE:
        raise HTTPException(status_code=409, detail="This asset has already been disposed of.")
    if on > date.today():
        raise HTTPException(status_code=422, detail="A disposal can't be dated in the future.")
    if on < asset.acquired_on:
        raise HTTPException(status_code=422, detail="A disposal can't be before the asset was acquired.")
    if proceeds < 0:
        raise HTTPException(status_code=422, detail="Proceeds can't be negative.")
    bank = bank_account_code or (resolve_account_code(db, "bank") if proceeds else None)
    if bank and not _account_exists(db, bank):
        raise HTTPException(status_code=422, detail=f"Account not found: {bank}")
    locale = _locale(db)
    cal = calendar_for(locale)
    before = month_start(on, cal) - timedelta(days=1)
    catch_up = run(db, before, preview=preview, assets=[asset]) if before >= asset.depreciation_start else None
    acc = accumulated(db, asset)
    if preview and catch_up:
        acc += sum(j["amount"] for j in catch_up["journals"])
    nbv = int(asset.cost) - acc
    result = proceeds - nbv
    out = {"asset_id": str(asset.id), "on": on.isoformat(), "cost": int(asset.cost), "accumulated": acc,
           "net_book_value": nbv, "proceeds": proceeds, "gain": max(0, result), "loss": max(0, -result),
           "depreciation_posted_first": catch_up["journals"] if catch_up else [], "transaction_id": None}
    if preview:
        return out
    desc = f"{asset.number} {asset.name}"
    lines = [(bank, proceeds, 0, desc)] if proceeds else []
    lines += [(asset.accumulated_account_code, acc, 0, desc), (asset.asset_account_code, 0, int(asset.cost), desc)]
    if result > 0:
        lines.append((_gain_loss_account(db, locale, "gain"), 0, result, bt(db, "fa_gain", text=desc)))
    elif result < 0:
        lines.append((_gain_loss_account(db, locale, "loss"), -result, 0, bt(db, "fa_loss", text=desc)))
    txn = _post(db, on=on, reference=f"{asset.number}-DISPOSAL", description=bt(db, "fa_disposal", name=asset.name),
                currency=asset.currency, lines=lines)
    asset.status, asset.disposed_on, asset.disposal_proceeds = DISPOSED, on, proceeds
    asset.disposal_transaction_id = txn.id
    db.flush()
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    out["transaction_id"] = str(txn.id)
    return out


# --- the register ------------------------------------------------------------------------------------------------

def asset_row(db: Session, a: FixedAsset, cal: str, *, as_of: date, posted: dict | None = None,
              entity_names: dict | None = None) -> dict[str, Any]:
    months = posted if posted is not None else _posted(db, [a.id]).get(a.id, {})
    acc = int(a.opening_accumulated or 0) + sum(v for ms, v in months.items() if ms <= as_of)
    sched = schedule(a, cal)
    remaining = [m for m in sched if m.period_start not in months]
    this_month = month_start(as_of, cal)
    return {
        "id": str(a.id), "number": a.number, "name": a.name, "category": a.category, "status": a.status,
        "currency": a.currency, "acquired_on": a.acquired_on.isoformat(), "in_service_on": a.in_service_on.isoformat(),
        "depreciation_start": a.depreciation_start.isoformat(), "method": a.method, "life_months": a.life_months,
        "rate_bps": a.rate_bps, "cost": int(a.cost), "residual": int(a.residual or 0),
        "opening_accumulated": int(a.opening_accumulated or 0),
        "opening_date": a.opening_date.isoformat() if a.opening_date else None,
        "accumulated": acc, "net_book_value": int(a.cost) - acc if a.status == ACTIVE else 0,
        "monthly_charge": next((m.amount for m in sched if m.period_start == this_month), 0) if a.status == ACTIVE else 0,
        "fully_depreciated": a.status == ACTIVE and not remaining,
        "last_depreciation_month": month_label(sched[-1].period_start, cal) if sched else None,
        "serial_number": a.serial_number, "location": a.location, "description": a.description,
        "entity_id": str(a.entity_id) if a.entity_id else None,
        "entity_name": (entity_names or {}).get(a.entity_id),
        "asset_account_code": a.asset_account_code, "accumulated_account_code": a.accumulated_account_code,
        "expense_account_code": a.expense_account_code,
        "disposed_on": a.disposed_on.isoformat() if a.disposed_on else None,
        "disposal_proceeds": a.disposal_proceeds,
        "acquisition_transaction_id": str(a.acquisition_transaction_id) if a.acquisition_transaction_id else None,
        "disposal_transaction_id": str(a.disposal_transaction_id) if a.disposal_transaction_id else None,
    }


def register(db: Session, *, as_of: date | None = None, include_disposed: bool = True) -> dict[str, Any]:
    from app.models.entity import Entity

    as_of = as_of or date.today()
    locale = _locale(db)
    cal = calendar_for(locale)
    q = select(FixedAsset).order_by(FixedAsset.number)
    if not include_disposed:
        q = q.where(FixedAsset.status == ACTIVE)
    assets = db.execute(q).scalars().all()
    posted = _posted(db, [a.id for a in assets])
    eids = {a.entity_id for a in assets if a.entity_id}
    names = dict(db.execute(select(Entity.id, Entity.name).where(Entity.id.in_(eids))).all()) if eids else {}
    rows = [asset_row(db, a, cal, as_of=as_of, posted=posted.get(a.id, {}), entity_names=names) for a in assets]
    totals: dict = defaultdict(lambda: {"cost": 0, "accumulated": 0, "net_book_value": 0, "count": 0})
    by_category: dict = defaultdict(lambda: {"cost": 0, "accumulated": 0, "net_book_value": 0, "count": 0})
    for r in rows:
        if r["status"] != ACTIVE:
            continue
        for bucket in (totals[r["currency"]], by_category[(r["currency"], r["category"])]):
            bucket["cost"] += r["cost"]
            bucket["accumulated"] += r["accumulated"]
            bucket["net_book_value"] += r["net_book_value"]
            bucket["count"] += 1
    pending = due(db, min(as_of, date.today()), assets=[a for a in assets if a.status == ACTIVE])
    pending_total: dict = defaultdict(int)
    for a, m in pending:
        pending_total[a.currency] += m.amount
    return {
        "as_of": as_of.isoformat(), "calendar": cal, "locale": locale, "assets": rows,
        "totals": dict(totals),
        "by_category": [{"currency": c, "category": k, **v} for (c, k), v in sorted(by_category.items())],
        "due": {"months": len(pending), "amount": dict(pending_total),
                "oldest": month_label(pending[0][1].period_start, cal) if pending else None},
    }


def schedule_rows(db: Session, a: FixedAsset) -> list[dict[str, Any]]:
    cal = calendar_for(_locale(db))
    posted = _posted(db, [a.id]).get(a.id, {})
    return [{"period_start": m.period_start.isoformat(), "label": month_label(m.period_start, cal),
             "period_end": month_end(m.period_start, cal).isoformat(), "amount": m.amount,
             "closing_nbv": m.closing_nbv, "posted": m.period_start in posted} for m in schedule(a, cal)]

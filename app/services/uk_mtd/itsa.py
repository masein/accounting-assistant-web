"""MTD for Income Tax: quarterly figures in HMRC's categories from the ledger.

For the company's income source (self-employment or UK property) and a
quarter of a tax year, every income and expense account is summed over the
quarter and over the tax year to date (updates are cumulative from April),
mapped to an HMRC category (``categories.default_category`` or the owner's
override) and returned with the request body HMRC's cumulative update takes:

* self-employment — ``periodDates`` / ``periodIncome`` / ``periodExpenses`` /
  ``periodDisallowableExpenses`` (Self Employment Business API);
* UK property — ``fromDate`` / ``toDate`` / ``ukProperty.income`` /
  ``ukProperty.expenses`` (Property Business API).

Business entertaining and depreciation are reported and also marked
disallowable in full. ``consolidatedExpenses`` (one total) is offered when the
year-to-date turnover is below the VAT registration threshold. Only journals
in the company's base currency count; undone journals never do.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
from app.services.uk_mtd import categories as C
from app.services.uk_mtd.periods import ItsaQuarter, tax_year_bounds, tax_year_label

# Consolidated expenses are allowed below the VAT registration threshold.
VAT_THRESHOLD = 90_000
# MTD ITSA applies from the tax year starting in April of the key year when
# qualifying income (self-employment turnover + property income) in the tax
# year two years earlier was above the threshold.
MANDATION_THRESHOLDS = {2026: 50_000, 2027: 30_000, 2028: 20_000}


def _account_totals(db: Session, start: date, end: date, currency: str) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        select(Account.code, Account.name, func.coalesce(func.sum(TransactionLine.debit), 0),
               func.coalesce(func.sum(TransactionLine.credit), 0))
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .where(Transaction.deleted_at.is_(None), Transaction.currency == currency,
               Transaction.date >= start, Transaction.date <= end)
        .group_by(Account.code, Account.name)
    ).all()
    return {code: {"name": name, "debit": int(d or 0), "credit": int(c or 0)} for code, name, d, c in rows}


def category_for(code: str, source: str, overrides: dict[str, str]) -> str | None:
    return overrides.get(code) or C.default_category(code, source)


def _figures(totals: dict[str, dict[str, Any]], source: str, overrides: dict[str, str]):
    """(income, expenses, accounts, excluded, unmapped) for one range."""
    income: dict[str, int] = defaultdict(int)
    expenses: dict[str, int] = defaultdict(int)
    accounts, excluded, unmapped = [], [], []
    for code in sorted(totals):
        t = totals[code]
        if classify_account_code(code) not in (REVENUE, EXPENSE):
            continue  # balance-sheet accounts never enter the update
        cat = category_for(code, source, overrides)
        kind = C.kind_of(cat, source) if cat else None
        if cat == C.EXCLUDED:
            excluded.append({"code": code, "name": t["name"], "amount": t["credit"] - t["debit"]
                             if classify_account_code(code) == REVENUE else t["debit"] - t["credit"]})
            continue
        if kind is None:
            unmapped.append({"code": code, "name": t["name"], "category": cat})
            continue
        amount = (t["credit"] - t["debit"]) if kind == "income" else (t["debit"] - t["credit"])
        (income if kind == "income" else expenses)[cat] += amount
        accounts.append({"code": code, "name": t["name"], "category": cat, "kind": kind, "amount": amount,
                         "overridden": code in overrides})
    return dict(income), dict(expenses), accounts, excluded, unmapped


def _money(v: int) -> float:
    return float(round(v, 2))


def _nonzero(d: dict[str, int]) -> dict[str, float]:
    return {k: _money(v) for k, v in d.items() if v}


def _se_body(start: date, end: date, income: dict, expenses: dict, consolidated: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "periodDates": {"periodStartDate": start.isoformat(), "periodEndDate": end.isoformat()},
        "periodIncome": _nonzero({k: income.get(k, 0) for k in C.SELF_EMPLOYMENT_INCOME}) or {"turnover": 0.0},
    }
    if consolidated:
        body["periodExpenses"] = {"consolidatedExpenses": _money(sum(expenses.values()))}
    else:
        exp = _nonzero({k: expenses.get(k, 0) for k in C.SELF_EMPLOYMENT_EXPENSES})
        if exp:
            body["periodExpenses"] = exp
        dis = _nonzero({f"{k}Disallowable": expenses.get(k, 0) for k in C.SELF_EMPLOYMENT_DISALLOWED_IN_FULL})
        if dis:
            body["periodDisallowableExpenses"] = dis
    return body


def _property_body(start: date, end: date, income: dict, expenses: dict, consolidated: bool) -> dict[str, Any]:
    inc = _nonzero({k: income.get(k, 0) for k in C.PROPERTY_INCOME if k != "rentARoomRents"})
    if income.get("rentARoomRents"):
        inc["rentARoom"] = {"rentsReceived": _money(income["rentARoomRents"])}
    if consolidated:
        # residential finance costs and rent-a-room relief stay itemised
        rest = {k: v for k, v in expenses.items() if k not in ("residentialFinancialCost", "rentARoomClaimed")}
        exp: dict[str, Any] = {"consolidatedExpenses": _money(sum(rest.values()))}
        if expenses.get("residentialFinancialCost"):
            exp["residentialFinancialCost"] = _money(expenses["residentialFinancialCost"])
    else:
        exp = _nonzero({k: expenses.get(k, 0) for k in C.PROPERTY_EXPENSES if k != "rentARoomClaimed"})
    if expenses.get("rentARoomClaimed"):
        exp["rentARoom"] = {"amountClaimed": _money(expenses["rentARoomClaimed"])}
    body: dict[str, Any] = {"fromDate": start.isoformat(), "toDate": end.isoformat(),
                            "ukProperty": {"income": inc or {"periodAmount": 0.0}}}
    if exp:
        body["ukProperty"]["expenses"] = exp
    return body


def quarterly_update(db: Session, q: ItsaQuarter, *, source: str, overrides: dict[str, str] | None = None,
                     currency: str = "GBP") -> dict[str, Any]:
    if source not in C.CATALOGUE:
        raise ValueError("Choose the income source (self-employment or UK property) in the MTD settings first")
    overrides = overrides or {}
    q_in, q_ex, q_acc, q_excl, q_unm = _figures(_account_totals(db, q.start, q.end, currency), source, overrides)
    y_in, y_ex, y_acc, y_excl, y_unm = _figures(_account_totals(db, q.cumulative_start, q.end, currency),
                                                source, overrides)
    cat = C.CATALOGUE[source]
    # HMRC compares the cumulative turnover of the update with the threshold.
    consolidated_allowed = sum(y_in.values()) < VAT_THRESHOLD
    build = _se_body if source == "self_employment" else _property_body
    by_account: dict[str, dict[str, Any]] = {}
    for row in y_acc:
        by_account[row["code"]] = {**row, "quarter": 0, "year_to_date": row["amount"]}
    for row in q_acc:
        by_account.setdefault(row["code"], {**row, "year_to_date": 0})["quarter"] = row["amount"]
    for row in by_account.values():
        row.pop("amount", None)

    def lines(kind: str, qd: dict, yd: dict) -> list[dict[str, Any]]:
        return [{"category": k, "quarter": qd.get(k, 0), "year_to_date": yd.get(k, 0)} for k in cat[kind]]

    return {
        "source": source, "currency": currency, "quarter": q.as_dict(),
        "income": lines("income", q_in, y_in), "expenses": lines("expenses", q_ex, y_ex),
        "totals": {
            "quarter": {"income": sum(q_in.values()), "expenses": sum(q_ex.values()),
                        "profit": sum(q_in.values()) - sum(q_ex.values())},
            "year_to_date": {"income": sum(y_in.values()), "expenses": sum(y_ex.values()),
                             "profit": sum(y_in.values()) - sum(y_ex.values())},
        },
        "disallowable_in_full": list(C.SELF_EMPLOYMENT_DISALLOWED_IN_FULL) if source == "self_employment" else [],
        "accounts": sorted(by_account.values(), key=lambda r: r["code"]),
        "excluded": y_excl, "unmapped": y_unm,
        "consolidated_allowed": consolidated_allowed,
        "hmrc_body": build(q.cumulative_start, q.end, y_in, y_ex, False),
        "hmrc_body_consolidated": build(q.cumulative_start, q.end, y_in, y_ex, True) if consolidated_allowed else None,
        "notes": [
            "Figures are cumulative from the start of the tax year, as HMRC's quarterly update expects.",
            "Business entertaining and depreciation are reported and marked disallowable in full."
            if source == "self_employment" else
            "Depreciation is never allowable for property income and is left out.",
            "Check the mapping of each account below; change any of them in the MTD settings.",
        ],
    }


def qualifying_income(db: Session, tax_year: int, *, source: str, overrides: dict[str, str] | None = None,
                      currency: str = "GBP") -> int:
    """Gross income of the business for a tax year — what the MTD threshold tests."""
    start, end = tax_year_bounds(tax_year)
    income, _ex, _a, _x, _u = _figures(_account_totals(db, start, end, currency), source, overrides or {})
    return sum(income.values())


def mandation(db: Session, tax_year: int, *, source: str, overrides: dict[str, str] | None = None,
              currency: str = "GBP") -> dict[str, Any]:
    """Whether MTD for Income Tax applies in ``tax_year``: qualifying income in
    the tax year two years earlier above that year's threshold."""
    threshold = MANDATION_THRESHOLDS.get(tax_year) or (MANDATION_THRESHOLDS[2028] if tax_year > 2028 else None)
    base_year = tax_year - 2
    income = qualifying_income(db, base_year, source=source, overrides=overrides, currency=currency)
    return {
        "tax_year": tax_year_label(tax_year), "threshold": threshold, "based_on": tax_year_label(base_year),
        "qualifying_income": income,
        "required": bool(threshold is not None and income > threshold),
        "note": "Qualifying income is the gross income of all self-employment and UK property businesses of the "
                "individual; these books hold one of them.",
    }

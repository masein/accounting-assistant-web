"""Deterministic VAT / sales-tax summary for a period.

Output tax = tax charged on sales invoices issued in the period; input tax =
tax incurred on purchase bills; net tax = output − input (what's payable to /
recoverable from the tax authority). Computed straight from invoice line
items so it matches what was posted. This is an ESTIMATE — the caveat below is
always attached and must always be surfaced to the user.
"""
from __future__ import annotations

from decimal import Decimal

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.invoice import Invoice
from app.core.money import exponent, issued_half_even, plain, quantize, tax_on, to_decimal

# Returned with every summary and surfaced to the user verbatim, even when the
# user asks to "just give me the number" (§17.6).
TAX_CAVEAT = (
    "This is an estimate based on the VAT/sales-tax rates recorded on your "
    "invoices — not a substitute for advice from a licensed tax professional. "
    "Filing thresholds, deadlines and rules vary by jurisdiction and change "
    "over time; verify current requirements with your tax authority or advisor."
)


def _line_tax(line_total, tax_rate, taxable: bool, currency: str | None = None, half_even: bool = False):
    """A line's VAT to its currency's places, half-up (the penny for pounds);
    without a currency, and in rials, whole units (app.core.money.tax_on).
    ``half_even``: a rial invoice issued before issue #2 (``half_even_vat``)."""
    if not taxable:
        return 0
    rate = float(tax_rate or 0)
    if rate <= 0:
        return 0
    return tax_on(line_total, rate, currency, half_even=half_even)


def half_even_vat(inv) -> bool:
    """Whether ``inv`` (an invoice or quote) was issued with its rial VAT
    rounded half to even, before issue #2, and so keeps that VAT
    (app.core.money.issued_half_even)."""
    return issued_half_even(
        inv.amount, [(it.line_total, it.tax_rate, it.taxable) for it in (inv.items or [])], inv.currency)


def _invoice_tax(inv: Invoice) -> tuple[int, set[float]]:
    """(tax_total, set of rates used) for one invoice's taxable lines."""
    total = 0
    rates: set[float] = set()
    old = half_even_vat(inv)
    for it in inv.items or []:
        t = _line_tax(it.line_total, it.tax_rate, it.taxable, inv.currency, old)
        if t and it.taxable and float(it.tax_rate or 0) > 0:
            rates.add(float(it.tax_rate))
        total += t
    return total, rates


def _notional_tax(line_total, tax_rate, currency: str | None = None, half_even: bool = False):
    rate = float(tax_rate or 0)
    if rate <= 0:
        return 0
    return tax_on(line_total, rate, currency, half_even=half_even)


def default_period_start(db: Session, today: date) -> date:
    """Start of the current VAT period, the default for a tax summary: the
    Jalali season (Farvardin/Tir/Mehr/Dey 1) for an Iranian company — the
    period its VAT return covers (app/services/tax_ir.py) — and the calendar
    quarter otherwise."""
    from app.services.locale_service import get_reporting_locale
    if get_reporting_locale(db) == "ir":
        from app.services.tax_ir import season_of
        return season_of(today).start
    return date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)


def compute_tax_summary(
    db: Session,
    from_date: date,
    to_date: date,
    currency: str | None = None,
) -> dict[str, Any]:
    """Output/input/net tax for invoices issued in [from_date, to_date].

    Counts recognised invoices (excludes draft/canceled). Returns the figures,
    the distinct rates seen (the assumptions used), and the mandatory caveat.
    """
    q = (
        select(Invoice)
        .where(
            Invoice.issue_date >= from_date,
            Invoice.issue_date <= to_date,
            Invoice.status.in_(["issued", "partially_paid", "paid", "credited"]),
        )
        .options(selectinload(Invoice.items))
    )
    if currency:
        q = q.where(Invoice.currency == currency.strip().upper())
    invoices = db.execute(q).scalars().all()

    output_tax = 0
    input_tax = 0
    sales_count = 0
    purchase_count = 0
    rates: set[float] = set()
    # Breakdowns (§7.3, §7.6): by tax treatment and by the rate actually applied.
    by_treatment: dict[str, dict[str, int]] = {}
    by_rate: dict[str, dict[str, int]] = {}
    reverse_charge_notional = 0
    jurisdictions: set[str] = set()

    def _bucket(d: dict[str, dict[str, int]], key: str, side: str, amount: int) -> None:
        slot = d.setdefault(key, {"output": 0, "input": 0, "base": 0})
        slot[side] += amount

    for inv in invoices:
        tax, inv_rates = _invoice_tax(inv)
        old = half_even_vat(inv)
        rates |= inv_rates
        side = "output" if inv.kind == "sales" else "input"
        if inv.kind == "sales":
            output_tax += tax
            sales_count += 1
        elif inv.kind == "purchase":
            input_tax += tax
            purchase_count += 1

        for it in inv.items or []:
            treatment = (getattr(it, "tax_treatment", "standard") or "standard")
            code = getattr(it, "tax_code", None)
            if code and "_" in code:
                jurisdictions.add(code.split("_", 1)[0])
            base = plain(it.line_total or 0)
            _bucket(by_treatment, treatment, "base", base)
            if treatment == "standard" and it.taxable:
                t = _line_tax(it.line_total, it.tax_rate, it.taxable, inv.currency, old)
                _bucket(by_treatment, treatment, side, t)
                _bucket(by_rate, f"{float(it.tax_rate or 0):g}", side, t)
            elif treatment == "reverse_charge":
                # Cross-border B2B: customer self-accounts. Notional output AND
                # input that net to zero — recorded/labelled, never added to net.
                notional = _notional_tax(it.line_total, it.tax_rate, inv.currency, old)
                _bucket(by_treatment, treatment, "output", notional)
                _bucket(by_treatment, treatment, "input", notional)
                reverse_charge_notional += notional

    rates_sorted = sorted(rates)
    if rates_sorted:
        assumptions = (
            "Rates applied as recorded per invoice line: "
            + ", ".join(f"{r:g}%" for r in rates_sorted)
            + ". Only lines marked taxable are included."
        )
    else:
        assumptions = "No tax rates were recorded on invoices in this period."

    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "currency": (currency or "").upper() or None,
        "output_tax": output_tax,
        "input_tax": input_tax,
        "net_tax": output_tax - input_tax,
        "sales_invoice_count": sales_count,
        "purchase_invoice_count": purchase_count,
        "rates": rates_sorted,
        "by_treatment": by_treatment,
        "by_rate": by_rate,
        "reverse_charge_notional": reverse_charge_notional,
        "jurisdictions": sorted(jurisdictions),
        "assumptions": assumptions,
        "caveat": TAX_CAVEAT,
    }

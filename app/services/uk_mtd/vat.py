"""VAT return boxes 1–9 for an MTD VAT period, from the period's invoices.

* Box 1 output VAT on sales (reverse-charge purchases add their notional VAT
  here too), less the VAT inside the period's sales credit notes.
* Box 4 input VAT on purchases (and the reverse-charge notional VAT).
* Box 6 / 7 every sale / purchase ex VAT — standard, zero-rated, exempt and
  reverse-charge alike — net of credit notes, in whole pounds.
* Boxes 2, 8 and 9 (Northern Ireland goods movements with the EU) are 0: the
  books do not record them.

Each line's VAT is computed exactly as the invoice posted. Only invoices in
the company's base currency count; others are listed. The HMRC body uses the
MTD VAT API field names; ``periodKey`` comes from HMRC's obligations and is
left for the submitting software to fill.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.credit_note import CreditNote
from app.models.invoice import Invoice
from app.services.tax_ir import _share
from app.services.tax_service import _line_tax, _notional_tax
from app.services.uk_mtd.periods import VatPeriod

RECOGNISED = ("issued", "partially_paid", "paid")


def _split(inv: Invoice) -> dict[str, int]:
    """base (every line), vat (standard lines), reverse (notional VAT)."""
    items = list(inv.items or [])
    if not items:
        return {"base": int(inv.amount or 0), "vat": 0, "reverse": 0}
    base = vat = reverse = 0
    for it in items:
        total = int(it.line_total or 0)
        base += total
        treatment = getattr(it, "tax_treatment", "standard") or "standard"
        if treatment == "reverse_charge":
            reverse += _notional_tax(total, it.tax_rate)
        elif treatment == "standard":
            vat += _line_tax(total, it.tax_rate, it.taxable)
    return {"base": base, "vat": vat, "reverse": reverse}


def vat_return(db: Session, period: VatPeriod, *, currency: str) -> dict[str, Any]:
    currency = (currency or "GBP").upper()
    invoices = db.execute(
        select(Invoice).where(Invoice.issue_date >= period.start, Invoice.issue_date <= period.end,
                              Invoice.status.in_(RECOGNISED), Invoice.kind.in_(("sales", "purchase")))
        .options(selectinload(Invoice.items))
    ).scalars().all()
    box1 = box4 = box6 = box7 = 0
    counts = {"sales": 0, "purchases": 0, "credit_notes": 0}
    other_currency: list[dict] = []
    for inv in invoices:
        if (inv.currency or currency).upper() != currency:
            other_currency.append({"number": inv.number, "currency": inv.currency, "amount": int(inv.amount or 0)})
            continue
        s = _split(inv)
        if inv.kind == "sales":
            counts["sales"] += 1
            box1 += s["vat"]
            box6 += s["base"]
        else:
            counts["purchases"] += 1
            box4 += s["vat"] + s["reverse"]
            box1 += s["reverse"]          # the customer accounts for the supplier's VAT
            box7 += s["base"]
    notes = db.execute(select(CreditNote).where(CreditNote.date >= period.start, CreditNote.date <= period.end,
                                                CreditNote.note_type == "reduction")).scalars().all()
    for cn in notes:
        if (cn.currency or currency).upper() != currency:
            continue
        inv = db.get(Invoice, cn.invoice_id) if cn.invoice_id else None
        vat = 0
        if inv is not None:
            s = _split(inv)
            vat = _share(s["vat"], s["base"] + s["vat"], int(cn.amount or 0))
        side = inv.kind if inv is not None else ("purchase" if cn.kind == "purchase" else "sales")
        counts["credit_notes"] += 1
        if side == "sales":
            box1 -= vat
            box6 -= int(cn.amount or 0) - vat
        else:
            box4 -= vat
            box7 -= int(cn.amount or 0) - vat
    box2 = box8 = box9 = 0
    box3 = box1 + box2
    box5 = abs(box3 - box4)
    body = {
        "periodKey": "",
        "vatDueSales": float(box1), "vatDueAcquisitions": float(box2), "totalVatDue": float(box3),
        "vatReclaimedCurrPeriod": float(box4), "netVatDue": float(box5),
        "totalValueSalesExVAT": int(box6), "totalValuePurchasesExVAT": int(box7),
        "totalValueGoodsSuppliedExVAT": int(box8), "totalAcquisitionsExVAT": int(box9),
        "finalised": False,
    }
    return {
        "period": period.as_dict(), "currency": currency,
        "boxes": {"1": box1, "2": box2, "3": box3, "4": box4, "5": box5, "6": box6, "7": box7, "8": box8, "9": box9},
        "direction": "payable" if box3 >= box4 else "repayable",
        "hmrc_body": body, "counts": counts, "other_currency": other_currency,
        "notes": [
            "Boxes 2, 8 and 9 (Northern Ireland goods movements with the EU) are not recorded and are 0.",
            "periodKey comes from HMRC's VAT obligations; your submitting software fills it in.",
            "Figures from your recorded invoices; check them before submitting.",
        ],
    }

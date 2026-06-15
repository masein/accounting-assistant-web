"""3-way match — compare a purchase order, what was received, and a vendor bill.

Pure logic, no DB/HTTP, so it's easy to test. For each PO line we look at the
ordered qty, the received qty, and the invoiced qty/price, and flag:

  - over_quantity : invoiced qty  > ordered qty
  - short_receipt : invoiced qty  > received qty   (billed for goods not yet in)
  - over_price    : invoice unit price > PO unit price · (1 + tolerance)
  - no_po_line    : an invoice line that matches no PO line

A bill is "matched" only when there are zero discrepancies; otherwise the
caller must not auto-approve it (§4.3) and surfaces the discrepancy list.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Default price tolerance: invoice may exceed the PO unit price by up to 1%
# (rounding / minor fee) before it counts as an over-price discrepancy.
DEFAULT_PRICE_TOLERANCE = 0.01


@dataclass
class MatchLine:
    key: str
    description: str
    ordered_qty: float
    received_qty: float
    invoiced_qty: float
    po_unit_price: int
    invoice_unit_price: int
    discrepancies: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    matched: bool
    lines: list[MatchLine]
    discrepancies: list[dict]

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "discrepancies": self.discrepancies,
            "lines": [
                {
                    "key": ln.key,
                    "description": ln.description,
                    "ordered_qty": ln.ordered_qty,
                    "received_qty": ln.received_qty,
                    "invoiced_qty": ln.invoiced_qty,
                    "po_unit_price": ln.po_unit_price,
                    "invoice_unit_price": ln.invoice_unit_price,
                    "discrepancies": ln.discrepancies,
                }
                for ln in self.lines
            ],
        }


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def match(
    po_lines: list[dict],
    invoice_lines: list[dict],
    *,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
) -> MatchResult:
    """Run the 3-way comparison.

    ``po_lines``: dicts with ``key``, ``description``, ``ordered_qty``,
    ``received_qty``, ``unit_price``. ``invoice_lines``: dicts with ``key``,
    ``description``, ``quantity``, ``unit_price``. ``key`` is whatever the
    caller uses to pair a PO line with an invoice line (e.g. an inventory item
    id); when absent we fall back to the normalized description.
    """
    # Index PO lines by key, then by normalized description, for pairing.
    by_key: dict[str, dict] = {}
    by_desc: dict[str, dict] = {}
    for p in po_lines:
        if p.get("key"):
            by_key[str(p["key"])] = p
        by_desc.setdefault(_norm(p.get("description")), p)

    result_lines: list[MatchLine] = []
    discrepancies: list[dict] = []

    for inv in invoice_lines:
        inv_qty = float(inv.get("quantity") or 0)
        inv_price = int(inv.get("unit_price") or 0)
        desc = inv.get("description") or ""

        po = None
        if inv.get("key") and str(inv["key"]) in by_key:
            po = by_key[str(inv["key"])]
        elif _norm(desc) in by_desc:
            po = by_desc[_norm(desc)]

        if po is None:
            ml = MatchLine(
                key=str(inv.get("key") or ""), description=desc,
                ordered_qty=0, received_qty=0, invoiced_qty=inv_qty,
                po_unit_price=0, invoice_unit_price=inv_price,
                discrepancies=["no_po_line"],
            )
            result_lines.append(ml)
            discrepancies.append({
                "type": "no_po_line", "description": desc,
                "invoiced_qty": inv_qty, "invoice_unit_price": inv_price,
            })
            continue

        ordered = float(po.get("ordered_qty") or 0)
        received = float(po.get("received_qty") or 0)
        po_price = int(po.get("unit_price") or 0)
        ml = MatchLine(
            key=str(po.get("key") or inv.get("key") or ""),
            description=po.get("description") or desc,
            ordered_qty=ordered, received_qty=received, invoiced_qty=inv_qty,
            po_unit_price=po_price, invoice_unit_price=inv_price,
        )

        if inv_qty > ordered:
            ml.discrepancies.append("over_quantity")
            discrepancies.append({
                "type": "over_quantity", "description": ml.description,
                "ordered_qty": ordered, "invoiced_qty": inv_qty,
            })
        if inv_qty > received:
            ml.discrepancies.append("short_receipt")
            discrepancies.append({
                "type": "short_receipt", "description": ml.description,
                "received_qty": received, "invoiced_qty": inv_qty,
            })
        # Over-price: invoice unit price above the PO price beyond tolerance.
        threshold = po_price * (1 + price_tolerance)
        if inv_price > threshold:
            ml.discrepancies.append("over_price")
            discrepancies.append({
                "type": "over_price", "description": ml.description,
                "po_unit_price": po_price, "invoice_unit_price": inv_price,
            })
        result_lines.append(ml)

    return MatchResult(
        matched=len(discrepancies) == 0,
        lines=result_lines,
        discrepancies=discrepancies,
    )

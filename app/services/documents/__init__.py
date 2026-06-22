"""Branded, themeable document engine (HTML/CSS → PDF via WeasyPrint).

One shared engine renders every customer-facing document — invoice, time
invoice, receipt, account statement, purchase order, payslip — with the
company's logo, brand colour, party cards, accent table, totals box,
amount-in-words, signature/stamp blocks and footer. Locale-aware: `ir` →
Persian/RTL/Jalali, `uk`/default → English/LTR.
"""
from app.services.documents.render import (  # noqa: F401
    render_invoice_pdf,
    render_time_invoice_pdf,
    render_receipt_pdf,
    render_statement_pdf,
    render_purchase_order_pdf,
    render_payslip_pdf,
)

"""Document strings in the two PDF locales: English (uk/default) and Persian (ir).

PDFs render in exactly one of these per the company's locale; the app UI i18n
(en/fa/es/ar) is separate.
"""
from __future__ import annotations

EN = {
    "invoice": "INVOICE",
    "quote": "QUOTE",
    "valid_until": "Valid until",
    "time_invoice": "INVOICE",
    "receipt": "RECEIPT",
    "statement": "ACCOUNT STATEMENT",
    "purchase_order": "PURCHASE ORDER",
    "payslip": "PAYSLIP",
    # the two parties, by their role on each document (render.ROLES)
    "seller": "Seller",
    "buyer": "Buyer",
    "received_by": "Received by",
    "payer": "Paid by",
    "from": "From",
    "account_of": "Account of",
    "supplier": "Supplier",
    "employer": "Employer",
    "employee": "Employee",
    "number": "No.",
    "issue_date": "Issue date",
    "due_date": "Due date",
    "date": "Date",
    "status": "Status",
    "period": "Period",
    "pay_period": "Pay period",
    "po_reference": "PO reference",
    "description": "Description",
    "qty": "Qty",
    "hours": "Hours",
    "rate": "Rate",
    "unit_price": "Unit price",
    "tax": "Tax",
    "amount": "Amount",
    "subtotal": "Subtotal",
    "discount": "Discount",
    "total": "Total",
    "amount_payable": "Amount payable",
    "amount_paid": "Amount paid",
    "balance_due": "Balance due",
    "net_pay": "Net pay",
    "gross_pay": "Gross pay",
    "eidi": "Year-end bonus (Eidi)",
    "sanavat": "Service pay (Sanavat)",
    "days_worked": "Days worked in the year",
    "year_end_payslip": "YEAR-END PAYSLIP",
    "deductions": "Deductions",
    "income_tax": "Income tax",
    "social_security": "National Insurance",
    "amount_in_words": "Amount in words",
    "how_to_pay": "How to pay",
    "payment_terms": "Payment terms",
    "tax_id": "Tax ID / VAT",
    "reg_no": "Reg. no.",
    "economic_code": "Economic code",
    "national_id": "National ID",
    "postal_code": "Postal code",
    "account_no": "Account no.",
    "iban": "IBAN",
    "paid_to": "Paid to",
    "signature": "Signature",
    "stamp": "Stamp",
    "page": "Page",
    "tel": "Tel",
    "email": "Email",
    "web": "Web",
    "received_with_thanks": "Received with thanks",
    "opening_balance": "Opening balance",
    "closing_balance": "Closing balance",
    "debit": "Debit",
    "credit": "Credit",
    "balance": "Balance",
    "regular_hours": "Regular hours",
    "overtime_hours": "Overtime hours",
    "leave_hours": "Paid leave hours",
    "hours_unit": "h",
    "item": "Item",
    "goods_services": "Service / Product",
    "paid_by": "Payment method",      # how a receipt was paid; "Paid by" is now its payer's box
}

FA = {
    "invoice": "فاکتور",
    "quote": "پیش‌فاکتور",
    "valid_until": "معتبر تا",
    "time_invoice": "فاکتور",
    "receipt": "رسید",
    "statement": "صورت‌حساب طرف حساب",
    "purchase_order": "سفارش خرید",
    "payslip": "فیش حقوقی",
    "seller": "فروشنده",
    "buyer": "خریدار",
    "received_by": "دریافت‌کننده",
    "payer": "پرداخت‌کننده",
    "from": "صادرکننده",
    "account_of": "طرف حساب",
    "supplier": "تأمین‌کننده",
    "employer": "کارفرما",
    "employee": "کارمند",
    "number": "شماره",
    "issue_date": "تاریخ صدور",
    "due_date": "سررسید",
    "date": "تاریخ",
    "status": "وضعیت",
    "period": "دوره",
    "pay_period": "دوره پرداخت",
    "po_reference": "شماره سفارش",
    "description": "شرح",
    "qty": "تعداد",
    "hours": "ساعت",
    "rate": "نرخ",
    "unit_price": "قیمت واحد",
    "tax": "مالیات",
    "amount": "مبلغ",
    "subtotal": "جمع جزء",
    "discount": "تخفیف",
    "total": "جمع کل",
    "amount_payable": "مبلغ قابل پرداخت",
    "amount_paid": "مبلغ پرداختی",
    "balance_due": "مانده بدهی",
    "net_pay": "خالص پرداختی",
    "gross_pay": "حقوق ناخالص",
    "eidi": "عیدی و پاداش",
    "sanavat": "حق سنوات",
    "days_worked": "روزهای کارکرد در سال",
    "year_end_payslip": "فیش عیدی و سنوات",
    "deductions": "کسورات",
    "income_tax": "مالیات حقوق",
    "social_security": "بیمهٔ سهم کارمند",
    "amount_in_words": "مبلغ به حروف",
    "how_to_pay": "نحوه پرداخت",
    "payment_terms": "شرایط پرداخت",
    "tax_id": "شناسه مالیاتی",
    "reg_no": "شماره ثبت",
    "economic_code": "شماره اقتصادی",
    "national_id": "شناسه ملی",
    "postal_code": "کد پستی",
    "account_no": "شماره حساب",
    "iban": "شبا",
    "paid_to": "واریز به",
    "signature": "امضا",
    "stamp": "مهر",
    "page": "صفحه",
    "tel": "تلفن",
    "email": "ایمیل",
    "web": "وب‌سایت",
    "received_with_thanks": "دریافت شد، با سپاس",
    "opening_balance": "مانده ابتدای دوره",
    "closing_balance": "مانده پایان دوره",
    "debit": "بدهکار",
    "credit": "بستانکار",
    "balance": "مانده",
    "regular_hours": "کارکرد عادی",
    "overtime_hours": "اضافه‌کاری",
    "leave_hours": "مرخصی با حقوق",
    "hours_unit": "ساعت",
    "item": "کالا",
    "goods_services": "کالا / خدمات",
    "paid_by": "روش پرداخت",
}


def labels_for(locale: str | None) -> dict:
    return FA if (locale or "").lower() == "ir" else EN


# A document's status in words, as the pages name it (02-i18n.js / i18n/fa.js):
# the PDF printed «partially_paid» (scenario S1).
STATUS = {
    "draft": ("Draft", "پیش‌نویس"),
    "issued": ("Issued", "صادرشده"),
    "sent": ("Sent", "ارسال‌شده"),
    "partially_paid": ("Partially paid", "بخشی پرداخت‌شده"),
    "paid": ("Paid", "پرداخت‌شده"),
    "overdue": ("Overdue", "سررسیدگذشته"),
    "credited": ("Credited", "برگشت‌شده"),
    "canceled": ("Canceled", "لغوشده"),
    "cancelled": ("Cancelled", "لغوشده"),
    "voided": ("Voided", "باطل‌شده"),
    "accepted": ("Accepted", "پذیرفته‌شده"),
    "declined": ("Declined", "ردشده"),
    "expired": ("Expired", "منقضی"),
    "converted": ("Invoiced", "فاکتورشده"),
    "partially_received": ("Partially received", "دریافت جزئی"),
    "received": ("Received", "دریافت‌شده"),
    "closed": ("Closed", "بسته‌شده"),
}


def status_label(status: str | None, locale: str | None) -> str:
    """«Partially paid» for ``partially_paid``; a status not listed reads as
    words too («On hold»), never with its underscore."""
    key = (status or "").strip().lower()
    words = STATUS.get(key)
    if words:
        return words[1] if (locale or "").lower() == "ir" else words[0]
    return key.replace("_", " ").capitalize()


# How a payment was made (app/models/payment.py ``method``), for a receipt:
# it printed the raw code («شرایط پرداخت: bank»; scenario BE4).
METHOD = {
    "bank": ("Bank", "بانک"),
    "transfer": ("Bank transfer", "انتقال بانکی"),
    "cash": ("Cash", "نقد"),
    "cheque": ("Cheque", "چک"),
    "credit": ("Credit note", "برگ بستانکار"),
}


def method_label(method: str | None, locale: str | None) -> str:
    key = (method or "bank").strip().lower()
    words = METHOD.get(key)
    if words:
        return words[1] if (locale or "").lower() == "ir" else words[0]
    return key.replace("_", " ").capitalize()

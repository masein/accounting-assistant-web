"""The words the system writes into the books, in the books' language.

Every journal the app posts on its own — an invoice's recognition and its
payments, a bill, opening balances, equity, payroll, mileage, time billing,
depreciation, petty cash — carries a description it writes itself. They were
English f-strings, so an Iranian company's general journal (دفتر روزنامه),
ledgers, statements and PDFs read "Invoice ARM-1805 — receivable" and
"Time billing — … (2026-10-01 → 2026-10-31)" (deep browser test, 2026-10-02).

They are written once, at posting time, in the company's book language:
Persian, with Jalali dates, for an ``ir`` company; English (as before) for
every other. The text is stored as written — changing the language later does
not rewrite old entries, the way paper books work.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

TEXT: dict[str, dict[str, str]] = {
    # ── invoices and bills ──
    "inv_issued": {"en": "Invoice {number} issued", "fa": "صدور فاکتور فروش {number}"},
    "bill_issued": {"en": "Invoice {number} issued", "fa": "ثبت فاکتور خرید {number}"},
    "inv_receivable": {"en": "Invoice {number} — receivable", "fa": "فاکتور {number} — حساب دریافتنی"},
    "inv_revenue": {"en": "Invoice {number} — revenue", "fa": "فاکتور {number} — درآمد فروش"},
    "inv_vat_out": {"en": "Invoice {number} — output VAT", "fa": "فاکتور {number} — مالیات بر ارزش افزوده فروش"},
    "bill_expense": {"en": "Bill {number} — expense", "fa": "فاکتور خرید {number} — هزینه"},
    "bill_payable": {"en": "Bill {number} — payable", "fa": "فاکتور خرید {number} — حساب پرداختنی"},
    "bill_vat_in": {"en": "Bill {number} — input VAT", "fa": "فاکتور خرید {number} — مالیات بر ارزش افزوده خرید"},
    "inv_receipt": {"en": "Invoice {number} receipt", "fa": "دریافت وجه فاکتور {number}"},
    "inv_settle_ar": {"en": "Invoice {number} — settle receivable", "fa": "فاکتور {number} — تسویه حساب دریافتنی"},
    "overpay_credit": {"en": "Overpayment credit — {number}", "fa": "اضافه‌دریافت — {number}"},
    "bill_settle_ap": {"en": "Bill {number} — settle payable", "fa": "فاکتور خرید {number} — تسویه حساب پرداختنی"},
    "overpay_advance": {"en": "Overpayment advance — {number}", "fa": "اضافه‌پرداخت (پیش‌پرداخت) — {number}"},
    "bill_payment": {"en": "Bill {number} payment", "fa": "پرداخت فاکتور خرید {number}"},
    "inv_payment": {"en": "Payment for invoice {number}", "fa": "دریافت وجه فاکتور {number}"},
    "bill_payment_desc": {"en": "Payment for invoice {number}", "fa": "پرداخت فاکتور خرید {number}"},
    "credit_note_sales": {"en": "Credit note — {number}", "fa": "برگشت از فروش — فاکتور {number}"},
    "credit_note_purchase": {"en": "Credit note — {number}", "fa": "برگشت از خرید — فاکتور {number}"},
    "credit_note_ar": {"en": "Credit note reduces receivable — {number}", "fa": "برگ بستانکار — کاهش دریافتنی فاکتور {number}"},
    "credit_note_ap": {"en": "Credit note reduces payable — {number}", "fa": "برگ بستانکار — کاهش پرداختنی فاکتور {number}"},
    "credit_note": {"en": "Credit note against invoice {number}", "fa": "برگ بستانکار فاکتور {number}"},
    "void_payment": {"en": "Void payment on invoice {number}", "fa": "ابطال پرداخت فاکتور {number}"},
    "void_invoice": {"en": "Void invoice {number}", "fa": "ابطال فاکتور {number}"},
    "reversed_payment": {"en": "Reversed payment on invoice {number}", "fa": "برگشت پرداخت فاکتور {number}"},
    # ── opening balances ──
    "opening": {"en": "Opening balances", "fa": "تراز افتتاحیه"},
    "opening_line": {"en": "Opening balance", "fa": "مانده افتتاحیه"},
    # ── equity ──
    "eq_capital_contribution": {"en": "Capital contribution — {name}", "fa": "افزایش سرمایه از آورده — {name}"},
    "eq_contribution_uncap": {"en": "Shareholder contribution (uncapitalised) — {name}", "fa": "آوردهٔ سهامدار (منظورنشده در سرمایه) — {name}"},
    "eq_shareholder_contribution": {"en": "Shareholder contribution — {name}", "fa": "آوردهٔ سهامدار — {name}"},
    "eq_contribution_received": {"en": "Contribution received — {name}", "fa": "دریافت آورده — {name}"},
    "eq_contribution": {"en": "Contribution — {name}", "fa": "آورده — {name}"},
    "eq_revaluation_capitalised": {"en": "Revaluation surplus capitalised", "fa": "انتقال مازاد تجدید ارزیابی به سرمایه"},
    "eq_capital_increase": {"en": "Capital increase", "fa": "افزایش سرمایه"},
    "eq_capital_increase_from": {"en": "Capital increase from {source}", "fa": "افزایش سرمایه از محل {source}"},
    "eq_dividend_declared": {"en": "Dividend declared — {name}", "fa": "تقسیم سود مصوب — {name}"},
    "eq_dividend_to": {"en": "Dividend to {name}", "fa": "سود سهام {name}"},
    "eq_dividend_payable": {"en": "Dividend payable — {name}", "fa": "سود سهام پرداختنی — {name}"},
    "eq_dividend_paid": {"en": "Dividend paid — {name}", "fa": "پرداخت سود سهام — {name}"},
    "eq_dividend_settled": {"en": "Dividend settled — {name}", "fa": "تسویه سود سهام پرداختنی — {name}"},
    "eq_current_account": {"en": "Shareholder current account ({direction}) — {name}", "fa": "حساب جاری سهامدار ({direction}) — {name}"},
    # ── payroll ──
    "pr_income_tax": {"en": "Income tax withheld", "fa": "مالیات حقوق کسرشده"},
    "pr_social": {"en": "Social insurance withheld", "fa": "حق بیمهٔ سهم کارمند"},
    "pr_deductions": {"en": "Pre-tax deductions withheld", "fa": "کسورات پیش از مالیات"},
    "pr_net_payable": {"en": "Net pay payable", "fa": "خالص حقوق پرداختنی"},
    "pr_employer_social": {"en": "Employer social insurance payable", "fa": "حق بیمهٔ سهم کارفرما"},
    "pr_run": {"en": "Payroll {start}–{end}", "fa": "حقوق و دستمزد {start} تا {end}"},
    "pr_void": {"en": "Void payroll {start}–{end}", "fa": "ابطال حقوق و دستمزد {start} تا {end}"},
    "pr_net_settled": {"en": "Net pay settled for payroll {start}–{end}", "fa": "پرداخت خالص حقوق {start} تا {end}"},
    "reversal_line": {"en": "Reversal — {text}", "fa": "برگشت — {text}"},
    # ── expenses, time, purchases ──
    "mileage_claim": {"en": "Mileage claim — {name} ({distance} {unit})", "fa": "هزینهٔ ایاب‌وذهاب — {name} ({distance} {unit})"},
    "mileage_expense": {"en": "Mileage expense", "fa": "هزینهٔ ایاب‌وذهاب"},
    "expenses_payable": {"en": "Employee expenses payable", "fa": "بدهی به کارکنان بابت هزینه"},
    "time_billing": {"en": "Time billing — {client} ({start} → {end})", "fa": "صورتحساب ساعات کار — {client} ({start} تا {end})"},
    "purchase_order": {"en": "Purchase order {number}", "fa": "سفارش خرید {number}"},
    # ── fixed assets and adjustments ──
    "fa_acquisition": {"en": "Acquisition: {name}", "fa": "خرید دارایی: {name}"},
    "fa_depreciation": {"en": "Depreciation {label}", "fa": "استهلاک {label}"},
    "fa_gain": {"en": "Gain on disposal — {text}", "fa": "سود فروش دارایی — {text}"},
    "fa_loss": {"en": "Loss on disposal — {text}", "fa": "زیان فروش دارایی — {text}"},
    "fa_disposal": {"en": "Disposal: {name}", "fa": "واگذاری دارایی: {name}"},
    "adj_accrual": {"en": "Accrual", "fa": "هزینهٔ تعهدی"},
    "adj_accrual_reversal": {"en": "Reversal of accrual {text}", "fa": "برگشت هزینهٔ تعهدی {text}"},
    "adj_prepayment": {"en": "Prepayment", "fa": "پیش‌پرداخت"},
    "adj_depreciation": {"en": "Depreciation", "fa": "استهلاک"},
    "adj_prepayment_release": {"en": "Prepayment release {i}/{n}", "fa": "انتقال پیش‌پرداخت به هزینه ({i} از {n})"},
    "adj_depreciation_n": {"en": "Depreciation {i}/{n}", "fa": "استهلاک ({i} از {n})"},
    # ── stock ──
    "inv_used_to_make": {"en": "Used to make {qty} × {name}", "fa": "مصرف در تولید {qty} × {name}"},
    "inv_produced_from": {"en": "Produced from {n} component(s)", "fa": "تولید از {n} جزء"},
    # ── cash, FX, reversals, petty cash, recurring ──
    "fx_reval": {"en": "Unrealised FX on {ccy} balances at {rate} {base} as of {on}",
                 "fa": "تسعیر ارز {ccy} به نرخ {rate} {base} در تاریخ {on}"},
    "fx_revalued_line": {"en": "Revalued {ccy} {amount} at {rate}", "fa": "تسعیر {amount} {ccy} به نرخ {rate}"},
    "fx_gain_line": {"en": "Unrealised FX gain on {ccy}", "fa": "سود تسعیر ارز {ccy}"},
    "fx_loss_line": {"en": "Unrealised FX loss on {ccy}", "fa": "زیان تسعیر ارز {ccy}"},
    "reversal_of": {"en": "Reversal of {ref}", "fa": "برگشت سند {ref}"},
    "reversal": {"en": "Reversal", "fa": "برگشت"},
    "payment_from_bank": {"en": "Payment from bank", "fa": "پرداخت از بانک"},
    "incl_fee": {"en": "{text} (incl. fee)", "fa": "{text} (با کارمزد)"},
    "petty_deposit": {"en": "Petty cash deposit — {name}", "fa": "شارژ تنخواه — {name}"},
    "petty_expense": {"en": "Petty cash: {text}", "fa": "تنخواه: {text}"},
    "recurring": {"en": "{name} (recurring)", "fa": "{name} (تکرارشونده)"},
    "fee_note": {"en": "Transaction fee - {method} via {bank}", "fa": "کارمزد تراکنش - {method} از طریق {bank}"},
    "fee_deduction": {"en": "Bank fee deduction - {bank}", "fa": "کسر کارمزد بانک - {bank}"},
    "stmt_row": {"en": "Bank statement row #{n}", "fa": "ردیف {n} صورت‌حساب بانکی"},
    "quote_invoice": {"en": "Quote {number}", "fa": "پیش‌فاکتور {number}"},
    "imported_voucher": {"en": "Imported voucher {number}", "fa": "سند واردشده {number}"},
    # ── accounts the system opens itself ──
    "bank_account_name": {"en": "{name} — bank account", "fa": "حساب بانکی {name}"},
    # ── more equity, mileage, payroll, adjustment lines ──
    "eq_cash_for_increase": {"en": "Cash for capital increase", "fa": "آوردهٔ نقدی برای افزایش سرمایه"},
    "eq_retained_capitalised": {"en": "Retained earnings capitalised", "fa": "انتقال سود انباشته به سرمایه"},
    "eq_increase_capital_line": {"en": "Increase in share capital", "fa": "افزایش سرمایهٔ ثبت‌شده"},
    "eq_loan_from": {"en": "Loan from {name}", "fa": "دریافت از {name} (حساب جاری)"},
    "eq_owed_to": {"en": "Owed to {name} (current account)", "fa": "بدهی به {name} (حساب جاری)"},
    "eq_withdrawal_by": {"en": "Withdrawal by {name} (current account)", "fa": "برداشت {name} (حساب جاری)"},
    "eq_paid_to": {"en": "Paid to {name}", "fa": "پرداخت به {name}"},
    "exp_clear_payable": {"en": "Clear employee payable", "fa": "تسویه بدهی به کارمند"},
    "exp_reimbursed": {"en": "Mileage reimbursed from bank", "fa": "پرداخت هزینهٔ ایاب‌وذهاب از بانک"},
    "exp_reimburse": {"en": "Reimburse mileage — {name}", "fa": "پرداخت هزینهٔ ایاب‌وذهاب — {name}"},
    "eq_current_event": {"en": "Current account {direction} — {name}", "fa": "حساب جاری {direction} — {name}"},
    "pr_gross": {"en": "Gross wages", "fa": "حقوق و دستمزد ناخالص"},
    "pr_clear_net": {"en": "Clear net pay payable", "fa": "تسویه خالص حقوق پرداختنی"},
    "pr_net_paid": {"en": "Net pay paid from bank", "fa": "پرداخت خالص حقوق از بانک"},
    "adj_accrued_income": {"en": "Accrued income", "fa": "درآمد تحقق‌یافته دریافت‌نشده"},
    "adj_accrued_liability": {"en": "Accrued liability", "fa": "هزینهٔ تحقق‌یافته پرداخت‌نشده"},
    "adj_prepayment_paid": {"en": "Prepayment paid", "fa": "پرداخت پیش‌پرداخت"},
    "adj_release_prepaid": {"en": "Release prepaid asset", "fa": "کاهش پیش‌پرداخت"},
    "adj_accumulated_dep": {"en": "Accumulated depreciation", "fa": "استهلاک انباشته"},
}

# The words a description takes as a value (an equity source, a direction, a unit).
VALUES: dict[str, dict[str, str]] = {
    "retained_earnings": {"en": "retained earnings", "fa": "سود انباشته"},
    "revaluation_surplus": {"en": "revaluation surplus", "fa": "مازاد تجدید ارزیابی"},
    "cash": {"en": "cash", "fa": "آوردهٔ نقدی"},
    "withdrawal": {"en": "withdrawal", "fa": "برداشت"},
    "deposit": {"en": "deposit", "fa": "واریز"},
    "km": {"en": "km", "fa": "کیلومتر"},
    "mi": {"en": "mi", "fa": "مایل"},
    # the payment methods' own names (transaction_fee.DEFAULT_PAYMENT_METHODS)
    "Paya": {"en": "Paya", "fa": "پایا"},
    "Satna": {"en": "Satna", "fa": "ساتنا"},
    "Card-to-Card": {"en": "Card-to-Card", "fa": "کارت به کارت"},
    "Internal Transfer": {"en": "Internal Transfer", "fa": "انتقال داخلی"},
}


def book_language(db: Session) -> str:
    """'fa' for an Iranian company's books, 'en' for every other."""
    from app.db.tenant import get_current_company
    from app.models.company import Company

    cid = get_current_company()
    if not cid:
        return "en"
    try:
        import uuid
        company = db.get(Company, uuid.UUID(str(cid)))
    except (ValueError, TypeError):
        return "en"
    return "fa" if company is not None and (company.locale or "").lower() == "ir" else "en"


def book_date(db: Session, d: date | str | None, lang: str | None = None) -> str:
    """A date as the books write it: Jalali (1405/07/09) in Persian books, ISO otherwise."""
    if d is None:
        return ""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    if (lang or book_language(db)) == "fa":
        from app.utils.jalali import format_jalali
        return format_jalali(d)
    return d.isoformat()


def book_value(db: Session | None, value: str, lang: str | None = None) -> str:
    """A value a description takes (VALUES), in ``lang`` or the books' language."""
    said = VALUES.get(str(value))
    return said.get(lang or book_language(db), value) if said else str(value)


def said(lang: str, key: str, **params) -> str:
    """``key``'s text in ``lang`` ('fa' or 'en'), for code that has no session."""
    return TEXT[key]["fa" if lang == "fa" else "en"].format(**params)


def bt(db: Session, key: str, **params) -> str:
    """``key``'s text in the company's book language, ``params`` filled in."""
    return said(book_language(db), key, **params)

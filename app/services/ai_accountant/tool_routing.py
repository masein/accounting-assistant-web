"""Which tools and which parts of the instructions a chat message needs (2026-10-22).

Every call sent the model all its tools (34 in a person's books, 59 in a company's — about 13k and 20k
tokens) and all 11k tokens of instructions, for "I had lunch for 7 Omani rials". Now a message gets the
core tools and instructions, plus the groups its words — or the conversation just before it — are about.
``more_tools`` lets the model add any group it finds missing, in the same turn, so routing never leaves it
without a tool it needs.

The groups are ordered and the core comes first, so the start of what is sent stays the same from one
message to the next and a provider's prompt cache still serves it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Always offered: finding a party or an account, the voucher card, a question with buttons, the ledger.
CORE: tuple[str, ...] = (
    "find_entity", "search_accounts", "propose_create_transaction", "ask_with_choices",
    "query_ledger", "get_account_balance", "propose_reverse_transaction", "get_company_defaults", "more_tools",
)

# group → (what it is for, in one line for ``more_tools``; its tools)
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "money": ("cash and bank today, each place and currency, people owed and owing, net worth, money places, "
              "renaming a place, the books' currency, money changed from one currency to another",
              ("get_cash_position", "get_people_balances", "get_net_worth", "propose_money_place",
               "propose_rename_place", "propose_books_currency", "propose_currency_exchange", "propose_empty_place")),
    "spending": ("spending and income for a period and by category, the monthly report card, insights, "
                 "financial statements, tax, the cash forecast",
                 ("get_spending_summary", "get_report_card", "get_insights", "get_financial_statement",
                  "get_tax_summary", "get_cash_forecast")),
    "planning": ("budgets, savings goals, can I afford it, the weekly and monthly reviews' settings",
                 ("get_budget_status", "propose_set_budget", "get_savings_goals", "propose_savings_goal",
                  "check_affordability", "propose_review_prefs")),
    "parties": ("listing, creating or changing clients, suppliers, employees and banks; what each owes",
                ("list_entities", "propose_create_entity", "propose_update_entity", "get_party_balances")),
    "statements": ("a bank statement checked against the books, its rows posted",
                   ("review_bank_statement", "propose_post_statement")),
    "commitments": ("installments and cheques: what is due, settling, bouncing, a cheque's steps",
                    ("list_commitments", "propose_settle_commitment", "propose_bounce_cheque",
                     "propose_create_cheque", "propose_cheque_step")),
    "invoices": ("sales and purchase invoices: listing, one invoice, a payment against it, a new one",
                 ("list_invoices", "get_invoice", "propose_record_invoice_payment", "propose_create_invoice")),
    "time": ("time tracking and billing clients for hours",
             ("list_unbilled_time", "get_time_summary", "propose_log_time", "propose_create_project",
              "propose_set_billable_rate", "propose_create_invoice_from_time")),
    "equity": ("shareholders: contributions, a capital increase, dividends, current accounts, the cap table",
               ("propose_shareholder_contribution", "propose_capital_increase", "propose_declare_dividend",
                "propose_shareholder_current_account", "get_cap_table")),
    "payroll": ("payroll: pay runs, posting and paying them",
                ("get_payroll", "propose_run_payroll", "propose_post_pay_run", "propose_pay_pay_run")),
    "operations": ("fixed assets, stock, purchase orders, recurring rules, petty cash, exchange rates",
                   ("get_fixed_assets", "get_inventory", "list_purchase_orders", "list_recurring_rules",
                    "propose_create_recurring_rule", "get_petty_cash", "get_exchange_rates")),
    "close": ("month end: the close checklist, locking a period, the audit trail",
              ("get_close_checklist", "propose_lock_period", "get_audit_trail")),
    "memory": ("remembering a standing choice ('always', 'from now on')", ("propose_remember_preference",)),
    "dates": ("giving entries already in the books the date they really had", ("propose_change_date",)),
}

_I = re.IGNORECASE
# What a message's words say it is about. Persian, English, Spanish and Arabic; generous on purpose:
# a group too many costs a few hundred tokens, a group missing costs a ``more_tools`` call.
KEYWORDS: dict[str, re.Pattern] = {
    "money": re.compile(
        r"پول|کیف|گاوصندوق|صندوق|موجودی|بدهکار|بستانکار|طلب|دارایی|ارز|اسم|نام|جیب|پاکت|کشو|امانت|قرض|چنج|تبدیل|صرافی|"
        r"\b(cash|money|wallet|purse|vault|safe|balance|owe|owes|owed|lent|lend|borrow|loan|worth|currency|rename|"
        r"exchang\w*|chang(e|ed) \d+|convert\w*|"
        r"envelope|drawer|how much do i have|dinero|cartera|debo|deben|saldo|moneda|محفظة|نقود|مال|رصيد|عملة|أدين|يدين)\b", _I),
    "spending": re.compile(
        r"خرج|هزینه|دسته|ماه|هفته|امسال|سال|امروز|دیروز|کارنامه|گزارش|سود|زیان|ترازنامه|ترازآزمایشی|مالیات|ارزش افزوده|"
        r"پیش[‌ ]?بینی|درآمد|تغییر|چه خبر|اوضاع|وضع|چطوره|چطور است|"
        r"\b(spen[dt]|spending|categor\w*|week|month|year|today|yesterday|report|profit|loss|balance sheet|"
        r"trial balance|tax|vat|forecast|income|earn\w*|revenue|insight\w*|what changed|how are things|going|"
        r"look at|anything|gast\w*|ingres\w*|informe|"
        r"impuesto|أنفق|إنفاق|مصاريف|دخل|تقرير|ضريبة|ربح)\b", _I),
    "planning": re.compile(
        r"بودجه|هدف|پس[‌ ]?انداز|بخرم|بخرمش|می[‌ ]?تونم|میتونم|قسطی|مرور|یادآوری|"
        r"\b(budget\w*|goal\w*|sav(e|ing)\w*|afford|review\w*|remind\w*|presupuesto|meta|ahorr\w*|ميزانية|هدف|ادخار|مراجعة)\b", _I),
    "parties": re.compile(
        r"مشتری|تامین|تأمین|کارمند|طرف[‌ ]?حساب|شخص|اشخاص|"
        r"\b(client\w*|customer\w*|supplier\w*|vendor\w*|employee\w*|part(y|ies)|contact\w*|iban|"
        r"proveedor\w*|empleado\w*|عميل|مورد|موظف)\b", _I),
    "statements": re.compile(
        r"صورت[‌ ]?حساب|گردش|اکسل|"
        r"\b(statement\w*|bank file|csv|extracto|كشف حساب)\b", _I),
    "commitments": re.compile(
        r"قسط|اقساط|چک|سررسید|برگشت|"
        r"\b(cheque\w*|check|installment\w*|instalment\w*|due|bounce\w*|cuota\w*|cheque|شيك|قسط)\b", _I),
    "invoices": re.compile(
        r"فاکتور|صورت[‌ ]?حساب فروش|بدهکار|بستانکار|طلب|مطالبات|"
        r"\b(invoice\w*|bill\w*|receivable\w*|payable\w*|owe\w*|unpaid|outstanding|factura\w*|فاتورة)\b", _I),
    "time": re.compile(
        r"ساعت|پروژه|کارکرد|\b(timesheet\w*|hours?|project\w*|billable|horas|proyecto|ساعات|مشروع)\b", _I),
    "equity": re.compile(
        r"سهام|سهامدار|شریک|سرمایه|سود سهام|"
        r"\b(shareholder\w*|dividend\w*|capital|equity|cap table|socio\w*|dividendo\w*|مساهم|أرباح|رأس المال)\b", _I),
    "payroll": re.compile(
        r"حقوق|دستمزد|فیش|\b(payroll|salar\w*|wages?|pay|paid|pay ?run|payslip\w*|nómina|sueldo|رواتب|راتب)\b", _I),
    "operations": re.compile(
        r"دارایی ثابت|استهلاک|انبار|کالا|سفارش خرید|تکرار|تنخواه|نرخ|هر ماه|ماهانه|ماهیانه|خودکار|"
        r"\b(fixed asset\w*|depreciation|inventory|stock|purchase order\w*|recurring|every month|monthly|each month|"
        r"automatic\w*|standing|petty cash|exchange rate\w*|"
        r"activo fijo|inventario|tipo de cambio|مخزون|استهلاك)\b", _I),
    "close": re.compile(
        r"بستن|قفل|حسابرسی|تاریخچه|پایان ماه|\b(close|closing|lock\w*|audit\w*|checklist|cierre|إقفال|تدقيق)\b", _I),
    "memory": re.compile(r"همیشه|از این به بعد|از حالا|یادت باشه|\b(always|from now on|remember|siempre|دائما)\b", _I),
    "dates": re.compile(r"تاریخ|\b(dates?|dated|re-?date|fecha|تاريخ)\b", _I),
}


def groups_for_tools(names: Iterable[str]) -> set[str]:
    """The groups these tools belong to (a core tool belongs to none)."""
    out = set()
    for name in names:
        for g, (_desc, tools) in GROUPS.items():
            if name in tools:
                out.add(g)
    return out


def route(message: str | None, *, recent_tools: Iterable[str] = (), attachments: bool = False,
          personal: bool = False) -> set[str]:
    """The groups a message needs: those its words name, those the conversation just used (an answer like
    «کیف پول قشنگم» carries on what came before), and those a document needs."""
    text = message or ""
    picked = {g for g, pattern in KEYWORDS.items() if pattern.search(text)}
    picked |= groups_for_tools(recent_tools)
    if attachments:
        picked |= {"invoices", "statements", "parties"}
    if personal:
        picked -= {"invoices", "time", "equity", "payroll", "operations", "close"}   # not a person's
    return picked


def tool_names(groups: Iterable[str], available: set[str]) -> list[str]:
    """Core first, then each group in its fixed order: the same message shape gives the same list."""
    wanted = set(groups)
    out = [t for t in CORE if t in available]
    for g, (_desc, tools) in GROUPS.items():
        if g in wanted:
            out += [t for t in tools if t in available and t not in out]
    return out


def more_tools_spec() -> dict[str, Any]:
    """The tool the model calls to add a group, each group in one line. The same on every call: it is the
    last tool, and a description that changed would change what a prompt cache serves."""
    lines = "\n".join(f"- {g}: {desc}" for g, (desc, _t) in GROUPS.items())
    return {
        "name": "more_tools",
        "description": ("Add tools for this message. Only the core tools and those this conversation is about are "
                        "offered; when you need one you don't have — to read a budget, list invoices, check a "
                        "statement — call this with the groups you need, then use them. Groups:\n" + lines),
        "input_schema": {"type": "object", "properties": {
            "groups": {"type": "array", "items": {"type": "string", "enum": list(GROUPS)},
                       "description": "The groups to add."}}, "required": ["groups"]},
    }


# --- the instructions -------------------------------------------------------------------------------------------

# A section of the instructions (its heading's start) → the groups it is for; the rest are always sent.
SECTION_GROUPS: dict[str, tuple[str, ...]] = {
    "Invoices, cheques and installments": ("invoices", "commitments"),
    "Payroll, budgets and month end": ("payroll", "planning", "close"),
    "Orders, schedules, shareholders, rates, petty cash": ("operations", "equity", "close"),
    "Time tracking & billing clients for hours": ("time",),
    "Shareholder equity": ("equity",),
    "Financial statements": ("spending",),
    "Tax / VAT questions": ("spending",),
    "Proactive insights": ("spending",),
    "Bank statements": ("statements",),
    "A statement posted by the user's own rules": ("statements",),
    "The app's own reviews": ("planning",),
    "Can I afford it?": ("planning",),
    "Attached documents": ("attachments",),
    "A reply to an earlier message": ("reply",),
}


def split_sections(prompt: str) -> list[tuple[str, str]]:
    """The instructions as (heading, text) pieces, in order; the text before the first heading has ''."""
    parts = prompt.split("\n# ")
    out = [("", parts[0])]
    for p in parts[1:]:
        out.append((p.split("\n", 1)[0], "\n# " + p))
    return out


def instructions(prompt: str, groups: set[str]) -> str:
    """The instructions this message needs: every section not tied to a group, and the sections of the groups
    chosen (``attachments`` and ``reply`` are groups here too), in the order they were written — the model
    reads its rules as they always were, less the ones about other things."""
    out = []
    for heading, text in split_sections(prompt):
        tied = next((gs for start, gs in SECTION_GROUPS.items() if heading.startswith(start)), None)
        if tied is None or set(tied) & groups:
            out.append(text)
    return "".join(out)

"""Common questions answered from the books without the model (roadmap
ROADMAP_ANDROID_CHAT P0.6).

"How much cash do we have?", "who owes us?", «موجودی چقدره؟», «بودجه چقدر
مونده؟»: the same read tools the accountant would call, run directly. The
answer is exact, comes back in well under a second instead of ~9, costs no
tokens, and works when the AI is down. The tool results ride along as
``tool_calls``, so a chat client draws them as blocks like any other reply.

Deliberately narrow: a short question, no amounts (an amount means "record
something" and belongs to the model), nothing about the future (a forecast is
another tool), and only phrasings that can't mean anything else. Anything
else goes to the model as before.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.ai_accountant.base import ToolContext
from app.core.money import plain, grouped, grouped_fa, signed_fa

MAX_LENGTH = 80

_DIGITS = re.compile(r"[0-9۰-۹٠-٩]")
_FUTURE = re.compile(
    r"\b(next|will|forecast|tomorrow|predict|projection|end of (the )?(month|year))\b"
    r"|آینده|پیش[‌ ]?بینی|فردا|ماه بعد|هفته بعد|خواهیم|خواهد|آخر ماه|آخر سال"
    r"|pr[oó]xim|pron[oó]stico|previsi[oó]n|ma[nñ]ana|el mes que viene|القادم|توقع|غدا|غدًا",
    re.IGNORECASE,
)

# A question that also asks for something to be done belongs to the model:
# "bank balance, and record the rent" is two requests, not one question.
_ACTIONS = re.compile(
    r"\b(record|post|book|enter|add|create|pay|paid|invoice|bill (them|him|her)|transfer|send|move|delete|undo|and)\b"
    r"|ثبت|بزن|پرداخت کن|پرداخت شد|بفرست|اضافه کن|بساز|واریز|منتقل|حذف|برگردون"
    r"|\b(registra|anota|apunta|paga|factura|transfiere|env[ií]a|borra|y)\b|سجّل|سجل|ادفع|حوّل|أرسل|احذف",
    re.IGNORECASE,
)

# «چه دسته‌بندی‌هایی داری؟» asks for the categories themselves (2026-10-21: it was answered with the year's
# spending); «هفته گذشته خرج ما چیا بوده و توی چه دسته‌بندی‌هایی؟» asks for spending, so it isn't this one
_SPENT = re.compile(r"خرج|هزینه|پول|هفته|ماه|امسال|سال|امروز|دیروز|\bspen[dt]|\bspending\b|\bweek|\bmonth|\byear|\btoday"
                    r"|gast|semana|\bmes\b|año|أنفق|صرف|إنفاق|أسبوع|شهر|سنة|اليوم", re.IGNORECASE)

# …but «برای خرج» in «چه دسته‌بندی‌هایی برای خرج داری؟» and "spending" in "what spending categories do I have?" only
# say which categories: the question went to the model, which searched nine times and missed «سفر» (AY2)
_FOR_SPENDING = re.compile(r"(برای|واسه|مال) (خرج|هزینه)(‌?ها)?|\b(spending|expense|income) categor\w*"
                           r"|\bcategor\w* (for|of) (spending|expenses?)|categor[ií]as de gasto|فئات (الإنفاق|المصاريف)",
                           re.IGNORECASE)

# A second question joined to the first: «چقدر پول داریم کلا و چقدر بدهی داریم» got only the money, its debts
# never answered (owner's phone, 2026-10-22, scenario AZ4) — both belong to the model.
_TWO_QUESTIONS = re.compile(
    r"\sو\s*(چقدر|چند|کی|چی|چه|کجا|کدوم|کدام)|\band (how|what|who|where|which)\b"
    r"|\by (cu[aá]nto|qu[eé]|qui[eé]n|d[oó]nde)\b|\sو\s?(كم|ماذا|من|أين)", re.IGNORECASE)

# Payables before receivables: «به کی بدهکاریم؟» (who do we owe) contains «کی بدهکار».
_INTENTS: list[tuple[str, re.Pattern]] = [
    ("categories", re.compile(
        r"\bwhat (spending |expense |income )?categories\b|\bwhich categories\b|\b(list|show) (my |the )?categories\b"
        r"|\bmy (spending |expense |income )?categories\b"
        r"|چه دسته[‌ ]?بندی|دسته[‌ ]?بندی[‌ ]?(ها|های)?(م|مون|ت)? ?(چی|چیا|کدوم|کدام)|کدوم دسته[‌ ]?بندی|چه دسته[‌ ]?(ها|های)"
        r"|qu[eé] categor[ií]as|cu[aá]les son (mis|las) categor[ií]as|ما (هي )?الفئات|أي فئات",
        re.IGNORECASE)),
    ("payables", re.compile(
        r"\bwhat do (we|i) owe\b|\bpayables?\b|\bbills? (due|to pay)\b|\bwho do (we|i) owe\b"
        r"|به کی بدهکار|بستانکارا?ن|بدهی[‌ ]?(ها|های) ?(ما|مون|من)|چقدر بدهکاری?م"
        r"|qu[eé] (debemos|debo)\b|a qui[eé]n (le )?(debemos|debo)|cuentas por pagar|ماذا علينا|ماذا ندين|لمن ندين|الذمم الدائنة",
        re.IGNORECASE)),
    ("receivables", re.compile(
        r"\bwho owes (me|us)\b|\breceivables?\b|\bunpaid (sales )?invoices\b|\boverdue invoices\b"
        r"|(?<!به )کی (به ?من |بهم |به ?ما )?بدهکار|بدهکارا?ن(م|مان)?\b|مطالبات|طلب[‌ ]?(ها|های)?(م|مون|مان)? ?(چقدر|کجاست)|فاکتورهای (پرداخت[‌ ]?نشده|باز|سررسید[‌ ]?گذشته)"
        r"|qui[eé]n (nos|me) debe|cuentas por cobrar|facturas (pendientes|impagadas|vencidas)|من (يدين|مدين) (لنا|لي)|الذمم المدينة|الفواتير غير المدفوعة",
        re.IGNORECASE)),
    ("budget", re.compile(
        r"\bbudgets?\b.*\b(left|status|look|going|remaining|used)\b|\bhow('?s| is| are) (my|our|the) budgets?\b"
        r"|بودجه|presupuesto|الميزانية|ميزانيت(ي|نا)",
        re.IGNORECASE)),
    ("spending", re.compile(
        r"\bhow much (did|have) (i|we) spen[dt]\b|\bspending this month\b|\bwhat did (i|we) spend\b"
        r"|خرج(ِ)? این ماه|این ماه چقدر خرج|چقدر خرج کرد(م|یم)|هزینه[‌ ]?(ها|های)? این ماه"
        r"|cu[aá]nto (gastamos|gast[eé]|hemos gastado) este mes|gastos de este mes|كم (أنفقنا|أنفقت|صرفنا|صرفت) هذا الشهر|مصاريف هذا الشهر",
        re.IGNORECASE)),
    ("net_worth", re.compile(
        r"\bnet worth\b|\bhow much am i worth\b|\bwhat am i worth\b"
        r"|دارایی خالص|ارزش خالص|کل دارایی ?(ها)?(م| من)|چقدر می[‌ ]?ارزم"
        r"|patrimonio neto|cu[aá]nto valgo|صافي (الثروة|القيمة)|كم أملك",
        re.IGNORECASE)),
    ("cash", re.compile(
        r"\bhow much (cash|money) (do|have) (we|i)\b|\bcash (balance|position|on hand)\b|\bbank balance\b"
        r"|\bwhat'?s in the bank\b|\bhow much is in the bank\b"
        r"|موجودی (نقد|بانک|حساب|حساب[‌ ]?ها|کل)?|نقدینگی|چقدر پول (داریم|دارم|مونده)|پول نقد"
        r"|cu[aá]nto dinero (tenemos|tengo|hay)|saldo (de caja|del banco|en el banco)|efectivo disponible"
        r"|كم (لدينا|عندنا|لدي|لديّ|عندي) من (المال|النقود)|الرصيد النقدي|رصيد (البنك|الصندوق)",
        re.IGNORECASE)),
]


# The forecast is about the future by nature, so it is matched before the
# future words send a question to the model; a what-if stays with the model.
_FORECAST = re.compile(
    r"\bcash ?flow forecast\b|\bcash forecast\b|\bwill (we|i) have enough (cash|money)\b|\bwhen (do|will) (we|i) run (short|out)\b"
    r"|پیش[‌ ]?بینی (نقدینگی|جریان نقد|نقد|پول)|کی پول کم میاری?م|پول(مون)? کم میاد"
    r"|previsi[oó]n de (caja|tesorer[ií]a|efectivo)|pron[oó]stico de (caja|efectivo)|tendremos (suficiente )?(dinero|efectivo)"
    r"|توقعات? (النقد|السيولة)|التدفق النقدي المتوقع|هل سيكفينا (المال|النقد)",
    re.IGNORECASE)
_WHAT_IF = re.compile(r"\bwhat if\b|\bif\b|اگه|اگر|qu[eé] pasa si|\bsi\b|ماذا لو|إذا", re.IGNORECASE)


def match(message: str) -> str | None:
    """The intent of a short question the books can answer directly, or None."""
    text = (message or "").strip()
    if not text or len(text) > MAX_LENGTH or _DIGITS.search(text) or _ACTIONS.search(text) or _TWO_QUESTIONS.search(text):
        return None
    if _FORECAST.search(text):
        return None if _WHAT_IF.search(text) else "forecast"
    if _FUTURE.search(text):
        return None
    for name, pattern in _INTENTS:
        if name == "categories" and _SPENT.search(_FOR_SPENDING.sub(" ", text)):
            continue                                  # what was spent in them is the model's (or spending's)
        if pattern.search(text):
            return name
    return None


@dataclass
class FastAnswer:
    intent: str
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _money(n: int, lang: str) -> str:
    return signed_fa(grouped_fa(n)) if lang == "fa" else grouped(n)


def _cur(code: str | None, lang: str) -> str:
    from app.core.currencies import after_amount
    return after_amount(code, lang)


_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def _in_currency(n, code: str, lang: str) -> str:
    """«۷۷٫۵۰۰ ریال عمان», "77.500 OMR": to the currency's own places, named in Persian."""
    from app.core.money import exponent, to_decimal
    places = exponent(code)
    figure = f"{int(to_decimal(n)):,}" if places == 0 else f"{to_decimal(n):,.{places}f}"
    if lang == "fa":
        from app.core.money import persian_figures
        return f"{signed_fa(persian_figures(figure))} {_cur(code, lang)}"
    return f"{figure} {code}"


def _priced(n: int, code: str | None, lang: str) -> str:
    """An amount with its currency as the reader writes it: «£23,923» in
    English, Spanish and Arabic for a currency with a symbol (it read
    «23,923 GBP» beside a card saying «£23,923», 2026-10-12); otherwise the
    number then the code, «۲۳٬۹۲۳ ریال»."""
    code = (code or "").upper()
    if lang != "fa" and code in _SYMBOLS:
        return ("-" if n < 0 else "") + _SYMBOLS[code] + _money(abs(n), lang)
    return f"{_money(n, lang)} {_cur(code, lang)}".strip()


_T = {
    "cash": {"en": "Cash and bank today: {total_p}.", "fa": "موجودی نقد و بانک امروز: {total} {cur}.",
             "es": "Efectivo y bancos hoy: {total_p}.", "ar": "النقد والبنوك اليوم: {total_p}."},
    "cash_currencies": {"en": "Cash and bank today, each currency as it is: {list}.",
                        "fa": "موجودی نقد و بانک امروز، هر ارز جداگانه: {list}.",
                        "es": "Efectivo y bancos hoy, cada moneda tal cual: {list}.",
                        "ar": "النقد والبنوك اليوم، كل عملة كما هي: {list}."},
    "receivables_one": {"en": "1 sales invoice is still open: {amounts} to collect.",
                        "fa": "یک فاکتور فروش هنوز تسویه نشده: {amounts} باید دریافت شود.",
                        "es": "1 factura de venta sigue abierta: {amounts} por cobrar.",
                        "ar": "فاتورة مبيعات واحدة ما زالت مفتوحة: {amounts} للتحصيل."},
    "payables_one": {"en": "1 supplier bill is still open: {amounts} to pay.",
                     "fa": "یک فاکتور خرید هنوز تسویه نشده: {amounts} باید پرداخت شود.",
                     "es": "1 factura de proveedor sigue abierta: {amounts} por pagar.",
                     "ar": "فاتورة مورد واحدة ما زالت مفتوحة: {amounts} للدفع."},
    "receivables": {"en": "{n} sales invoices are still open: {amounts} to collect.",
                    "fa": "{n} فاکتور فروش هنوز تسویه نشده: {amounts} باید دریافت شود.",
                    "es": "{n} facturas de venta siguen abiertas: {amounts} por cobrar.",
                    "ar": "{n} فواتير مبيعات ما زالت مفتوحة: {amounts} للتحصيل."},
    "payables": {"en": "{n} supplier bills are still open: {amounts} to pay.",
                 "fa": "{n} فاکتور خرید هنوز تسویه نشده: {amounts} باید پرداخت شود.",
                 "es": "{n} facturas de proveedores siguen abiertas: {amounts} por pagar.",
                 "ar": "{n} فواتير موردين ما زالت مفتوحة: {amounts} للدفع."},
    "spending": {"en": "This month's spending so far: {total_p}.", "fa": "خرج این ماه تا امروز: {total} {cur}.",
                 "es": "Gasto de este mes hasta hoy: {total_p}.", "ar": "إنفاق هذا الشهر حتى اليوم: {total_p}."},
    "categories": {"en": "Spending categories: {spend}. Income: {income}.",
                   "fa": "دسته‌بندی‌های خرج: {spend}. درآمد: {income}.",
                   "es": "Categorías de gasto: {spend}. Ingresos: {income}.",
                   "ar": "فئات الإنفاق: {spend}. الدخل: {income}."},
    "spending_in": {"en": "This month's spending so far: {list}.", "fa": "خرج این ماه تا امروز: {list}.",
                    "es": "Gasto de este mes hasta hoy: {list}.", "ar": "إنفاق هذا الشهر حتى اليوم: {list}."},
    "spending_currencies": {"en": "This month's spending so far, each currency as it is: {list}.",
                            "fa": "خرج این ماه تا امروز، هر ارز جداگانه: {list}.",
                            "es": "Gasto de este mes hasta hoy, cada moneda tal cual: {list}.",
                            "ar": "إنفاق هذا الشهر حتى اليوم، كل عملة كما هي: {list}."},
    "budget": {"en": "This month: {actual_p} of {budget_p} spent, {left_p} left.",
               "fa": "این ماه: {actual} از {budget} {cur} خرج شده، {left} مانده.",
               "es": "Este mes: {actual_p} de {budget_p} gastados, quedan {left_p}.",
               "ar": "هذا الشهر: أُنفق {actual_p} من {budget_p}، وتبقّى {left_p}."},
    # spent past the budget: not a negative «… -۵۰٬۰۰۰ مانده» (Persian copy review, 2026-10-31)
    "budget_over": {"en": "This month: {actual_p} of {budget_p} spent, {over_p} over budget.",
                    "fa": "این ماه: {actual} از {budget} {cur} خرج شده، {over} فراتر از بودجه.",
                    "es": "Este mes: {actual_p} de {budget_p} gastados, {over_p} por encima del presupuesto.",
                    "ar": "هذا الشهر: أُنفق {actual_p} من {budget_p}، بتجاوز {over_p} للميزانية."},
    "forecast": {"en": "Cash over the next {weeks} weeks, an estimate: from {opening_p} to {closing_p}; the lowest, {lowest_p}, in the week of {low_week}.",
                 "fa": "پیش‌بینی نقدینگی {weeks} هفتهٔ آینده: از {opening} به {closing} {cur}؛ کمترین مقدار {lowest} در هفتهٔ {low_week}.",
                 "es": "Caja en las próximas {weeks} semanas, una estimación: de {opening_p} a {closing_p}; el mínimo, {lowest_p}, la semana del {low_week}.",
                 "ar": "النقد في الأسابيع الـ{weeks} القادمة، تقديرًا: من {opening_p} إلى {closing_p}؛ الأدنى، {lowest_p}، في أسبوع {low_week}."},
    "forecast_negative": {"en": " It goes below zero in the week of {week}.", "fa": " در هفتهٔ {week} منفی می‌شود.",
                          "es": " Baja de cero la semana del {week}.", "ar": " ينخفض تحت الصفر في أسبوع {week}."},
    # the lowest week is the first below zero: the week once («… در هفتهٔ ۹ آذر ۱۴۰۵. در هفتهٔ ۹ آذر ۱۴۰۵ منفی می‌شود.»)
    "forecast_negative_same": {"en": "; it goes below zero that week.", "fa": "؛ از همان هفته منفی می‌شود.",
                               "es": "; esa semana baja de cero.", "ar": "؛ وينخفض تحت الصفر في ذلك الأسبوع."},
    "net_worth": {"en": "Your net worth today: {net_p}: {assets_p} owned, {owed_p} owed.",
                  "fa": "دارایی خالص شما امروز: {net} {cur}؛ {assets} دارایی، {owed} بدهی.",
                  "es": "Tu patrimonio neto hoy: {net_p}: {assets_p} en activos, {owed_p} en deudas.",
                  "ar": "صافي ثروتك اليوم: {net_p}: {assets_p} أصول، {owed_p} ديون."},
    "net_worth_currencies": {"en": "Your net worth today, each currency as it is: {list}.",
                             "fa": "دارایی خالص شما امروز، هر ارز جداگانه: {list}.",
                             "es": "Tu patrimonio neto hoy, cada moneda tal cual: {list}.",
                             "ar": "صافي ثروتك اليوم، كل عملة كما هي: {list}."},
    "net_worth_gain": {"en": " Gold and currency at today's rates add {gain_p}.", "fa": " طلا و ارز به نرخ امروز {gain} بیشتر می‌ارزند.",
                       "es": " El oro y las divisas a precio de hoy suman {gain_p}.", "ar": " الذهب والعملات بسعر اليوم تضيف {gain_p}."},
    "bank_one": {"en": "{name} today: {amount_p}.", "fa": "موجودی {name} امروز: {amount} {cur}.",
                 "es": "{name} hoy: {amount_p}.", "ar": "{name} اليوم: {amount_p}."},
    "receivables_parties": {"en": "By the books, {who} {verb} us {total_p} in all; the most: {top}.",
                            "fa": "طبق دفاتر، طلب ما از {who} روی هم {total} {cur} است؛ بیشترین: {top}.",
                            "es": "Según los libros, {who} nos {verb} {total_p} en total; los que más: {top}.",
                            "ar": "حسب الدفاتر، {who} مدينون لنا بمجموع {total_p}؛ أكثرهم: {top}."},
    "payables_parties": {"en": "By the books, we owe {who} {total_p} in all; the most: {top}.",
                         "fa": "طبق دفاتر، روی هم {total} {cur} به {who} بدهکاریم؛ بیشترین: {top}.",
                         "es": "Según los libros, debemos {total_p} a {who} en total; a quienes más: {top}.",
                         "ar": "حسب الدفاتر، ندين لـ{who} بمجموع {total_p}؛ أكثرهم: {top}."},
    "payables_liabilities": {"en": "No supplier or party is owed anything in the books; the balance sheet's liabilities come to {total_p}: {top}.",
                             "fa": "به هیچ تأمین‌کننده یا طرف حسابی در دفاتر بدهکار نیستیم؛ بدهی‌های ترازنامه روی هم {total} {cur} است: {top}.",
                             "es": "No debemos nada a proveedores ni terceros en los libros; los pasivos del balance suman {total_p}: {top}.",
                             "ar": "لا ندين لأي مورد أو طرف في الدفاتر؛ مطلوبات الميزانية مجموعها {total_p}: {top}."},
    "receivables_nobody": {"en": "Nobody owes us anything: no open sales invoice and no debtor in the books.",
                           "fa": "هیچ‌کس به ما بدهکار نیست: نه فاکتور فروش بازی هست و نه بدهکاری در دفاتر.",
                           "es": "Nadie nos debe nada: ninguna factura de venta abierta y ningún deudor en los libros.",
                           "ar": "لا أحد مدين لنا: لا فاتورة مبيعات مفتوحة ولا مدين في الدفاتر."},
    "payables_nobody": {"en": "We owe nobody anything: no open supplier bill and no creditor in the books.",
                        "fa": "به کسی بدهکار نیستیم: نه فاکتور خرید بازی هست و نه بستانکاری در دفاتر.",
                        "es": "No debemos nada a nadie: ninguna factura de proveedor abierta y ningún acreedor en los libros.",
                        "ar": "لا ندين لأحد: لا فاتورة مورد مفتوحة ولا دائن في الدفاتر."},
    # a person's books: money between them and other people, person by person (scenario AG4)
    "people_payables": {"en": "You owe {list}.", "fa": "بدهی‌های شما: {list}.", "es": "Debes: {list}.",
                        "ar": "ديونك: {list}."},
    "people_receivables": {"en": "Owed to you: {list}.", "fa": "طلب‌های شما: {list}.", "es": "Te deben: {list}.",
                           "ar": "مستحق لك: {list}."},
    "people_payables_nobody": {"en": "You owe nobody anything.", "fa": "به کسی بدهکار نیستید.",
                               "es": "No le debes nada a nadie.", "ar": "لا تدين لأحد بشيء."},
    "people_receivables_nobody": {"en": "Nobody owes you anything.", "fa": "کسی به شما بدهکار نیست.",
                                  "es": "Nadie te debe nada.", "ar": "لا أحد يدين لك بشيء."},
    "people_loose": {"en": " And {amounts} that names no one.", "fa": " و {amounts} که به نام کسی نیست.",
                     "es": " Y {amounts} sin nombre de nadie.", "ar": " و{amounts} لا يحمل اسم أحد."},
    "budget_none": {"en": "No budgets are set for this month.", "fa": "برای این ماه بودجه‌ای تعیین نشده است.",
                    "es": "No hay presupuestos para este mes.", "ar": "لا توجد ميزانيات لهذا الشهر."},
}


def _say(key: str, lang: str, **kw) -> str:
    table = _T[key]
    return (table.get(lang) or table["en"]).format(**kw)


# A bank named in a cash question: «موجودی بانک ملت», "Barclays bank balance".
# The words that follow «بانک» in a question about all of them aren't names.
_BANK_AFTER = re.compile(r"(?:بانک|\bbank)\s+([^\s؟?!.,،]+)", re.IGNORECASE)
_BANK_BEFORE = re.compile(r"([^\s؟?!.,،]+)\s+bank\b", re.IGNORECASE)
_NOT_A_NAME = {"چقدره", "چقدر", "چه", "چند", "الان", "امروز", "داریم", "دارم", "ما", "مون", "ها", "های", "هامون",
               "چیه", "است", "هست", "رو", "را", "balance", "balances", "account", "accounts", "is", "the", "our", "my",
               "what", "whats", "what's", "how", "in", "at"}


def _named_bank(message: str) -> str | None:
    """The bank a cash question names, folded, or None for "all the money"."""
    from app.utils.text import fold_fa
    text = fold_fa(message or "")
    for pattern in (_BANK_AFTER, _BANK_BEFORE):
        for m in pattern.finditer(text):
            word = m.group(1).strip().lower()
            if word and word not in _NOT_A_NAME:
                return word
    return None


def _liabilities(db) -> tuple[Any, list[tuple[str, str, Any]]]:
    """(the balance sheet's liabilities total, the accounts that owe something,
    largest first as (code, name, balance)), at base value. The total nets a
    liability account in debit, as the balance sheet does: adding up only the
    ones that owe gave 23,672,063,635 beside its 23,495,300,037 (AI QA, 2026-10-12)."""
    from datetime import date as _d
    from sqlalchemy import func, select as _select
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from app.services.reporting.common import LIABILITY, classify_account_code
    rows = db.execute(
        _select(Account.code, Account.name,
                func.coalesce(func.sum(TransactionLine.base_credit), 0) - func.coalesce(func.sum(TransactionLine.base_debit), 0))
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(Transaction.deleted_at.is_(None), Transaction.date <= _d.today())
        .group_by(Account.code, Account.name)).all()
    liab = [(c, n, plain(b)) for c, n, b in rows if classify_account_code(c or "") == LIABILITY and b]
    total = plain(sum((b for _c, _n, b in liab), 0))
    return total, sorted((r for r in liab if r[2] > 0), key=lambda r: -r[2])


def _who(n: int, lang: str) -> str:
    if lang == "fa":
        return f"{_money(n, lang)} طرف حساب"
    if lang == "es":
        return f"{n} tercero" if n == 1 else f"{n} terceros"
    if lang == "ar":
        return f"{n} طرف" if n == 1 else f"{n} أطراف"
    return f"{n} party" if n == 1 else f"{n} parties"


async def answer(db, message: str, *, user_id: str, username: str | None, lang: str,
                 mode: str = "default") -> FastAnswer | None:
    """Run the matching read tool and say the answer in one sentence."""
    intent = match(message)
    if intent is None:
        return None
    ctx = ToolContext(db=db, user_id=user_id, username=username, user_message=message, mode=mode)

    async def run(tool, args) -> dict[str, Any]:
        result = await tool.run(ctx, args)
        # one id per answer: the phone keeps one card per block id, so the same question asked
        # twice in a thread lost its second table (phone QA, 2026-10-12)
        calls.append({"tool_use_id": f"fast-{intent}-{uuid.uuid4().hex[:8]}", "name": tool.name,
                      "input": args.model_dump(mode="json"),
                      "result": result})
        return result

    calls: list[dict[str, Any]] = []
    if intent == "categories":
        if mode != "personal":
            return None                                   # a company's chart is the Chart of accounts page
        from app.services.ai_accountant.personal_glance import categories
        from app.services.reporting.common import EXPENSE, REVENUE
        cats = categories(db)                             # the same list the assistant's instructions carry
        sep = "، " if lang in ("fa", "ar") else ", "
        text = _say("categories", lang, spend=sep.join(a.name for a in cats[EXPENSE]),
                    income=sep.join(a.name for a in cats[REVENUE]))
        return FastAnswer(intent=intent, text=text, tool_calls=calls)
    if intent == "cash":
        from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput
        named = _named_bank(message)
        if named:
            # «موجودی بانک ملت» is one bank's balance, not all the money (AI QA, 2026-10-12)
            from app.utils.text import fold_fa
            r = await GetCashPosition().run(ctx, GetCashPositionInput())
            hits = [a for a in r["accounts"] if named in fold_fa(a.get("account_name") or "").lower().split()]
            if len(hits) > 1:
                # a petty-cash account named after the bank («تنخواه گردان حساب … بانک ملت») is not the bank
                from sqlalchemy import select as _select
                from app.models.entity import Entity
                banks = {(c or "").strip() for (c,) in db.execute(_select(Entity.code).where(Entity.type == "bank")).all()}
                hits = [a for a in hits if a["account_code"] in banks] or hits
            if len(hits) != 1:
                return None                 # a bank the books don't have, or several: the model asks or says so
            from app.services.ai_accountant.read_tools import GetAccountBalance, GetAccountBalanceInput
            b = await run(GetAccountBalance(), GetAccountBalanceInput(account_code=hits[0]["account_code"]))
            name = b.get("account_name") or hits[0]["account_name"]
            text = _say("bank_one", lang, name=name, amount=_money(b["balance"], lang), cur=_cur(b.get("currency"), lang),
                        amount_p=_priced(b["balance"], b.get("currency"), lang))
            return FastAnswer(intent=intent, text=text, tool_calls=calls)
        r = await run(GetCashPosition(), GetCashPositionInput())
        held = r.get("by_currency") or []
        if len(held) > 1 or (len(held) == 1 and held[0]["currency"] != r.get("currency")):
            # each currency on its own, nothing exchanged (scenario AB5)
            sep = "، " if lang in ("fa", "ar") else " · "
            text = _say("cash_currencies", lang, list=sep.join(_in_currency(h["total"], h["currency"], lang) for h in held))
        else:
            text = _say("cash", lang, total=_money(r["total"], lang), cur=_cur(r.get("currency"), lang),
                        total_p=_priced(r["total"], r.get("currency"), lang))
    elif intent in ("receivables", "payables") and mode == "personal":
        # a person owes people, not suppliers: «who do I owe?» read the invoices and said
        # nobody, with Amir owed 3,555.06 USD (scenario AG4)
        from app.services.ai_accountant.personal_tools import GetPeopleBalances, GetPeopleBalancesInput
        r = await run(GetPeopleBalances(), GetPeopleBalancesInput())
        side = "owes_you" if intent == "receivables" else "you_owe"
        people = [x for x in r["people"] if x[side]]
        from sqlalchemy import select as _select
        from app.models.entity import Entity
        from app.services.ai_accountant.orchestrator import _named_in
        named = [e.name for e in db.execute(_select(Entity).where(Entity.type != "bank")).scalars()
                 if _named_in(message, e.name)]
        if named:
            # «how much do I owe Amir?»: Amir alone; someone with nothing on this side, the model says so
            people = [x for x in people if any(_named_in(x["person"], n) for n in named)]
            if not people:
                return None
        sep = "، " if lang in ("fa", "ar") else ", "

        def owed(book: dict) -> str:
            return " + ".join(_in_currency(v, c, lang) for c, v in book.items())
        def who(name: str) -> str:                 # a Latin name kept whole in a right-to-left sentence
            return f"\u2068{name}\u2069" if lang in ("fa", "ar") else name
        text = (_say(f"people_{intent}", lang, list=sep.join(f"{who(x['person'])} {owed(x[side])}" for x in people))
                if people else _say(f"people_{intent}_nobody", lang))
        loose = (r.get("not_tied_to_anyone") or {}).get(side) or {}
        if loose and not named:
            text += _say("people_loose", lang, amounts=owed(loose))
    elif intent in ("receivables", "payables"):
        from app.services.ai_accountant.invoice_tools import ListInvoices, ListInvoicesInput
        kind = "sales" if intent == "receivables" else "purchase"
        r = await run(ListInvoices(), ListInvoicesInput(kind=kind, open_only=True, limit=25))
        totals = r.get("totals_by_currency") or {}
        # What the ledger says each party owes or is owed: a migrated company has
        # balances and no invoices, and was told nobody owed it anything (AI QA, 2026-10-12)
        from app.services.ai_accountant.invoice_tools import GetPartyBalances, GetPartyBalancesInput
        side = "debtors" if intent == "receivables" else "creditors"
        p = await run(GetPartyBalances(), GetPartyBalancesInput(side=side, limit=10))
        parts_text = []
        if r.get("count"):
            # per currency, never added together
            parts = [_priced(plain(t["balance_due"] or 0), c, lang)
                     for c, t in sorted(totals.items(), key=lambda kv: -plain(kv[1].get("balance_due") or 0))]
            key = f"{intent}_one" if r["count"] == 1 else intent       # «1 sales invoice is», not «1 … are»
            parts_text.append(_say(key, lang, n=_money(r["count"], lang), amounts=" + ".join(parts)))
        base_invoiced = plain((totals.get(p.get("currency")) or {}).get("balance_due") or 0)
        if p.get(f"{side}_count") and p[f"{side}_total"] > base_invoiced:     # more is owed than the invoices show
            sep = "، " if lang in ("fa", "ar") else ", "
            one = p[f"{side}_count"] == 1
            verb = {"en": "owes" if one else "owe", "es": "debe" if one else "deben"}.get(lang, "")
            top = sep.join(f"{x['party']} ({_priced(x['balance'], p.get('currency'), lang)})" for x in p[side][:3])
            parts_text.append(_say(f"{intent}_parties", lang, who=_who(p[f"{side}_count"], lang),
                                   total=_money(p[f"{side}_total"], lang), cur=_cur(p.get("currency"), lang),
                                   total_p=_priced(p[f"{side}_total"], p.get("currency"), lang), top=top, verb=verb))
        if not parts_text and intent == "payables":
            # a school owes its bank loan and its sister school, with no party on the books:
            # «به کی بدهکاریم؟» said "nobody" (AI QA on migrated books, 2026-10-12)
            total, owed = _liabilities(db)
            if owed and total > 0:
                sep = "، " if lang in ("fa", "ar") else ", "
                cur = p.get("currency")
                top = sep.join(f"{name} ({_priced(bal, cur, lang)})" for _c, name, bal in owed[:3])
                parts_text.append(_say("payables_liabilities", lang, total=_money(total, lang), cur=_cur(cur, lang),
                                       total_p=_priced(total, cur, lang), top=top))
        text = " ".join(parts_text) if parts_text else _say(f"{intent}_nobody", lang)
    elif intent == "spending":
        from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
        r = await run(GetSpendingSummary(), GetSpendingSummaryInput(period="this_month"))
        held = r.get("by_currency") or []
        if held or (r.get("currency") or "IRR").upper() not in ("IRR", *_SYMBOLS):
            # each currency in its own amounts: Omani rials with no rate read «۰ ریال» (scenario AO4)
            sep = "، " if lang in ("fa", "ar") else " · "
            amounts = [(h["total"], h["currency"]) for h in held] or [(r.get("total") or 0, r.get("currency"))]
            text = _say("spending_currencies" if len(amounts) > 1 else "spending_in", lang,
                        list=sep.join(_in_currency(n, c, lang) for n, c in amounts))
        else:
            text = _say("spending", lang, total=_money(r.get("total") or 0, lang), cur=_cur(r.get("currency"), lang),
                        total_p=_priced(r.get("total") or 0, r.get("currency"), lang))
    elif intent == "net_worth":
        if mode != "personal":
            return None                     # a company's worth is its equity: the model answers that
        from app.services.ai_accountant.personal_tools import GetNetWorth, GetNetWorthInput
        r = await run(GetNetWorth(), GetNetWorthInput())
        cur, code = _cur(r.get("currency"), lang), r.get("currency")
        every = r.get("by_currency") or []
        if len(every) > 1:
            # each currency on its own, nothing exchanged (docs/PLAN_CURRENCIES.md phase 3)
            sep = "، " if lang in ("fa", "ar") else " · "
            text = _say("net_worth_currencies", lang,
                        list=sep.join(_in_currency(x["net_worth"], x["currency"], lang) for x in every))
            return FastAnswer(intent=intent, text=text, tool_calls=calls)
        text = _say("net_worth", lang, net=_money(r["net_worth"], lang), cur=cur, assets=_money(r["total_assets"], lang),
                    owed=_money(r["total_liabilities"], lang), net_p=_priced(r["net_worth"], code, lang),
                    assets_p=_priced(r["total_assets"], code, lang), owed_p=_priced(r["total_liabilities"], code, lang))
        if r.get("unrealized_gain"):
            text += _say("net_worth_gain", lang, gain=_money(r["unrealized_gain"], lang),
                         gain_p=_priced(r["unrealized_gain"], code, lang))
    elif intent == "forecast":
        from app.services.ai_accountant.blocks import display_date
        from app.services.ai_accountant.cash_tools import GetCashForecast, GetCashForecastInput
        from app.services.locale_service import get_display_calendar
        r = await run(GetCashForecast(), GetCashForecastInput())
        calendar = get_display_calendar(db)
        day = lambda iso: display_date(iso, calendar, lang) or iso  # noqa: E731
        cur, code = _cur(r.get("currency"), lang), r.get("currency")
        low = r.get("lowest") or {}
        text = _say("forecast", lang, weeks=_money(len(r.get("weeks") or []), lang), opening=_money(r["opening_cash"], lang),
                    closing=_money(r.get("closing_cash") or 0, lang), cur=cur, lowest=_money(low.get("closing") or 0, lang),
                    low_week=day(low.get("week_start")), opening_p=_priced(r["opening_cash"], code, lang),
                    closing_p=_priced(r.get("closing_cash") or 0, code, lang), lowest_p=_priced(low.get("closing") or 0, code, lang))
        if r.get("first_negative_week") == low.get("week_start") and text.endswith("."):
            text = text[:-1] + _say("forecast_negative_same", lang)
        elif r.get("first_negative_week"):
            text += _say("forecast_negative", lang, week=day(r["first_negative_week"]))
    else:  # budget
        from app.services.ai_accountant.period_tools import GetBudgetStatus, GetBudgetStatusInput
        r = await run(GetBudgetStatus(), GetBudgetStatusInput())
        if not r.get("budgets"):
            text = _say("budget_none", lang)
        else:
            cur, code = _cur(r.get("currency"), lang), r.get("currency")
            left = r.get("total_left") or 0
            text = _say("budget" if left >= 0 else "budget_over", lang, actual=_money(r.get("total_actual") or 0, lang),
                        budget=_money(r.get("total_budget") or 0, lang), left=_money(left, lang), cur=cur,
                        over=_money(-left, lang), actual_p=_priced(r.get("total_actual") or 0, code, lang),
                        budget_p=_priced(r.get("total_budget") or 0, code, lang),
                        left_p=_priced(left, code, lang), over_p=_priced(-left, code, lang))
    return FastAnswer(intent=intent, text=text, tool_calls=calls)

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
from dataclasses import dataclass, field
from typing import Any

from app.services.ai_accountant.base import ToolContext

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

# Payables before receivables: «به کی بدهکاریم؟» (who do we owe) contains «کی بدهکار».
_INTENTS: list[tuple[str, re.Pattern]] = [
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
        r"|بودجه|presupuesto|الميزانية",
        re.IGNORECASE)),
    ("spending", re.compile(
        r"\bhow much (did|have) (i|we) spen[dt]\b|\bspending this month\b|\bwhat did (i|we) spend\b"
        r"|خرج(ِ)? این ماه|این ماه چقدر خرج|چقدر خرج کرد(م|یم)|هزینه[‌ ]?(ها|های)? این ماه"
        r"|cu[aá]nto (gastamos|gast[eé]|hemos gastado) este mes|gastos de este mes|كم (أنفقنا|أنفقت|صرفنا|صرفت) هذا الشهر|مصاريف هذا الشهر",
        re.IGNORECASE)),
    ("cash", re.compile(
        r"\bhow much (cash|money) (do|have) (we|i)\b|\bcash (balance|position|on hand)\b|\bbank balance\b"
        r"|\bwhat'?s in the bank\b|\bhow much is in the bank\b"
        r"|موجودی (نقد|بانک|حساب|حساب[‌ ]?ها|کل)?|نقدینگی|چقدر پول (داریم|دارم|مونده)|پول نقد"
        r"|cu[aá]nto dinero (tenemos|tengo|hay)|saldo (de caja|del banco|en el banco)|efectivo disponible"
        r"|كم (لدينا|عندنا) من (المال|النقود)|الرصيد النقدي|رصيد (البنك|الصندوق)",
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
    if not text or len(text) > MAX_LENGTH or _DIGITS.search(text) or _ACTIONS.search(text):
        return None
    if _FORECAST.search(text):
        return None if _WHAT_IF.search(text) else "forecast"
    if _FUTURE.search(text):
        return None
    for name, pattern in _INTENTS:
        if pattern.search(text):
            return name
    return None


@dataclass
class FastAnswer:
    intent: str
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _money(n: int, lang: str) -> str:
    s = f"{int(n):,}"
    if lang == "fa":
        from app.utils.jalali import to_persian_digits
        return to_persian_digits(s).replace(",", "٬")
    return s


def _cur(code: str | None, lang: str) -> str:
    if (code or "").upper() == "IRR" and lang == "fa":
        return "ریال"
    return code or ""


_T = {
    "cash": {"en": "Cash and bank today: {total} {cur}.", "fa": "موجودی نقد و بانک امروز: {total} {cur}.",
             "es": "Efectivo y bancos hoy: {total} {cur}.", "ar": "النقد والبنوك اليوم: {total} {cur}."},
    "receivables": {"en": "{n} sales invoices are still open: {amounts} to collect.",
                    "fa": "{n} فاکتور فروش هنوز باز است: {amounts} طلب.",
                    "es": "{n} facturas de venta siguen abiertas: {amounts} por cobrar.",
                    "ar": "{n} فواتير مبيعات ما زالت مفتوحة: {amounts} للتحصيل."},
    "receivables_none": {"en": "No sales invoice is waiting to be paid.", "fa": "هیچ فاکتور فروشی منتظر پرداخت نیست.",
                         "es": "Ninguna factura de venta espera pago.", "ar": "لا توجد فاتورة مبيعات بانتظار الدفع."},
    "payables": {"en": "{n} supplier bills are still open: {amounts} to pay.",
                 "fa": "{n} فاکتور خرید هنوز باز است: {amounts} بدهی.",
                 "es": "{n} facturas de proveedores siguen abiertas: {amounts} por pagar.",
                 "ar": "{n} فواتير موردين ما زالت مفتوحة: {amounts} للدفع."},
    "payables_none": {"en": "No supplier bill is waiting to be paid.", "fa": "هیچ فاکتور خریدی منتظر پرداخت نیست.",
                      "es": "Ninguna factura de proveedor espera pago.", "ar": "لا توجد فاتورة مورد بانتظار الدفع."},
    "spending": {"en": "This month's spending so far: {total} {cur}.", "fa": "خرج این ماه تا امروز: {total} {cur}.",
                 "es": "Gasto de este mes hasta hoy: {total} {cur}.", "ar": "إنفاق هذا الشهر حتى اليوم: {total} {cur}."},
    "budget": {"en": "This month: {actual} of {budget} {cur} spent, {left} left.",
               "fa": "این ماه: {actual} از {budget} {cur} خرج شده، {left} مانده.",
               "es": "Este mes: {actual} de {budget} {cur} gastados, quedan {left}.",
               "ar": "هذا الشهر: أُنفق {actual} من {budget} {cur}، وتبقّى {left}."},
    "forecast": {"en": "Cash over the next {weeks} weeks, an estimate: from {opening} to {closing} {cur}; the lowest, {lowest}, in the week of {low_week}.",
                 "fa": "نقدینگی {weeks} هفتهٔ آینده، به تخمین: از {opening} به {closing} {cur}؛ کمترین، {lowest}، در هفتهٔ {low_week}.",
                 "es": "Caja en las próximas {weeks} semanas, una estimación: de {opening} a {closing} {cur}; el mínimo, {lowest}, la semana del {low_week}.",
                 "ar": "النقد في الأسابيع الـ{weeks} القادمة، تقديرًا: من {opening} إلى {closing} {cur}؛ الأدنى، {lowest}، في أسبوع {low_week}."},
    "forecast_negative": {"en": " It goes below zero in the week of {week}.", "fa": " در هفتهٔ {week} منفی می‌شود.",
                          "es": " Baja de cero la semana del {week}.", "ar": " ينخفض تحت الصفر في أسبوع {week}."},
    "budget_none": {"en": "No budgets are set for this month.", "fa": "برای این ماه بودجه‌ای تعیین نشده است.",
                    "es": "No hay presupuestos para este mes.", "ar": "لا توجد ميزانيات لهذا الشهر."},
}


def _say(key: str, lang: str, **kw) -> str:
    table = _T[key]
    return (table.get(lang) or table["en"]).format(**kw)


async def answer(db, message: str, *, user_id: str, username: str | None, lang: str,
                 mode: str = "default") -> FastAnswer | None:
    """Run the matching read tool and say the answer in one sentence."""
    intent = match(message)
    if intent is None:
        return None
    ctx = ToolContext(db=db, user_id=user_id, username=username, user_message=message, mode=mode)

    async def run(tool, args) -> dict[str, Any]:
        result = await tool.run(ctx, args)
        calls.append({"tool_use_id": f"fast-{intent}", "name": tool.name, "input": args.model_dump(mode="json"),
                      "result": result})
        return result

    calls: list[dict[str, Any]] = []
    if intent == "cash":
        from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput
        r = await run(GetCashPosition(), GetCashPositionInput())
        text = _say("cash", lang, total=_money(r["total"], lang), cur=_cur(r.get("currency"), lang))
    elif intent in ("receivables", "payables"):
        from app.services.ai_accountant.invoice_tools import ListInvoices, ListInvoicesInput
        kind = "sales" if intent == "receivables" else "purchase"
        r = await run(ListInvoices(), ListInvoicesInput(kind=kind, open_only=True, limit=25))
        totals = r.get("totals_by_currency") or {}
        if not r.get("count"):
            text = _say(f"{intent}_none", lang)
        else:
            # per currency, never added together
            parts = [f"{_money(t['balance_due'], lang)} {_cur(c, lang)}".strip()
                     for c, t in sorted(totals.items(), key=lambda kv: -int(kv[1].get("balance_due") or 0))]
            text = _say(intent, lang, n=_money(r["count"], lang), amounts=" + ".join(parts))
    elif intent == "spending":
        from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
        r = await run(GetSpendingSummary(), GetSpendingSummaryInput(period="this_month"))
        text = _say("spending", lang, total=_money(r.get("total") or 0, lang), cur=_cur(r.get("currency"), lang))
    elif intent == "forecast":
        from app.services.ai_accountant.blocks import display_date
        from app.services.ai_accountant.cash_tools import GetCashForecast, GetCashForecastInput
        from app.services.locale_service import get_display_calendar
        r = await run(GetCashForecast(), GetCashForecastInput())
        calendar = get_display_calendar(db)
        day = lambda iso: display_date(iso, calendar, lang) or iso  # noqa: E731
        cur = _cur(r.get("currency"), lang)
        low = r.get("lowest") or {}
        text = _say("forecast", lang, weeks=_money(len(r.get("weeks") or []), lang), opening=_money(r["opening_cash"], lang),
                    closing=_money(r.get("closing_cash") or 0, lang), cur=cur, lowest=_money(low.get("closing") or 0, lang),
                    low_week=day(low.get("week_start")))
        if r.get("first_negative_week"):
            text += _say("forecast_negative", lang, week=day(r["first_negative_week"]))
    else:  # budget
        from app.services.ai_accountant.period_tools import GetBudgetStatus, GetBudgetStatusInput
        r = await run(GetBudgetStatus(), GetBudgetStatusInput())
        if not r.get("budgets"):
            text = _say("budget_none", lang)
        else:
            cur = _cur(r.get("currency"), lang)
            text = _say("budget", lang, actual=_money(r.get("total_actual") or 0, lang),
                        budget=_money(r.get("total_budget") or 0, lang), left=_money(r.get("total_left") or 0, lang), cur=cur)
    return FastAnswer(intent=intent, text=text, tool_calls=calls)

"""Persian sentences the server fills in read like Persian: the values put into
them came in Latin digits, ISO dates and currency codes — «هر بار حداکثر 50
پیامک», «12 روز از سررسید گذشته (1405/07/15)», «موجودی نقد: 1,250,000,000 IRR»,
«سود −۵۰,۰۰۰ ریال» (Persian copy review, 2026-10-31). English, Spanish and
Arabic read as before."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

LRI, PDI, FSI = "⁦", "⁩", "⁨"


# --- API messages (app/core/messages.py) ------------------------------------------------------------------

def test_a_messages_figures_and_dates_are_persian_its_codes_as_typed():
    from app.core.messages import localize_detail
    assert localize_detail("At most 50 messages at a time", "fa") == f"هر بار حداکثر {FSI}۵۰{PDI} پیامک پذیرفته می‌شود."
    petty = localize_detail("Expense 2,500,000 exceeds the petty cash available (1,000,000). Top up the float first.", "fa")
    assert f"{FSI}۲,۵۰۰,۰۰۰{PDI}" in petty and f"{FSI}۱,۰۰۰,۰۰۰{PDI}" in petty
    assert localize_detail("Low gross margin at 12.5%.", "fa") == f"حاشیه سود ناخالص پایین: {FSI}۱۲٫۵{PDI}٪."
    # an account code, an id and a document number keep the digits they were typed in
    assert localize_detail("Account not found: 6112", "fa") == f"حساب پیدا نشد: {FSI}۶۱۱۲{PDI}"   # BJ2
    assert "S-7" in localize_detail("Sales invoice number 'S-7' already exists (id 12).", "fa")
    # a date in Persian digits in either calendar
    closed = "Period is closed through 2026-06-30; cannot post or back-date an entry dated 2026-05-01. Reopen the period or use a later date."
    assert localize_detail(closed, "fa").startswith(f"دوره تا {FSI}۲۰۲۶-۰۶-۳۰{PDI} بسته است")
    assert localize_detail(closed, "fa", jalali=True).startswith(f"دوره تا {FSI}۱۴۰۵/۰۴/۰۹{PDI} بسته است")
    # the other languages as before
    assert localize_detail("At most 50 messages at a time", "ar") == f"{FSI}50{PDI} رسالة على الأكثر في كل مرة."
    assert localize_detail("At most 50 messages at a time", "es") == "Como máximo 50 mensajes a la vez."


# --- the bell and the phone's feed (app/services/notification_text.py) ------------------------------------

def test_the_bell_says_its_figures_dates_and_currency_in_persian():
    from app.services.notification_text import render
    overdue = {"number": "INV-12", "side": "receivable", "days": 12, "date": "2026-10-07",
               "amount": "1,250,000", "currency": "IRR"}
    title, message = render("invoice_overdue_part", overdue, "fa", "jalali")
    assert title == f"فاکتور {FSI}INV-12{PDI} سررسید گذشته"                       # the number as typed
    assert message == (f"{FSI}طلب از مشتری{PDI} — {FSI}۱۲{PDI} روز از سررسید گذشته ({FSI}۱۴۰۵/۰۷/۱۵{PDI})؛ "
                       f"مانده {FSI}۱,۲۵۰,۰۰۰{PDI} {FSI}ریال{PDI}")
    assert render("invoice_overdue_part", {**overdue, "currency": "USD"}, "fa")[1].endswith(f"{FSI}دلار آمریکا{PDI}")
    assert render("apikey_expiring", {"label": "CI", "days": 3, "prefix": "ak_12", "date": "2026-11-01"},
                  "fa")[0] == f"کلید API «{FSI}CI{PDI}» تا {FSI}۳{PDI} روز دیگر منقضی می‌شود"
    rent = {"noun": "installment", "seq": " 2/6", "title": "اجاره", "amount": "1,250,000", "verb": "pay",
            "when": {"key": "commitment_due", "params": {"date": "2026-10-12"}}}
    title, message = render("commitment", rent, "fa", "jalali")
    assert title == f"{FSI}قسط{PDI} ۲ از ۶: {FSI}اجاره{PDI}"
    assert message == f"{FSI}۱,۲۵۰,۰۰۰{PDI} {FSI}پرداختنی{PDI} — {FSI}سررسید ۱۴۰۵/۰۷/۲۰{PDI}"
    budget = {"category": "خوراک", "pct": 120, "actual": "1,200 USD", "limit": "1,000 USD", "month": "1405-07", "spent": "120"}
    assert render("budget_over", budget, "fa", "jalali")[1] == \
        f"{FSI}۱,۲۰۰ دلار آمریکا{PDI} از {FSI}۱,۰۰۰ دلار آمریکا{PDI} در {FSI}مهر ۱۴۰۵{PDI} خرج شده ({FSI}۱۲۰{PDI}٪)"
    project = {"name": "Site", "parts": [{"key": "project_hours", "params": {"used": "90", "budget": "100", "pct": 90}},
                                         {"key": "project_amount", "params": {"used": "9,000", "budget": "10,000",
                                                                              "currency": "GBP", "pct": 90}}]}
    assert render("project_near", project, "fa")[1] == f"{FSI}۹۰ از ۱۰۰ ساعت (۹۰٪)، ۹,۰۰۰ از ۱۰,۰۰۰ پوند انگلیس (۹۰٪){PDI}"
    # English as it was
    assert render("commitment", rent, "en", "jalali") == ("Installment 2/6: اجاره", "1,250,000 to pay — due 1405/07/20")
    assert render("project_near", project, "en")[1] == "90 of 100 hours (90%) · 9,000 of 10,000 GBP (90%)"


# --- insights: the dashboard's panel and the bell ---------------------------------------------------------

def test_an_insight_on_the_dashboard_is_worded_as_the_chat_words_it(db, monkeypatch):
    from app.services import insight_service as svc
    from app.services.insight_service import Insight, insights_payload
    from app.services.notification_text import render
    drop = Insight(key="t", kind="revenue_drop", severity="warning", page="dashboard",
                   params={"pct": "35", "month": "1405-07", "current": "650,000", "average": "1,000,000"})
    monkeypatch.setattr(svc, "all_detectors", lambda: (("one", lambda db_, today: [drop]),))
    svc.invalidate_insights_cache()
    try:
        title = insights_payload(db, "fa")["insights"][0]["title"]
        assert "مهر ۱۴۰۵" in title and "۳۵٪" in title and "1405-07" not in title and "35" not in title
        # the assistant's get_insights reads the books' own keys and figures
        assert "1405-07" in insights_payload(db, "fa", worded=False)["insights"][0]["title"]
    finally:
        svc.invalidate_insights_cache()
    bell = render("insight:revenue_drop", drop.params, "fa")[0]
    assert "مهر ۱۴۰۵" in bell and "۳۵٪" in bell


def test_names_in_an_insight_are_joined_as_the_language_writes_a_list():
    from app.services.insight_service import Insight
    names = ["کاغذ A4", "خودکار", "دفتر…"]
    low = Insight(key="k", kind="low_stock", severity="info", page="inventory", params={"count": 5, "names": names})
    assert low.localize("fa")["message"].endswith("کاغذ A4، خودکار، دفتر….")
    assert "A4, " not in low.localize("ar")["message"]
    assert low.localize("en")["message"].endswith("A4, خودکار, دفتر….")
    hire = Insight(key="n", kind="new_employee", severity="info", page="payroll",
                   params={"names": ["علی", "سارا"], "delta": "1,000"})
    assert "علی، سارا" in hire.localize("fa")["title"] + hire.localize("fa")["message"]
    # a row stored before the list keeps its text
    assert "علی, سارا" in str(Insight(key="n", kind="new_employee", severity="info", page="payroll",
                                      params={"names": "علی, سارا", "delta": "1,000"}).localize("fa"))


# --- the daily digest -------------------------------------------------------------------------------------

def test_the_persian_digest_writes_persian_figures_and_names_the_rial():
    from app.services.digest_service import format_digest
    d = {"currency": "IRR", "cash_on_hand": 1_250_000_000, "runway_months": 4.2, "ar_outstanding": 380_000_000,
         "ap_outstanding": 0, "ar_overdue": 120_000_000, "ap_overdue": 0, "low_cash": False, "low_cash_reasons": []}
    fa = format_digest("آکمه", d, "fa").split("\n")
    assert fa[1:] == ["موجودی نقد: ۱,۲۵۰,۰۰۰,۰۰۰ ریال", "دوام نقدینگی: ۴٫۲ ماه",
                      "مطالبات باز: ۳۸۰,۰۰۰,۰۰۰ ریال (سررسیدگذشته ۱۲۰,۰۰۰,۰۰۰)", "بدهی‌های باز: ۰ ریال (سررسیدگذشته ۰)"]
    assert "موجودی نقد: ۵۰۰ ریال عمان" in format_digest("آکمه", {**d, "currency": "OMR", "cash_on_hand": 500}, "fa")
    assert "Cash on hand: 1,250,000,000 IRR" in format_digest("Acme", d, "en")


# --- the self-audit's findings ----------------------------------------------------------------------------

def test_an_audit_finding_reads_persian_figures_and_the_companys_dates():
    from app.services.audit_service import AuditFinding
    dup = AuditFinding(severity="warning", category="duplicate", title="t", detail="d", key="duplicate_payment",
                       params={"a": "3fa2b1c4", "b": "9e01d2aa", "date": "2026-10-07", "amount": "1,250,000",
                               "similarity": "85%"})
    _, detail = dup.localized("fa", "jalali")
    assert detail == (f"اسناد {FSI}3fa2b1c4{PDI} و {FSI}9e01d2aa{PDI} در تاریخ {FSI}۱۴۰۵/۰۷/۱۵{PDI}: "
                      f"مبلغ یکسان ({FSI}۱,۲۵۰,۰۰۰{PDI})، شباهت شرح {FSI}۸۵٪{PDI}")
    assert f"{FSI}۲۰۲۶-۱۰-۰۷{PDI}" in dup.localized("fa")[1]                     # a Gregorian company's
    assert "on 1405/07/15" in dup.localized("en", "jalali")[1] and "(1,250,000)" in dup.localized("en", "jalali")[1]


# --- the assistant's answers from the books (fast_paths) --------------------------------------------------

def _answer(monkeypatch, tool_path: str, tool: str, result: dict, message: str, lang: str = "fa",
            calendar: str = "jalali") -> str:
    import importlib

    from app.services.ai_accountant import fast_paths
    from app.services import locale_service

    async def run(self, ctx, args):
        return result
    monkeypatch.setattr(getattr(importlib.import_module(tool_path), tool), "run", run)
    monkeypatch.setattr(locale_service, "get_display_calendar", lambda db: calendar)
    out = asyncio.run(fast_paths.answer(None, message, user_id="u", username=None, lang=lang))
    return out.text


def test_a_persian_answer_names_its_currency_and_keeps_a_minus_on_the_left(monkeypatch):
    cash = {"total": 1_250_500, "currency": "OMR", "by_currency": [{"currency": "OMR", "total": 1_250_500}]}
    said = _answer(monkeypatch, "app.services.ai_accountant.cash_tools", "GetCashPosition", cash, "موجودی چقدره؟")
    assert said == "موجودی نقد و بانک امروز: ۱,۲۵۰,۵۰۰ ریال عمان."
    assert _answer(monkeypatch, "app.services.ai_accountant.cash_tools", "GetCashPosition", cash,
                   "how much cash do we have?", lang="en") == "Cash and bank today: 1,250,500 OMR."
    overdrawn = {"total": -150_000, "currency": "IRR", "by_currency": [{"currency": "IRR", "total": -150_000}]}
    said = _answer(monkeypatch, "app.services.ai_accountant.cash_tools", "GetCashPosition", overdrawn, "موجودی چقدره؟")
    assert said == f"موجودی نقد و بانک امروز: {LRI}−۱۵۰,۰۰۰{PDI} ریال."


def test_a_budget_spent_past_its_limit_says_so(monkeypatch):
    over = {"budgets": [{}], "currency": "IRR", "total_actual": 450_000, "total_budget": 400_000, "total_left": -50_000}
    said = _answer(monkeypatch, "app.services.ai_accountant.period_tools", "GetBudgetStatus", over, "بودجه چقدر مونده؟")
    assert said == "این ماه: ۴۵۰,۰۰۰ از ۴۰۰,۰۰۰ ریال خرج شده، ۵۰,۰۰۰ فراتر از بودجه."
    assert _answer(monkeypatch, "app.services.ai_accountant.period_tools", "GetBudgetStatus", over,
                   "how's my budget looking?", lang="en") == \
        "This month: 450,000 IRR of 400,000 IRR spent, 50,000 IRR over budget."
    under = {**over, "total_actual": 300_000, "total_left": 100_000}
    assert _answer(monkeypatch, "app.services.ai_accountant.period_tools", "GetBudgetStatus", under,
                   "بودجه چقدر مونده؟") == "این ماه: ۳۰۰,۰۰۰ از ۴۰۰,۰۰۰ ریال خرج شده، ۱۰۰,۰۰۰ مانده."


def test_the_forecast_names_a_week_once(monkeypatch):
    weeks = [{"week_start": "2026-11-30"}] * 13
    low = {"opening_cash": 100_000, "closing_cash": 20_000, "weeks": weeks, "currency": "IRR",
           "lowest": {"week_start": "2026-11-30", "closing": -40_000}, "first_negative_week": "2026-11-30"}
    said = _answer(monkeypatch, "app.services.ai_accountant.cash_tools", "GetCashForecast", low, "پیش‌بینی نقدینگی")
    assert said == (f"پیش‌بینی نقدینگی ۱۳ هفتهٔ آینده: از ۱۰۰,۰۰۰ به ۲۰,۰۰۰ ریال؛ کمترین مقدار {LRI}−۴۰,۰۰۰{PDI} "
                    "در هفتهٔ ۹ آذر ۱۴۰۵؛ از همان هفته منفی می‌شود.")
    later = {**low, "lowest": {"week_start": "2026-12-07", "closing": -40_000}}
    said = _answer(monkeypatch, "app.services.ai_accountant.cash_tools", "GetCashForecast", later, "پیش‌بینی نقدینگی")
    assert said.endswith("در هفتهٔ ۱۶ آذر ۱۴۰۵. در هفتهٔ ۹ آذر ۱۴۰۵ منفی می‌شود.")


# --- the monthly review -----------------------------------------------------------------------------------

def _review(facts: dict) -> NS:
    return NS(kind="monthly", id="r1", facts={"calendar": "jalali", "currency": "IRR", "month": "1405-07",
                                              "previous_month": "1405-06", **facts})


def test_a_month_spent_past_its_income_says_how_much_not_a_rate_without_its_minus():
    from app.services.proactive_reviews import render
    f = {"personal": True, "income": 800_000, "spent": 900_000, "saved": -100_000, "rate": -12.5,
         "previous": {"income": 0, "spending": 0, "saved": 0}}
    fa = render(_review(f), "fa", ideas=False)
    assert fa["headline"] == "درآمد ۸۰۰,۰۰۰ ریال ایران، خرج ۹۰۰,۰۰۰ ریال ایران؛ ۱۰۰,۰۰۰ ریال ایران بیشتر از درآمد خرج شد."
    assert fa["card"]["ring"] is None                                    # no «۱۳٪ از درآمد» beside a negative saving
    assert render(_review(f), "en", ideas=False)["headline"] == \
        "Income 800,000 Iranian rial, spending 900,000 Iranian rial: 100,000 Iranian rial more than came in."
    saved = {**f, "spent": 600_000, "saved": 200_000, "rate": 25}
    assert render(_review(saved), "fa", ideas=False)["headline"].endswith("پس‌انداز ۲۰۰,۰۰۰ ریال ایران (۲۵٪ از درآمد).")


def test_a_company_month_with_a_loss_says_loss():
    from app.services.proactive_reviews import render
    f = {"personal": False, "income": 100_000, "spent": 150_000, "profit": -50_000,
         "previous": {"income": 80_000, "spending": 100_000, "profit": -20_000}}
    assert render(_review(f), "fa", ideas=False)["headline"] == \
        "درآمد ۱۰۰,۰۰۰ ریال ایران، هزینه ۱۵۰,۰۰۰ ریال ایران، زیان ۵۰,۰۰۰ ریال ایران (ماه قبل زیان ۲۰,۰۰۰ ریال ایران)."
    profit = {**f, "spent": 60_000, "profit": 40_000, "previous": {"income": 0, "spending": 0, "profit": 10_000}}
    assert render(_review(profit), "en", ideas=False)["headline"] == \
        "Revenue 100,000 Iranian rial, expenses 60,000 Iranian rial, profit 40,000 Iranian rial (last month 10,000 Iranian rial)."
    from app.services.proactive_reviews import _money
    assert _money(-5_000, "IRR", "fa") == f"{LRI}−۵,۰۰۰{PDI} ریال ایران"           # any negative amount: its minus on the left


# --- the phone's briefing ---------------------------------------------------------------------------------

@pytest.mark.parametrize("n, said", [(1, "یک سند منتظر تأیید شماست."), (3, "3 سند منتظر تأیید شماست.")])
def test_one_waiting_voucher_is_one(n, said):
    from app.api.mobile_chat import _APPROVALS_LINE
    assert _APPROVALS_LINE["fa"](n) == said                               # the caller turns «3» into «۳»

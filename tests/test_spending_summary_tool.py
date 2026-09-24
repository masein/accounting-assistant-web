"""get_spending_summary — period words resolved server-side in the company's
calendar (QA 2026-09-24 A.8-7: "این ماه چقدر خرج کردم؟" answered "nothing
recorded" right after a posting, and today was dated ۲۴ مهر instead of ۲ مهر).
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.spending_tools import (
    GetSpendingSummary,
    GetSpendingSummaryInput,
    resolve_period,
)
from app.utils.jalali import format_jalali_long

TODAY = date(2026, 9, 24)  # Thursday; Jalali 1405/07/02 (۲ مهر ۱۴۰۵)


class TestResolvePeriodJalali:
    def test_this_month_starts_on_mehr_1(self):
        p = resolve_period("this_month", TODAY, "jalali")
        assert (p.from_date, p.to_date) == (date(2026, 9, 23), TODAY)
        assert "مهر ۱۴۰۵" in p.label_fa and "۱ مهر ۱۴۰۵ تا ۲ مهر ۱۴۰۵" in p.label_fa
        assert p.label_en.startswith("Mehr 1405")
        d = p.as_dict()
        assert d["from_jalali"] == "1405/07/01" and d["to_jalali"] == "1405/07/02"

    def test_last_month_is_all_of_shahrivar(self):
        p = resolve_period("last_month", TODAY, "jalali")
        assert (p.from_date, p.to_date) == (date(2026, 8, 23), date(2026, 9, 22))
        assert "شهریور ۱۴۰۵" in p.label_fa

    def test_this_year_starts_on_farvardin_1(self):
        p = resolve_period("this_year", TODAY, "jalali")
        assert (p.from_date, p.to_date) == (date(2026, 3, 21), TODAY)
        assert "۱۴۰۵" in p.label_fa

    def test_last_year_is_the_whole_previous_jalali_year(self):
        p = resolve_period("last_year", TODAY, "jalali")
        assert (p.from_date, p.to_date) == (date(2025, 3, 21), date(2026, 3, 20))

    def test_this_week_starts_on_saturday(self):
        p = resolve_period("this_week", TODAY, "jalali")
        assert (p.from_date, p.to_date) == (date(2026, 9, 19), TODAY)

    def test_last_month_across_the_year_boundary(self):
        # 1405/01/05 → last month is Esfand 1404 (29 days: 1404/12/01 = 2026-02-20 … 2026-03-20)
        farvardin_5 = date(2026, 3, 25)
        p = resolve_period("last_month", farvardin_5, "jalali")
        assert (p.from_date, p.to_date) == (date(2026, 2, 20), date(2026, 3, 20))
        assert "اسفند ۱۴۰۴" in p.label_fa


class TestResolvePeriodGregorian:
    def test_this_and_last_month(self):
        assert (resolve_period("this_month", TODAY, "gregorian").from_date, TODAY) == (date(2026, 9, 1), TODAY)
        p = resolve_period("last_month", TODAY, "gregorian")
        assert (p.from_date, p.to_date) == (date(2026, 8, 1), date(2026, 8, 31))
        assert p.label_en.startswith("August 2026")

    def test_years_and_weeks(self):
        assert resolve_period("this_year", TODAY, "gregorian").from_date == date(2026, 1, 1)
        p = resolve_period("last_year", TODAY, "gregorian")
        assert (p.from_date, p.to_date) == (date(2025, 1, 1), date(2025, 12, 31))
        assert resolve_period("this_week", TODAY, "gregorian").from_date == date(2026, 9, 21)  # Monday
        p = resolve_period("last_week", TODAY, "gregorian")
        assert (p.from_date, p.to_date) == (date(2026, 9, 14), date(2026, 9, 20))

    def test_today_yesterday_custom(self):
        assert resolve_period("today", TODAY, "gregorian").from_date == TODAY
        assert resolve_period("yesterday", TODAY, "gregorian").to_date == date(2026, 9, 23)
        p = resolve_period("custom", TODAY, "gregorian", date(2026, 1, 1), date(2026, 1, 31))
        assert (p.from_date, p.to_date) == (date(2026, 1, 1), date(2026, 1, 31))
        with pytest.raises(ToolError):
            resolve_period("custom", TODAY, "gregorian")
        with pytest.raises(ToolError):
            resolve_period("custom", TODAY, "gregorian", date(2026, 2, 1), date(2026, 1, 1))


def test_format_jalali_long_is_day_month_year_in_persian():
    assert format_jalali_long(TODAY) == "۲ مهر ۱۴۰۵"
    assert format_jalali_long(date(2026, 3, 21)) == "۱ فروردین ۱۴۰۵"


def _run(db, **kw):
    return asyncio.run(GetSpendingSummary().run(ToolContext(db=db, user_id="u"), GetSpendingSummaryInput(**kw)))


def test_tool_counts_a_posting_made_today(db, make_transaction, monkeypatch):
    from app.services.ai_accountant import spending_tools
    monkeypatch.setattr(spending_tools, "get_display_calendar", lambda _db: "jalali")

    before = _run(db, period="this_month")
    make_transaction([("6112", 500_000, 0), ("1110", 0, 500_000)], tx_date=date.today(), description="نان")
    db.flush()
    after = _run(db, period="this_month")

    assert after["total"] - before["total"] == 500_000
    assert after["transaction_count"] - before["transaction_count"] == 1
    cat = {c["category_code"]: c["amount"] for c in after["by_category"]}
    cat0 = {c["category_code"]: c["amount"] for c in before["by_category"]}
    assert cat["6112"] - cat0.get("6112", 0) == 500_000
    assert after["kind"] == "spending" and after["currency"]
    assert after["period"]["calendar"] == "jalali"
    assert after["today_jalali_long"] == format_jalali_long(date.today())
    assert after["period"]["to"] == date.today().isoformat()
    assert "note" not in after


def test_tool_income_kind_and_category_filter(db, make_transaction, monkeypatch):
    from app.services.ai_accountant import spending_tools
    monkeypatch.setattr(spending_tools, "get_display_calendar", lambda _db: "gregorian")

    before = _run(db, period="this_month", kind="income")
    make_transaction([("1110", 300_000, 0), ("4110", 0, 300_000)], tx_date=date.today(), description="حقوق")
    make_transaction([("6110", 70_000, 0), ("1110", 0, 70_000)], tx_date=date.today(), description="salary paid")
    db.flush()
    after = _run(db, period="this_month", kind="income")
    assert after["total"] - before["total"] == 300_000  # the 6110 expense is not income

    only_6110_before = before_6110 = _run(db, period="this_month", category_code="6110")
    assert only_6110_before["total"] >= 70_000
    assert all(c["category_code"].startswith("6110") for c in before_6110["by_category"])


def test_tool_reports_empty_period_plainly(db, monkeypatch):
    from app.services.ai_accountant import spending_tools
    monkeypatch.setattr(spending_tools, "get_display_calendar", lambda _db: "gregorian")
    out = _run(db, period="custom", from_date=date(2035, 1, 1), to_date=date(2035, 1, 2))
    assert out["total"] == 0 and out["by_category"] == []
    assert "No spending recorded" in out["note"]


class TestPromptWiring:
    def test_today_line_carries_the_long_jalali_date(self, db, monkeypatch):
        from app.services import locale_service
        from app.services.ai_accountant.orchestrator import run_chat_turn
        from tests.test_ai_accountant_orchestrator import _assistant_text

        monkeypatch.setattr(locale_service, "get_display_calendar", lambda _db: "jalali")
        captured: dict = {}

        class _Cap:
            shape = "fake"

            async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
                captured["system_prompt"] = system_prompt
                captured["tools"] = tools
                return _assistant_text("سلام")

        asyncio.run(run_chat_turn(db, user_id="u1", user_message="سلام", client=_Cap(), mode="personal"))
        sp = captured["system_prompt"]
        assert format_jalali_long(date.today()) in sp
        assert "get_spending_summary" in sp
        assert "Never convert a Gregorian date to Jalali" in sp
        assert "get_spending_summary" in {t["name"] for t in captured["tools"]}

    def test_personal_turn_answers_from_the_tool(self, db, make_transaction, monkeypatch):
        from app.services.ai_accountant import spending_tools
        from app.services.ai_accountant.orchestrator import run_chat_turn
        from tests.test_ai_accountant_orchestrator import _FakeClient, _assistant_text, _assistant_tool_call

        monkeypatch.setattr(spending_tools, "get_display_calendar", lambda _db: "jalali")
        make_transaction([("6112", 500_000, 0), ("1110", 0, 500_000)], tx_date=date.today(), description="نان")
        db.flush()
        client = _FakeClient([
            _assistant_tool_call("get_spending_summary", {"period": "this_month"}),
            _assistant_text("این ماه ۵۰۰٬۰۰۰ ریال خرج کردید."),
        ])
        result = asyncio.run(run_chat_turn(
            db, user_id="u1", user_message="این ماه چقدر خرج کردم؟", client=client, mode="personal",
        ))
        assert result.proposals == [] and result.turns == 2
        assert result.tool_calls[0]["name"] == "get_spending_summary"
        # The tool result the model saw carries the resolved period + total.
        tool_msg = client.sent[1][-1]
        payload = tool_msg.to_dict()
        text = str(payload)
        assert "label_fa" in text and "total" in text and "1405/07/01" in text

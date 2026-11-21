"""A tax summary given no start date covers the current quarter in the company's calendar.

For an Iranian company that is the current season (فصل), the period its VAT is filed for. Before, both the
report endpoint and the assistant's get_tax_summary tool started at the Gregorian quarter: asked on
28 Aban 1405 (19 November 2026) they summed from 1 October instead of Mehr 1 (23 September), so every
season was cut in two. Both now share tax_service.default_period_start (found by Nightshift's QA notes).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest

from app.api.reports import get_tax_summary
from app.db.tenant import use_company
from app.models.company import Company
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.read_tools import GetTaxSummary, GetTaxSummaryInput
from app.services.locale_service import set_display_calendar
from app.services.tax_ir import season_of
from app.services.tax_service import default_period_start


@pytest.fixture()
def company(db):
    """A company of the given locale, in this test's session only (rolled back afterwards)."""
    def make(locale: str) -> Company:
        c = Company(id=uuid.uuid4(), name=f"Period {locale}", slug=f"period-{uuid.uuid4().hex[:8]}", locale=locale,
                    base_currency="IRR" if locale == "ir" else "GBP", status="active", token_version=0)
        db.add(c)
        db.flush()
        return c
    return make


@pytest.mark.parametrize("today, start", [
    (date(2026, 11, 19), date(2026, 9, 23)),    # 28 Aban 1405 → 1 Mehr
    (date(2026, 9, 23), date(2026, 9, 23)),     # 1 Mehr itself
    (date(2026, 9, 22), date(2026, 6, 22)),     # 31 Shahrivar → 1 Tir
    (date(2026, 3, 21), date(2026, 3, 21)),     # Nowruz 1405
    (date(2026, 3, 20), date(2025, 12, 22)),    # 29 Esfand 1404 → 1 Dey 1404
])
def test_an_iranian_company_starts_at_the_season(db, company, today, start):
    c = company("ir")
    with use_company(c.id):
        assert default_period_start(db, today) == start
        assert start == season_of(today).start           # the seasonal VAT report's own season


@pytest.mark.parametrize("today, start", [
    (date(2026, 11, 19), date(2026, 10, 1)),
    (date(2026, 1, 1), date(2026, 1, 1)),
    (date(2026, 3, 31), date(2026, 1, 1)),
])
def test_a_uk_company_keeps_the_calendar_quarter(db, company, today, start):
    c = company("uk")
    with use_company(c.id):
        assert default_period_start(db, today) == start


def test_the_display_calendar_decides(db, company):
    c = company("uk")
    with use_company(c.id):
        set_display_calendar(db, "jalali")
        assert default_period_start(db, date(2026, 11, 19)) == date(2026, 9, 23)


def test_the_report_endpoint_uses_it(db, company):
    today = date.today()
    for locale, expected in (("ir", season_of(today).start), ("uk", date(today.year, (today.month - 1) // 3 * 3 + 1, 1))):
        c = company(locale)
        with use_company(c.id):
            data = get_tax_summary(None, None, None, db)
        assert data["from_date"] == expected.isoformat(), locale
        assert data["to_date"] == today.isoformat()


def _tool(db, **kw):
    ctx = ToolContext(db=db, user_id="test-user", username="tester")
    return asyncio.run(GetTaxSummary().run(ctx, GetTaxSummaryInput(**kw)))


def test_the_assistant_tool_uses_it(db, company):
    c = company("ir")
    with use_company(c.id):
        out = _tool(db)
    assert out["from_date"] == season_of(date.today()).start.isoformat()
    assert out["to_date"] == date.today().isoformat()


def test_a_given_period_is_kept(db, company):
    c = company("ir")
    with use_company(c.id):
        out = _tool(db, from_date=date(2026, 1, 1), to_date=date(2026, 2, 1))
    assert (out["from_date"], out["to_date"]) == ("2026-01-01", "2026-02-01")

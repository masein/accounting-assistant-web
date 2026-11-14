"""The reviews' figures (chat review 2026-11-10, scenarios BW1, BW3).

From production: a person's monthly review said net worth rose in a month of spending — the starting balances
recorded that month and money changed from dollars counted as gains; and a company's review said the runway
twice. Figures made up. (The two runway formulas — the dashboard's gross costs with the month under way, the
insight's net burn of complete months — wait for the owner's choice.)
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.db.tenant import use_company
from app.services.ai_eval.fixture import seed_personal_books


@pytest.fixture()
def person(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def test_bw1_starting_balances_are_not_gains(db, person):
    from app.services.report_card import net_worth_change, report_card
    today = date.today()
    with use_company(person.company_id):
        worth = net_worth_change(db, today - timedelta(days=40), today, "OMR")
        # 100 + 60 OMR recorded as already there; 12.5 + 20.25 + 3.75 spent; Amir's 300 lent is owed back
        assert worth["recorded"] == Decimal("160") and worth["change"] == Decimal("-36.5")
        assert worth["end"] - worth["start"] == Decimal("123.5")
        card = report_card(db, today.strftime("%Y-%m"), currency="OMR", today=today)
    assert card["net_worth"]["change"] == card["net_worth"]["end"] - card["net_worth"]["start"] - card["net_worth"]["recorded"]


def test_bw3_the_review_says_the_runway_once():
    from app.services.proactive_reviews import _company_lines
    m = lambda v: str(v)  # noqa: E731
    facts = {"cash": 299, "runway": 1.0, "suggestions": [{"key": "runway", "params": {"months": 1.0}}]}
    said = " ".join(_company_lines(facts, "en", m))
    assert "months" not in said and "299" in said
    said = " ".join(_company_lines({**facts, "suggestions": []}, "en", m))
    assert "about 1 months" in said
    said = " ".join(_company_lines(facts, "en", m, ideas=False))             # suggestions switched off
    assert "about 1 months" in said

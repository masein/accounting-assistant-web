"""The CFO's sentences in Persian figures, and no runway without spending
(scenarios V1, V2). They read «درآمد کل IRR 34,480,830,000» and «با نرخ سوخت
فعلی 0 در ماه، حدود 999.0 ماه دوام می‌آورد» on the schools' books, whose last
entries are months old."""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

from app.db.tenant import use_company
from tests.test_cfo_receivables import _code, co  # noqa: F401  (the fixture)


def _journal(co, day, lines):
    r = co["api"].post("/transactions", json={
        "date": day.isoformat(), "description": "school year", "reference": f"V-{uuid.uuid4().hex[:6]}",
        "lines": [{"account_code": c, "debit": d, "credit": k} for c, d, k in lines]})
    assert r.status_code == 201, r.text


def _books(co, db):
    bank, revenue, expense = (_code(db, co, c) for c in ("bank", "revenue", "expense"))
    capital = _code(db, co, "share_capital")
    then = date.today() - timedelta(days=150)          # five months ago: nothing spent since
    _journal(co, then - timedelta(days=30), [(bank, 37_000_000_000, 0), (capital, 0, 37_000_000_000)])
    _journal(co, then, [(bank, 34_480_830_000, 0), (revenue, 0, 34_480_830_000)])
    _journal(co, then, [(expense, 35_200_130_356, 0), (bank, 0, 35_200_130_356)])


def test_v1_v2_the_persian_report_writes_persian_figures_and_no_runway(co, db):
    from app.services.cfo_intelligence import answer_cfo_question, build_cfo_report
    _books(co, db)
    with use_company(co["cid"]):
        fa = build_cfo_report(db, lang="fa")
        en = build_cfo_report(db, lang="en")
        runway_fa = answer_cfo_question(db, "چند ماه دوام داریم؟", lang="fa")
        health_fa = answer_cfo_question(db, "وضعیت سلامت مالی چطوره؟", lang="fa")

    said = [fa.narrative, runway_fa, health_fa] + [t for i in fa.insights for t in (i.title, i.body)]
    for text in said:                                                     # V1
        assert not re.search(r"[0-9]", text), text                       # no Latin digit anywhere
        assert "IRR" not in text, text
    assert "۳۴,۴۸۰,۸۳۰,۰۰۰ ریال" in fa.narrative and "۷۱۹,۳۰۰,۳۵۶ ریال" in fa.narrative
    assert any("٪" in i.title + i.body for i in fa.insights)              # «۳۸٪», not «38٪»
    assert "34,480,830,000 IRR" in en.narrative                           # English keeps both

    assert fa.runway_months == -1 and en.runway_months == -1              # V2: not «999»
    assert "۹۹۹" not in fa.narrative and "999" not in en.narrative
    assert "سه ماه اخیر" in fa.narrative and "سه ماه اخیر" in runway_fa
    assert "nothing was spent in the last three months" in en.narrative
    kpi = {k.key: k for k in en.kpis}["runway_months"]
    assert kpi.value == -1 and kpi.risk_level == "normal"
    assert not any("runway" in (i.title or "").lower() for i in en.insights)

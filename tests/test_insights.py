"""Proactive insights: deterministic detectors, four-language wording, the
/insights endpoint, the bell integration, the get_insights tool and the
chat briefing. Every scenario uses an isolated future year so other tests'
2026 data never falls inside a detector's window."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.bank_statement import BankStatement
from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity, TransactionEntity
from app.models.notification import Notification
from app.models.pay_run import PayRun, PayRunLine
from app.services import insight_service as svc
from app.services.insight_service import (
    Insight,
    briefing_text,
    compute_insights,
    detect_expense_spikes,
    detect_payroll,
    detect_receivables_growth,
    detect_revenue_drop,
    detect_statement_due,
    detect_vendor_outliers,
    runway_insight,
)


@pytest.fixture(autouse=True)
def _no_cache():
    svc.invalidate_insights_cache()
    yield
    svc.invalidate_insights_cache()


def _employee(db, name):
    e = Entity(type="employee", name=name)
    db.add(e)
    db.flush()
    return e


def _run(db, pay_date, lines, status="paid"):
    run = PayRun(period_start=pay_date.replace(day=1), period_end=pay_date, pay_date=pay_date,
                 status=status, total_gross=sum(g for _, g in lines), total_net=sum(g for _, g in lines))
    db.add(run)
    db.flush()
    for ent, gross in lines:
        db.add(PayRunLine(run_id=run.id, entity_id=ent.id, employee_name=ent.name, gross=gross,
                          taxable_base=gross, net_pay=gross))
    db.flush()
    return run


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------

def test_payroll_rise_names_the_new_employee(db):
    today = date(2040, 3, 20)
    ali, sara = _employee(db, "Ali Payroll"), _employee(db, "Sara Joiner")
    _run(db, date(2040, 2, 25), [(ali, 100_000_000)])
    _run(db, date(2040, 3, 15), [(ali, 100_000_000), (sara, 40_000_000)])
    db.commit()

    (ins,) = [i for i in detect_payroll(db, today) if i.kind == "payroll_change"]
    assert ins.key == "payroll-change-2040-03" and ins.severity == "warning"
    assert ins.data["pct"] == 40.0 and ins.data["joiners"] == ["Sara Joiner"]
    en, fa = ins.localize("en"), ins.localize("fa")
    assert en["title"] == "Payroll rose 40% in 2040-03"
    assert "New on payroll: Sara Joiner" in en["message"]
    assert "۴۰" not in fa["title"] and "40٪ بیشتر شد" in fa["title"]
    assert "کارمند جدید: Sara Joiner" in fa["message"]


def test_payroll_small_change_without_headcount_move_is_quiet(db):
    today = date(2041, 3, 20)
    ali = _employee(db, "Ali Steady")
    _run(db, date(2041, 2, 25), [(ali, 100_000_000)])
    _run(db, date(2041, 3, 15), [(ali, 104_000_000)])
    db.commit()
    assert [i for i in detect_payroll(db, today) if i.kind == "payroll_change"] == []


def test_stale_payroll_is_not_news(db):
    today = date(2042, 9, 1)
    ali = _employee(db, "Ali Old")
    _run(db, date(2042, 1, 25), [(ali, 100_000_000)])
    _run(db, date(2042, 2, 25), [(ali, 200_000_000)])
    db.commit()
    assert [i for i in detect_payroll(db, today) if i.kind == "payroll_change"] == []


def test_fresh_pay_profile_predicts_the_monthly_delta(db):
    today = date(2043, 5, 10)
    nika = _employee(db, "Nika Newhire")
    p = EmployeePayProfile(entity_id=nika.id, pay_type="salaried", base_salary=55_000_000, active=True)
    db.add(p)
    db.flush()
    p.created_at = datetime(2043, 5, 3, tzinfo=timezone.utc)
    db.commit()
    (ins,) = [i for i in detect_payroll(db, today) if i.kind == "new_employee"]
    assert ins.data == {"names": ["Nika Newhire"], "monthly_delta": 55_000_000}
    assert "55,000,000" in ins.localize("en")["message"]
    # A month later it is old news.
    assert [i for i in detect_payroll(db, date(2043, 7, 1)) if i.kind == "new_employee"] == []


# ---------------------------------------------------------------------------
# Expenses / revenue / runway
# ---------------------------------------------------------------------------

def test_expense_spike_needs_history_and_a_real_jump(db, make_transaction):
    today = date(2044, 6, 15)
    for m in (3, 4, 5):
        make_transaction([("6112", 10_000_000, 0), ("1110", 0, 10_000_000)], tx_date=date(2044, m, 10), description="internet")
    make_transaction([("6112", 45_000_000, 0), ("1110", 0, 45_000_000)], tx_date=date(2044, 6, 5), description="internet")
    db.commit()
    (ins,) = detect_expense_spikes(db, today)
    assert ins.kind == "expense_spike" and ins.data["account_code"] == "6112"
    assert ins.data["current"] == 45_000_000 and ins.data["average"] == 10_000_000
    assert ins.localize("en")["title"].endswith("is running 4.5× its usual level")

    # 12m on a 10m average is noise, not a spike.
    today2 = date(2045, 6, 15)
    for m in (3, 4, 5):
        make_transaction([("6112", 10_000_000, 0), ("1110", 0, 10_000_000)], tx_date=date(2045, m, 10))
    make_transaction([("6112", 12_000_000, 0), ("1110", 0, 12_000_000)], tx_date=date(2045, 6, 5))
    db.commit()
    assert detect_expense_spikes(db, today2) == []


def test_revenue_drop_on_last_complete_month(db, make_transaction):
    today = date(2046, 7, 3)
    for m in (3, 4, 5):
        make_transaction([("1110", 80_000_000, 0), ("4110", 0, 80_000_000)], tx_date=date(2046, m, 12))
    make_transaction([("1110", 30_000_000, 0), ("4110", 0, 30_000_000)], tx_date=date(2046, 6, 12))
    db.commit()
    (ins,) = detect_revenue_drop(db, today)
    assert ins.data["month"] == "2046-06" and ins.data["pct"] == 62.5
    assert "62%" in ins.localize("en")["title"] and "62٪" in ins.localize("fa")["title"]


def test_runway_thresholds():
    today = date(2047, 1, 1)
    assert runway_insight(cash=100, burn=0, today=today) is None            # not burning
    assert runway_insight(cash=1_000, burn=100, today=today) is None        # 10 months: fine
    warn = runway_insight(cash=500, burn=100, today=today)
    assert warn is not None and warn.severity == "warning" and warn.params["months"] == "5.0"
    high = runway_insight(cash=200, burn=100, today=today)
    assert high.severity == "high" and high.localize("es")["title"].startswith("El efectivo cubre unos 2.0")


# ---------------------------------------------------------------------------
# Vendors, statements, receivables
# ---------------------------------------------------------------------------

def test_vendor_outlier_against_the_suppliers_own_history(db, make_transaction):
    today = date(2048, 4, 20)
    sup = Entity(type="supplier", name="Arsbaran Paper")
    db.add(sup)
    db.flush()
    for m, amt in ((1, 10_000_000), (2, 11_000_000), (3, 9_500_000)):
        t = make_transaction([("6112", amt, 0), ("1110", 0, amt)], tx_date=date(2048, m, 5), description="paper")
        db.add(TransactionEntity(transaction_id=t.id, entity_id=sup.id, role="supplier"))
    big = make_transaction([("6112", 60_000_000, 0), ("1110", 0, 60_000_000)], tx_date=date(2048, 4, 10), description="paper — bulk")
    db.add(TransactionEntity(transaction_id=big.id, entity_id=sup.id, role="supplier"))
    db.commit()
    (ins,) = detect_vendor_outliers(db, today)
    assert ins.data["entity_name"] == "Arsbaran Paper" and ins.data["transaction_id"] == str(big.id)
    assert ins.data["typical"] == 10_000_000
    assert "usually about 10,000,000" in ins.localize("en")["message"]

    # A payment merely 20% above usual is not flagged.
    today2 = date(2049, 4, 20)
    sup2 = Entity(type="supplier", name="Quiet Supplier")
    db.add(sup2)
    db.flush()
    for m, amt in ((1, 10_000_000), (2, 10_000_000), (3, 10_000_000), (4, 12_000_000)):
        t = make_transaction([("6112", amt, 0), ("1110", 0, amt)], tx_date=date(2049, m, 5))
        db.add(TransactionEntity(transaction_id=t.id, entity_id=sup2.id, role="supplier"))
    db.commit()
    assert detect_vendor_outliers(db, today2) == []


def test_statement_due_after_forty_days(db):
    # The most recent statement in the DB decides; pin one as the latest.
    s = BankStatement(bank_name="Due Bank", source_type="csv", source_filename="due.csv",
                      currency="IRR", from_date=date(2050, 1, 1), to_date=date(2050, 1, 31),
                      status="parsed", total_rows=1)
    db.add(s)
    db.flush()
    s.created_at = datetime(2099, 6, 1, tzinfo=timezone.utc)
    db.commit()
    (ins,) = detect_statement_due(db, date(2050, 3, 20))
    assert ins.kind == "statement_due" and ins.data["last_statement_to"] == "2050-01-31"
    assert ins.page == "bank-statements"
    assert "2050-01-31" in ins.localize("fa")["message"]
    assert detect_statement_due(db, date(2050, 2, 20)) == []   # only 20 days


def test_receivables_growth(db, make_transaction):
    today = date(2051, 8, 30)
    make_transaction([("1112", 100_000_000, 0), ("4110", 0, 100_000_000)], tx_date=date(2051, 7, 1))
    make_transaction([("1112", 50_000_000, 0), ("4110", 0, 50_000_000)], tx_date=date(2051, 8, 20))
    db.commit()
    (ins,) = detect_receivables_growth(db, today)
    # Other tests' receivables (2026) are in both balances, so assert the move.
    assert ins.data["current"] - ins.data["previous"] == 50_000_000
    assert ins.page == "invoices"


# ---------------------------------------------------------------------------
# Orchestration: ranking, cache, resilience, surfaces
# ---------------------------------------------------------------------------

def test_compute_insights_ranks_and_survives_a_broken_detector(db, monkeypatch):
    def boom(db_, today):
        raise RuntimeError("detector exploded")

    def fine(db_, today):
        return [
            Insight(key="a", kind="statement_first", severity="info", page="bank-statements", params={"count": 3}, amount=1),
            Insight(key="b", kind="runway", severity="high", page="dashboard",
                    params={"months": "2.0", "cash": "1", "burn": "1"}, amount=5),
        ]

    monkeypatch.setattr(svc, "DETECTORS", (("boom", boom), ("fine", fine)))
    out = compute_insights(db, today=date(2052, 1, 1), use_cache=False)
    assert [i.key for i in out] == ["b", "a"]   # high before info


def test_cache_serves_the_same_result_within_ttl(db, monkeypatch):
    calls = {"n": 0}

    def counting(db_, today):
        calls["n"] += 1
        return []

    monkeypatch.setattr(svc, "DETECTORS", (("c", counting),))
    compute_insights(db, today=date(2053, 1, 1))
    compute_insights(db, today=date(2053, 1, 1))
    assert calls["n"] == 1
    compute_insights(db, today=date(2053, 1, 2))   # a new day recomputes
    assert calls["n"] == 2


def test_every_template_has_all_four_languages():
    for kind, tpl in svc._TEMPLATES.items():
        for part in ("title", "message"):
            assert set(tpl[part]) == set(svc.SUPPORTED_LANGUAGES), (kind, part)


def test_insights_endpoint_is_localized(auth_client, db, monkeypatch):
    monkeypatch.setattr(svc, "DETECTORS", (("one", lambda db_, today: [
        Insight(key="x", kind="statement_first", severity="info", page="bank-statements", params={"count": 21}),
    ]),))
    r = auth_client.get("/insights")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "en"
    assert body["insights"][0]["title"] == "Upload a bank statement to check your books"
    assert "21 bank movements" in body["insights"][0]["message"]
    assert body["insights"][0]["page"] == "bank-statements"


def test_insights_reach_the_bell_as_kind_insight(db, monkeypatch):
    from app.services.notification_service import refresh_notifications, visible_to

    monkeypatch.setattr(svc, "DETECTORS", (("one", lambda db_, today: [
        Insight(key="bell-test", kind="statement_first", severity="info", page="bank-statements", params={"count": 30}),
    ]),))
    refresh_notifications(db, today=date(2054, 1, 1))
    row = db.execute(select(Notification).where(Notification.dedupe_key == "insight-bell-test")).scalar_one()
    assert row.kind == "insight" and row.level == "info" and row.link_page == "bank-statements"
    assert row.dismissed_at is None
    # Visible to a personal user as well as the owner; not to an employee.
    assert visible_to(row, user_id="u", role="personal") and visible_to(row, user_id="u", role="owner")
    assert not visible_to(row, user_id="u", role="employee")

    # Condition cleared → the row auto-resolves on the next refresh.
    svc.invalidate_insights_cache()
    monkeypatch.setattr(svc, "DETECTORS", (("none", lambda db_, today: []),))
    refresh_notifications(db, today=date(2054, 1, 1))
    db.refresh(row)
    assert row.dismissed_at is not None


def test_get_insights_tool_and_registration(db, monkeypatch):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.insight_tools import GetInsights, GetInsightsInput
    from app.services.ai_accountant.orchestrator import build_default_registry, build_personal_registry

    assert "get_insights" in {t["name"] for t in build_default_registry().to_anthropic()}
    assert "get_insights" in {t["name"] for t in build_personal_registry().to_anthropic()}
    monkeypatch.setattr(svc, "DETECTORS", (("one", lambda db_, today: [
        Insight(key="t", kind="revenue_drop", severity="warning", page="dashboard",
                params={"pct": "30", "month": "2055-02", "current": "70", "average": "100"}),
    ]),))
    out = asyncio.run(GetInsights().run(ToolContext(db=db, user_id="u-ins"), GetInsightsInput(language="fa")))
    assert out["count"] == 1 and out["insights"][0]["title"] == "درآمد در 2055-02 30٪ کم شد"


def test_briefing_text_and_endpoint(auth_client, db, monkeypatch):
    assert briefing_text([], "en") is None
    ins = Insight(key="b", kind="statement_due", severity="info", page="bank-statements", params={"last": "2056-01-31"})
    txt = briefing_text([ins], "en")
    assert txt.startswith("Before we start") and "2056-01-31" in txt and txt.endswith("Want me to look into any of these?")

    # Endpoint with nothing to say writes nothing.
    monkeypatch.setattr(svc, "DETECTORS", (("none", lambda db_, today: []),))
    r = auth_client.post("/ai-accountant/briefing", json={"session_id": None})
    assert r.status_code == 200 and r.json()["text"] is None

    svc.invalidate_insights_cache()
    monkeypatch.setattr(svc, "DETECTORS", (("one", lambda db_, today: [ins]),))
    r = auth_client.post("/ai-accountant/briefing", json={"session_id": None})
    body = r.json()
    assert r.status_code == 200 and body["count"] == 1 and "2056-01-31" in body["text"]
    msgs = auth_client.get(f"/ai-accountant/sessions/{body['session_id']}/messages").json()
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"].get("briefing") is True

"""Anomaly detection as insights (roadmap 2026-09 §5.2): each pattern fires
on the case it describes and stays quiet on the near misses."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.company import Company
from app.models.credit_note import CreditNote
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services import anomaly_detection as A
from app.services.insight_service import compute_insights

TODAY = date(2026, 9, 21)        # a Monday
FRIDAY = date(2026, 9, 18)
SATURDAY = date(2026, 9, 19)


@pytest.fixture()
def books(db):
    c = Company(id=uuid.uuid4(), name="Anomaly Co", slug=f"an-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
    yield {"company": c, "acc": acc}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def _entity(db, b, name, kind="supplier"):
    with use_company(b["company"].id):
        e = Entity(id=uuid.uuid4(), name=name, type=kind)
        db.add(e)
        db.commit()
    return e


def _journal(db, b, day, amount, *, debit="6112", credit="1110", entity=None, role="supplier", reference=None,
             deleted=False, currency="IRR"):
    with use_company(b["company"].id):
        t = Transaction(id=uuid.uuid4(), date=day, reference=reference, description="j", currency=currency)
        if deleted:
            t.deleted_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.add(t)
        db.flush()
        db.add_all([TransactionLine(transaction_id=t.id, account_id=b["acc"][debit].id, debit=amount, credit=0),
                    TransactionLine(transaction_id=t.id, account_id=b["acc"][credit].id, debit=0, credit=amount)])
        if entity is not None:
            db.add(TransactionEntity(transaction_id=t.id, entity_id=entity.id, role=role))
        db.commit()
    return t


def _run(db, b, fn, today=TODAY):
    with use_company(b["company"].id):
        return fn(db, today)


# --- duplicates -------------------------------------------------------------------------------

def test_same_amount_to_the_same_supplier_within_a_week(db, books):
    s = _entity(db, books, "Supplier S")
    t1 = _journal(db, books, TODAY - timedelta(days=5), 5_000_000, entity=s)
    t2 = _journal(db, books, TODAY - timedelta(days=2), 5_000_000, entity=s)
    [ins] = _run(db, books, A.detect_duplicate_payments)
    assert ins.kind == "duplicate_payment" and ins.severity == "high"
    assert set(ins.data["transaction_ids"]) == {str(t1.id), str(t2.id)}
    assert ins.data["reason"] == "same amount within a week"


def test_not_duplicates(db, books):
    s, other = _entity(db, books, "Supplier S"), _entity(db, books, "Other")
    _journal(db, books, TODAY - timedelta(days=20), 5_000_000, entity=s)
    _journal(db, books, TODAY - timedelta(days=5), 5_000_000, entity=s)       # 15 days apart
    _journal(db, books, TODAY - timedelta(days=4), 5_000_000, entity=other)   # another supplier
    _journal(db, books, TODAY - timedelta(days=3), 5_000_000, entity=s, deleted=True)   # undone
    assert _run(db, books, A.detect_duplicate_payments) == []


def test_the_same_reference_paid_twice(db, books):
    s = _entity(db, books, "Supplier S")
    _journal(db, books, TODAY - timedelta(days=40), 3_000_000, entity=s, reference="INV-77")
    _journal(db, books, TODAY - timedelta(days=1), 3_100_000, entity=s, reference="INV-77")
    [ins] = _run(db, books, A.detect_duplicate_payments)
    assert ins.data["reason"] == "same reference" and ins.data["reference"] == "INV-77"


# --- just under the threshold ----------------------------------------------------------------------

def _threshold(db, b, amount):
    from app.services.expense_settings import set_expense_settings
    with use_company(b["company"].id):
        set_expense_settings(db, approval_threshold=amount)
        db.commit()


def test_split_payments_under_the_approval_threshold(db, books):
    _threshold(db, books, 10_000_000)
    s = _entity(db, books, "Supplier S")
    _journal(db, books, TODAY - timedelta(days=10), 9_500_000, entity=s)
    _journal(db, books, TODAY - timedelta(days=3), 9_800_000, entity=s)
    _journal(db, books, TODAY - timedelta(days=2), 4_000_000, entity=s)       # well under: not part of it
    [ins] = _run(db, books, A.detect_just_under_threshold)
    assert ins.params["count"] == 2 and ins.data["limit"] == 10_000_000 and ins.data["configured_threshold"] is True
    assert ins.amount == 19_300_000


def test_one_payment_under_the_threshold_is_not_a_pattern(db, books):
    _threshold(db, books, 10_000_000)
    _journal(db, books, TODAY - timedelta(days=3), 9_900_000, entity=_entity(db, books, "S"))
    assert _run(db, books, A.detect_just_under_threshold) == []


def test_expense_claims_split_under_the_threshold(db, books):
    from app.models.mileage_claim import MileageClaim
    _threshold(db, books, 1_000_000)
    with use_company(books["company"].id):
        for days in (4, 2):
            db.add(MileageClaim(employee_name="Reza", claim_date=TODAY - timedelta(days=days), distance=10,
                                unit="km", rate=95_000, amount=950_000, currency="IRR", status="approved"))
        db.commit()
    [ins] = _run(db, books, A.detect_just_under_threshold)
    assert ins.page == "expenses" and ins.params["name"] == "Reza" and ins.params["count"] == 2


def test_round_number_fallback_without_a_threshold(db, books):
    s = _entity(db, books, "Supplier S")
    _journal(db, books, TODAY - timedelta(days=9), 4_900_000, entity=s)
    _journal(db, books, TODAY - timedelta(days=1), 4_850_000, entity=s)
    [ins] = _run(db, books, A.detect_just_under_threshold)
    assert ins.data["limit"] == 5_000_000 and ins.data["configured_threshold"] is False
    assert A._round_ceiling(4_000_000) is None and A._round_ceiling(9_700_000) == 10_000_000


# --- a new vendor's large first payment -----------------------------------------------------------------

def _baseline(db, b, n=12, amount=1_000_000):
    regular = _entity(db, b, "Regular")
    for i in range(n):
        _journal(db, b, TODAY - timedelta(days=40 + i * 7), amount, entity=regular)
    return regular


def test_large_first_payment_to_a_new_supplier(db, books):
    regular = _baseline(db, books)
    new = _entity(db, books, "Brand New Ltd")
    t = _journal(db, books, TODAY - timedelta(days=2), 5_000_000, entity=new)
    _journal(db, books, TODAY - timedelta(days=1), 5_000_000, entity=regular)   # known supplier: not new
    [ins] = _run(db, books, A.detect_new_vendor_large)
    assert ins.params["name"] == "Brand New Ltd" and ins.data["transaction_id"] == str(t.id)
    assert ins.data["typical_supplier_payment"] == 1_000_000


def test_no_new_vendor_flag_without_a_baseline_or_when_small(db, books):
    _baseline(db, books, n=5)
    _journal(db, books, TODAY - timedelta(days=2), 9_000_000, entity=_entity(db, books, "New"))
    assert _run(db, books, A.detect_new_vendor_large) == []
    _baseline(db, books, n=12)
    _journal(db, books, TODAY - timedelta(days=2), 2_000_000, entity=_entity(db, books, "Small new"))
    assert all(i.params["name"] != "Small new" for i in _run(db, books, A.detect_new_vendor_large))


# --- round amounts on a weekend ------------------------------------------------------------------------------

def _activity(db, b, n=12):
    for i in range(n):
        _journal(db, b, TODAY - timedelta(days=35 + i), 3_123_000 + i * 1_000)


def test_round_friday_entries_in_iran(db, books):
    _activity(db, books)
    t = _journal(db, books, FRIDAY, 50_000_000)
    _journal(db, books, FRIDAY, 1_000_000)          # round but smaller than usual
    _journal(db, books, TODAY, 60_000_000)          # round but on a Monday
    [ins] = _run(db, books, A.detect_round_weekend)
    assert ins.params["count"] == 1 and ins.data["entries"][0]["transaction_id"] == str(t.id)


def test_weekend_follows_the_locale(db, books):
    books["company"].locale = "uk"
    db.commit()
    from app.services.locale_service import set_reporting_locale
    with use_company(books["company"].id):
        set_reporting_locale(db, "uk")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
    if "6112" not in acc:
        pytest.skip("uk chart codes differ")
    _activity(db, books)
    _journal(db, books, SATURDAY, 50_000_000)
    _journal(db, books, FRIDAY, 40_000_000)          # not a weekend in the UK
    [ins] = _run(db, books, A.detect_round_weekend)
    assert ins.params["count"] == 1 and ins.data["entries"][0]["date"] == SATURDAY.isoformat()


def test_an_insight_names_its_dates_in_the_company_calendar(db, books):
    """A weekend round amount said "on 2026-09-18" in a Jalali company (retest
    2, 2026-10-03, #54: the browser suite failed on a Saturday); the AI's data
    keeps the ISO date, and a Gregorian company keeps its own."""
    from app.services.locale_service import set_display_calendar
    _activity(db, books)
    _journal(db, books, FRIDAY, 50_000_000)
    with use_company(books["company"].id):
        feed = compute_insights(db, today=TODAY, use_cache=False)
    ins = next(i for i in feed if i.kind == "round_weekend")
    for lang in ("en", "fa", "es", "ar"):
        msg = ins.as_dict(lang)["message"]
        assert "1405/06/27" in msg and "2026-09-18" not in msg, (lang, msg)
    assert ins.data["entries"][0]["date"] == "2026-09-18"
    with use_company(books["company"].id):
        set_display_calendar(db, "gregorian")
        db.commit()
        feed = compute_insights(db, today=TODAY, use_cache=False)
    ins = next(i for i in feed if i.kind == "round_weekend")
    assert "2026-09-18" in ins.as_dict("en")["message"]


# --- category drift -----------------------------------------------------------------------------------------

def test_an_account_taking_over_spending(db, books):
    for days in (120, 200, 300):
        _journal(db, books, TODAY - timedelta(days=days), 1_000_000, debit="6112")
        _journal(db, books, TODAY - timedelta(days=days), 1_000_000, debit="6130")
    for days in (10, 40, 70):
        _journal(db, books, TODAY - timedelta(days=days), 1_000_000, debit="6112")
        _journal(db, books, TODAY - timedelta(days=days), 4_000_000, debit="6130")
    [ins] = _run(db, books, A.detect_category_drift)
    assert ins.data["account_code"] == "6130"
    assert ins.data["share_last_90_days"] == 80.0 and ins.data["share_previous_9_months"] == 50.0


def test_steady_spending_is_not_drift(db, books):
    for days in (10, 40, 70, 120, 200, 300):
        _journal(db, books, TODAY - timedelta(days=days), 1_000_000, debit="6112")
        _journal(db, books, TODAY - timedelta(days=days), 1_100_000, debit="6130")
    assert _run(db, books, A.detect_category_drift) == []


# --- reversals ------------------------------------------------------------------------------------------------

def test_a_run_of_reversals_for_one_counterparty(db, books):
    c = _entity(db, books, "Client C", kind="client")
    for i in range(2):
        _journal(db, books, TODAY - timedelta(days=5 + i), 700_000, debit="1110", credit="4110", entity=c,
                 role="client", deleted=True)
    with use_company(books["company"].id):
        db.add(CreditNote(entity_id=c.id, kind="sales", date=TODAY - timedelta(days=3), amount=100_000,
                          currency="IRR", note_type="reduction"))
        db.commit()
    [ins] = _run(db, books, A.detect_reversal_pattern)
    assert ins.params == {"name": "Client C", "undone": 2, "credits": 1}


def test_two_reversals_are_not_a_pattern(db, books):
    c = _entity(db, books, "Client C", kind="client")
    for i in range(2):
        _journal(db, books, TODAY - timedelta(days=5 + i), 700_000, entity=c, role="client", deleted=True)
    assert _run(db, books, A.detect_reversal_pattern) == []


# --- in the feed ---------------------------------------------------------------------------------------------------

def test_anomalies_reach_the_insight_feed_in_every_language(db, books):
    s = _entity(db, books, "تأمین‌کننده الف")
    _journal(db, books, TODAY - timedelta(days=5), 5_000_000, entity=s)
    _journal(db, books, TODAY - timedelta(days=2), 5_000_000, entity=s)
    with use_company(books["company"].id):
        feed = compute_insights(db, today=TODAY, use_cache=False)
    dup = next(i for i in feed if i.kind == "duplicate_payment")
    assert feed.index(dup) == 0                       # high severity comes first
    for lang in ("en", "fa", "es", "ar"):
        d = dup.as_dict(lang)
        assert "تأمین‌کننده الف" in d["title"] and "{" not in d["message"]


def test_a_broken_anomaly_detector_does_not_hide_the_rest(db, books, monkeypatch):
    from app.services import insight_service
    def boom(db, today):
        raise RuntimeError("detector bug")
    monkeypatch.setattr(insight_service, "DETECTORS", insight_service.DETECTORS + (("boom", boom),))
    with use_company(books["company"].id):
        assert isinstance(compute_insights(db, today=TODAY, use_cache=False), list)

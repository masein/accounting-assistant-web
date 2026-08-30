"""Installments (اقساط) and cheques (چک).

Both are dated obligations: an amount, a due date, a settle-or-bounce
lifecycle, and a reminder before the date. Installments of one loan share a
plan_id so "what's left" and "what's next" are grouped queries.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.models.account import Account
from app.models.commitment import BOUNCED, CHEQUE, INSTALLMENT, PAY, PENDING, RECEIVE, SETTLED, Commitment
from app.models.transaction import Transaction, TransactionLine
from app.services import commitment_service as svc

TODAY = date.today()


@pytest.fixture(autouse=True)
def _isolate(db):
    """Start each test with an empty schedule.

    The suite shares one session, so totals/feed assertions would otherwise see
    commitments left behind by earlier tests in this file.
    """
    db.execute(delete(Commitment))
    db.commit()
    yield
    db.execute(delete(Commitment))
    db.commit()


# ---------------------------------------------------------------------------
# Schedule arithmetic
# ---------------------------------------------------------------------------
class TestSplitAmount:
    def test_an_even_split_is_even(self):
        assert svc.split_amount(1_200_000, 12) == [100_000] * 12

    def test_the_remainder_lands_on_the_first_installment(self):
        parts = svc.split_amount(1_000_000, 3)
        assert parts == [333_334, 333_333, 333_333]

    def test_the_schedule_always_sums_to_the_debt(self):
        """A schedule that doesn't add up leaves a balance nobody can clear."""
        for total, count in [(1_000_000, 3), (7, 3), (999_999, 7), (10, 4)]:
            assert sum(svc.split_amount(total, count)) == total

    def test_a_single_installment_is_the_whole_amount(self):
        assert svc.split_amount(500, 1) == [500]

    def test_zero_count_is_refused(self):
        with pytest.raises(ValueError):
            svc.split_amount(100, 0)


class TestAddMonths:
    def test_ordinary_month_step(self):
        assert svc.add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)

    def test_month_end_is_clamped_not_skipped(self):
        """The 31st + 1 month must land on the 28th/30th, not overflow."""
        assert svc.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert svc.add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)

    def test_it_rolls_over_the_year(self):
        assert svc.add_months(date(2026, 11, 20), 3) == date(2027, 2, 20)

    def test_a_leap_february_is_respected(self):
        assert svc.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
class TestInstallmentPlan:
    def test_it_generates_a_dated_monthly_schedule(self, db):
        rows = svc.create_installment_plan(
            db, title="وام خودرو", total_amount=1_200_000, count=12,
            first_due=date(2026, 9, 10),
        )
        db.commit()
        assert len(rows) == 12
        assert [r.sequence for r in rows] == list(range(1, 13))
        assert all(r.plan_total == 12 for r in rows)
        assert rows[0].due_date == date(2026, 9, 10)
        assert rows[11].due_date == date(2027, 8, 10)
        assert len({r.plan_id for r in rows}) == 1

    def test_summary_tracks_what_is_left(self, db):
        rows = svc.create_installment_plan(
            db, title="قسط", total_amount=300_000, count=3, first_due=TODAY,
        )
        db.commit()
        svc.settle(db, rows[0], post=False)
        db.commit()

        s = svc.plan_summary(db, rows[0].plan_id)
        assert s["total_amount"] == 300_000
        assert s["remaining_amount"] == 200_000
        assert s["paid_count"] == 1 and s["total_count"] == 3
        assert s["next_due_date"] == rows[1].due_date

    def test_a_finished_plan_has_nothing_left(self, db):
        rows = svc.create_installment_plan(
            db, title="قسط", total_amount=200_000, count=2, first_due=TODAY)
        db.commit()
        for r in rows:
            svc.settle(db, r, post=False)
        db.commit()
        s = svc.plan_summary(db, rows[0].plan_id)
        assert s["remaining_amount"] == 0
        assert s["next_due_date"] is None

    def test_summary_of_an_unknown_plan_is_empty(self, db):
        assert svc.plan_summary(db, uuid.uuid4()) == {}


# ---------------------------------------------------------------------------
# Settlement posts real entries
# ---------------------------------------------------------------------------
class TestSettlement:
    def _codes(self, db, txn_id):
        out = {}
        for ln in db.execute(
            select(TransactionLine).where(TransactionLine.transaction_id == txn_id)
        ).scalars().all():
            out[db.get(Account, ln.account_id).code] = (ln.debit, ln.credit)
        return out

    def test_paying_an_installment_clears_the_liability_and_the_bank(self, db):
        row = svc.create_cheque(
            db, title="قسط وام", amount=500_000, due_date=TODAY,
            direction=PAY, counter_account_code="2110")
        db.commit()
        svc.settle(db, row)
        db.commit()

        assert row.status == SETTLED and row.settled_on == TODAY
        posted = self._codes(db, row.settled_transaction_id)
        assert posted["2110"] == (500_000, 0)     # liability debited down
        assert posted["1110"] == (0, 500_000)     # bank credited

    def test_receiving_money_reverses_the_legs(self, db):
        row = svc.create_cheque(
            db, title="چک دریافتی", amount=800_000, due_date=TODAY,
            direction=RECEIVE, counter_account_code="1112")
        db.commit()
        svc.settle(db, row)
        db.commit()
        posted = self._codes(db, row.settled_transaction_id)
        assert posted["1110"] == (800_000, 0)     # bank debited
        assert posted["1112"] == (0, 800_000)

    def test_settling_without_an_account_records_no_entry(self, db):
        """Tracking-only: some users just want the reminder, not the posting."""
        row = svc.create_cheque(db, title="چک", amount=100, due_date=TODAY)
        db.commit()
        before = len(db.execute(select(Transaction)).scalars().all())
        svc.settle(db, row)
        db.commit()
        assert row.status == SETTLED
        assert row.settled_transaction_id is None
        assert len(db.execute(select(Transaction)).scalars().all()) == before

    def test_settling_twice_is_refused(self, db):
        from fastapi import HTTPException

        row = svc.create_cheque(db, title="چک", amount=100, due_date=TODAY)
        db.commit()
        svc.settle(db, row, post=False)
        db.commit()
        with pytest.raises(HTTPException):
            svc.settle(db, row, post=False)

    def test_a_bounced_cheque_is_not_settled(self, db):
        """The money is still owed — it must not read as done."""
        row = svc.create_cheque(db, title="چک برگشتی", amount=100, due_date=TODAY)
        db.commit()
        svc.mark_bounced(db, row)
        db.commit()
        assert row.status == BOUNCED
        assert row in list(svc.outstanding(db))

    def test_only_a_cheque_can_bounce(self, db):
        from fastapi import HTTPException

        rows = svc.create_installment_plan(
            db, title="قسط", total_amount=100, count=1, first_due=TODAY)
        db.commit()
        with pytest.raises(HTTPException):
            svc.mark_bounced(db, rows[0])


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------
def test_totals_separate_what_you_owe_from_what_you_are_owed(db):
    svc.create_cheque(db, title="پرداختنی", amount=300_000, due_date=TODAY, direction=PAY)
    svc.create_cheque(db, title="دریافتنی", amount=500_000,
                      due_date=TODAY + timedelta(days=5), direction=RECEIVE)
    db.commit()
    t = svc.totals(db)
    assert t["payable"] == 300_000
    assert t["receivable"] == 500_000
    assert t["count"] == 2
    assert t["next_due_date"] == TODAY


def test_settled_items_drop_out_of_totals(db):
    row = svc.create_cheque(db, title="x", amount=100, due_date=TODAY)
    db.commit()
    svc.settle(db, row, post=False)
    db.commit()
    assert svc.totals(db)["count"] == 0


# ---------------------------------------------------------------------------
# API + reminders
# ---------------------------------------------------------------------------
class TestApi:
    def test_create_plan_and_list(self, auth_client):
        r = auth_client.post("/commitments/installments", json={
            "title": "وام مسکن", "total_amount": 600_000, "count": 6,
            "first_due": TODAY.isoformat(), "counter_account_code": "2110"})
        assert r.status_code == 201, r.text
        assert len(r.json()) == 6

        listed = auth_client.get("/commitments?kind=installment").json()
        assert len([x for x in listed if x["title"] == "وام مسکن"]) == 6

    def test_settle_through_the_api(self, auth_client):
        made = auth_client.post("/commitments/cheques", json={
            "title": "چک", "amount": 250_000, "due_date": TODAY.isoformat(),
            "counter_account_code": "2110"}).json()
        r = auth_client.post(f"/commitments/{made['id']}/settle", json={"post": True})
        assert r.status_code == 200
        assert r.json()["status"] == "settled"
        assert r.json()["settled_transaction_id"]

    def test_a_settled_commitment_cannot_be_deleted(self, auth_client):
        made = auth_client.post("/commitments/cheques", json={
            "title": "چک", "amount": 100, "due_date": TODAY.isoformat()}).json()
        auth_client.post(f"/commitments/{made['id']}/settle", json={"post": False})
        assert auth_client.delete(f"/commitments/{made['id']}").status_code == 400

    def test_a_pending_commitment_can_be_deleted(self, auth_client):
        made = auth_client.post("/commitments/cheques", json={
            "title": "چک", "amount": 100, "due_date": TODAY.isoformat()}).json()
        assert auth_client.delete(f"/commitments/{made['id']}").status_code == 204

    def test_zero_amount_is_rejected(self, auth_client):
        r = auth_client.post("/commitments/cheques", json={
            "title": "چک", "amount": 0, "due_date": TODAY.isoformat()})
        assert r.status_code == 422

    def test_due_soon_appears_on_the_notification_feed(self, auth_client):
        auth_client.post("/commitments/cheques", json={
            "title": "چک اجاره", "amount": 900_000,
            "due_date": (TODAY + timedelta(days=1)).isoformat()})
        feed = auth_client.get("/notifications/feed").json()
        hit = next((i for i in feed if i["kind"] == "commitment" and "چک اجاره" in i["title"]), None)
        assert hit is not None and hit["level"] == "warning"

    def test_overdue_is_escalated(self, auth_client):
        auth_client.post("/commitments/cheques", json={
            "title": "قسط عقب‌افتاده", "amount": 100_000,
            "due_date": (TODAY - timedelta(days=5)).isoformat()})
        feed = auth_client.get("/notifications/feed").json()
        hit = next((i for i in feed if "قسط عقب‌افتاده" in i["title"]), None)
        assert hit is not None and hit["level"] == "high"
        assert "overdue" in hit["message"]

    def test_a_far_future_commitment_does_not_nag(self, auth_client):
        auth_client.post("/commitments/cheques", json={
            "title": "چک دور", "amount": 100,
            "due_date": (TODAY + timedelta(days=90)).isoformat()})
        feed = auth_client.get("/notifications/feed").json()
        assert not any("چک دور" in i["title"] for i in feed)

    def test_settling_clears_the_alert(self, auth_client):
        made = auth_client.post("/commitments/cheques", json={
            "title": "چک تسویه", "amount": 100,
            "due_date": (TODAY + timedelta(days=1)).isoformat()}).json()
        assert any("چک تسویه" in i["title"] for i in auth_client.get("/notifications/feed").json())

        auth_client.post(f"/commitments/{made['id']}/settle", json={"post": False})
        assert not any("چک تسویه" in i["title"] for i in auth_client.get("/notifications/feed").json())

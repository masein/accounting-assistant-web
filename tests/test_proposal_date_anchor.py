"""The entry date on a chat proposal must be anchored to the server's clock,
not the model's unreliable guess: "today" → date.today(), "yesterday" →
today−1, no date → today, an explicit absolute date is kept, and a document
(OCR) turn keeps the invoice's own date.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.date_resolver import (
    has_explicit_absolute_date,
    resolve_entry_date,
)
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
)

T0 = date(2026, 6, 13)          # "today" for deterministic unit tests
STALE = date(2023, 10, 18)      # the model's hallucinated date


class TestResolveEntryDate:
    def test_today_anchors_to_today(self):
        msg = "Record a 300 GBP office-supplies expense paid from cash today"
        assert resolve_entry_date(msg, STALE, today=T0) == T0

    def test_yesterday(self):
        assert resolve_entry_date("paid rent yesterday", STALE, today=T0) == T0 - timedelta(days=1)

    def test_no_date_defaults_to_today(self):
        # No date term + a drifted model date → today (don't trust the guess).
        assert resolve_entry_date("5000 GBP from Acme as sales", STALE, today=T0) == T0

    def test_explicit_absolute_date_is_kept(self):
        model = date(2026, 2, 10)
        assert resolve_entry_date("record it on 2026-02-10", model, today=T0) == model

    def test_explicit_month_name_is_kept(self):
        model = date(2026, 3, 3)
        assert resolve_entry_date("the bill on 3 March", model, today=T0) == model

    def test_document_turn_keeps_model_date(self):
        # OCR/invoice turn: the document's own date wins, untouched.
        inv_date = date(2026, 1, 5)  # Jalali 1404/10/15
        assert resolve_entry_date("here is the receipt", inv_date, today=T0, has_attachment=True) == inv_date

    def test_n_days_ago(self):
        assert resolve_entry_date("paid 3 days ago", STALE, today=T0) == T0 - timedelta(days=3)

    def test_persian_yesterday(self):
        assert resolve_entry_date("دیروز اجاره دادم", STALE, today=T0) == T0 - timedelta(days=1)

    def test_spanish_yesterday(self):
        assert resolve_entry_date("pagué el alquiler ayer", STALE, today=T0) == T0 - timedelta(days=1)

    def test_arabic_yesterday(self):
        assert resolve_entry_date("دفعت الإيجار أمس", STALE, today=T0) == T0 - timedelta(days=1)

    def test_persian_n_days_ago_digits(self):
        # Persian digits in "۳ روز پیش".
        assert resolve_entry_date("۳ روز پیش پرداختم", STALE, today=T0) == T0 - timedelta(days=3)

    def test_last_weekday(self):
        # T0 = Saturday 2026-06-13. "last tuesday" → the prior Tuesday.
        out = resolve_entry_date("paid last tuesday", STALE, today=T0)
        assert out.weekday() == 1 and out < T0

    def test_bare_weekday_in_name_not_treated_as_date(self):
        # "Friday" inside a description (no qualifier) → no relative match →
        # no date stated → today.
        assert resolve_entry_date("paid Friday Cafe Ltd", STALE, today=T0) == T0

    def test_explicit_absolute_detection(self):
        assert has_explicit_absolute_date("on 2026-02-10")
        assert has_explicit_absolute_date("3 March")
        assert not has_explicit_absolute_date("the March invoice")
        assert not has_explicit_absolute_date("paid today")


class TestProposalDateAnchored:
    @pytest.fixture(autouse=True)
    def _isolate(self, db: Session):
        yield
        db.execute(delete(AIProposal))
        db.execute(delete(TransactionLine))
        db.execute(delete(Transaction))
        db.commit()

    def _propose(self, db: Session, message: str, model_date: str, attachment_ids=None):
        ctx = ToolContext(db=db, user_id="u", username="t", user_message=message,
                          attachment_ids=attachment_ids or [])
        out = asyncio.run(ProposeCreateTransaction().run(ctx, ProposeCreateTransactionInput(
            date=model_date, description="x", currency="IRR",
            lines=[
                {"account_code": "6110", "debit": 1000, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 1000},
            ],
        )))
        row = db.execute(select(AIProposal).where(
            AIProposal.confirmation_token == uuid.UUID(out["confirmation_token"]))).scalar_one()
        return row.tool_input["date"]

    def test_today_message_uses_real_today(self, db: Session):
        # Model put a stale 2023 date; "today" in the message → real today.
        assert self._propose(db, "300 expense from cash today", "2023-10-18") == date.today().isoformat()

    def test_no_date_message_uses_today(self, db: Session):
        assert self._propose(db, "5000 from Acme as sales", "2026-01-05") == date.today().isoformat()

    def test_attachment_turn_keeps_document_date(self, db: Session):
        # A document attached this turn → keep the model's (document) date.
        assert self._propose(db, "here is the receipt", "2026-01-05",
                             attachment_ids=["any"]) == "2026-01-05"


class TestFutureDateGuard:
    """Past/today dates always record; only genuinely-future, non-scheduled
    dates are refused — enforced server-side against the REAL today."""

    @pytest.fixture(autouse=True)
    def _isolate(self, db: Session):
        yield
        db.execute(delete(AIProposal))
        db.execute(delete(TransactionLine))
        db.execute(delete(Transaction))
        db.commit()

    def _run(self, db: Session, message: str, model_date: str):
        ctx = ToolContext(db=db, user_id="u", username="t", user_message=message)
        out = asyncio.run(ProposeCreateTransaction().run(ctx, ProposeCreateTransactionInput(
            date=model_date, description="x", currency="IRR",
            lines=[
                {"account_code": "6110", "debit": 1000, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 1000},
            ],
        )))
        row = db.execute(select(AIProposal).where(
            AIProposal.confirmation_token == uuid.UUID(out["confirmation_token"]))).scalar_one()
        return row.tool_input["date"]

    def test_explicit_past_date_records(self, db: Session):
        # The live bug: an explicit past date wrongly refused as "future".
        # 2026-02-10 is past relative to a 2026-06-13 today.
        past = (date.today() - timedelta(days=120)).isoformat()
        assert self._run(db, f"record rent on {past}", past) == past

    def test_genuinely_future_date_rejected(self, db: Session):
        from app.services.ai_accountant.base import ToolError
        future = (date.today() + timedelta(days=200)).isoformat()
        ctx = ToolContext(db=db, user_id="u", username="t", user_message=f"record it on {future}")
        with pytest.raises(ToolError) as ei:
            asyncio.run(ProposeCreateTransaction().run(ctx, ProposeCreateTransactionInput(
                date=future, description="x", currency="IRR",
                lines=[
                    {"account_code": "6110", "debit": 1000, "credit": 0},
                    {"account_code": "1110", "debit": 0, "credit": 1000},
                ],
            )))
        assert ei.value.code == "future_date"

    def test_scheduled_future_date_allowed(self, db: Session):
        # Explicitly scheduled → the future date is kept and allowed.
        future = (date.today() + timedelta(days=30)).isoformat()
        assert self._run(db, f"schedule this rent for {future}", future) == future

    def test_today_message_not_future(self, db: Session):
        assert self._run(db, "pay it today", "2099-01-01") == date.today().isoformat()

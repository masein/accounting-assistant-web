"""Fixes for the 28-Mordad bug reports.

1. A bank's "View transactions" must show every journal touching its GL cash
   account (not only entity-linked ones), with the bank's own net move.
2. Typing "confirm" in chat while a card is pending → deterministic pointer to
   the card's button, never a duplicate proposal.
3. propose_reverse_transaction mirrors the original legs server-side.
4. The ledger excludes soft-deleted journals and never 500s on a line whose
   account row is inaccessible.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.ai_accountant import AIProposal
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.proposal_tools import (
    ProposeReverseTransaction,
    ProposeReverseTransactionInput,
)

USER = "u-rev"


def _mk_txn(make_transaction, *, reference, lines):
    return make_transaction(reference=reference, lines=lines)


# ---------------------------------------------------------------------------
# 1. Bank View-transactions completeness
# ---------------------------------------------------------------------------

def test_bank_view_includes_gl_line_transactions_with_share(auth_client, db, make_transaction):
    code = "1117"
    from app.services.account_resolver import _ensure_account
    _ensure_account(db, code, "بانک ویو تست — bank account", "ir")
    bank = Entity(type="bank", name=f"بانک ویو {uuid.uuid4().hex[:5]}", code=code)
    db.add(bank)
    db.commit()

    ref = f"VIEW-{uuid.uuid4().hex[:6]}"
    make_transaction(reference=ref, lines=[(code, 0, 900_000), ("6112", 900_000, 0)])

    txns = auth_client.get(f"/reports/entities/{bank.id}/transactions").json()
    hit = next((t for t in txns if t.get("reference") == ref), None)
    assert hit is not None, "journal touching the bank's GL account must appear"
    link = next(l for l in hit["entity_links"] if l["entity_id"] == str(bank.id))
    assert link["amount"] == -900_000  # the bank's own net move (credit)


def test_bank_view_hides_soft_deleted(auth_client, db, make_transaction):
    code = "1118"
    from app.services.account_resolver import _ensure_account
    _ensure_account(db, code, "بانک سافت دیلیت — bank account", "ir")
    bank = Entity(type="bank", name=f"بانک حذف {uuid.uuid4().hex[:5]}", code=code)
    db.add(bank)
    db.commit()
    ref = f"SOFT-{uuid.uuid4().hex[:6]}"
    txn = make_transaction(reference=ref, lines=[(code, 500, 0), ("4110", 0, 500)])
    txn.deleted_at = datetime.now(timezone.utc)
    db.commit()
    txns = auth_client.get(f"/reports/entities/{bank.id}/transactions").json()
    assert all(t.get("reference") != ref for t in txns)


# ---------------------------------------------------------------------------
# 2. Typed "confirm" guard
# ---------------------------------------------------------------------------

def test_typed_confirm_points_at_card_instead_of_duplicating(client, db):
    from tests.conftest import _CSRFTestClient

    from app.api.ai_accountant import _is_bare_confirmation
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings

    assert _is_bare_confirmation("confirm the transaction")
    assert _is_bare_confirmation("تایید کن")
    assert _is_bare_confirmation("yes, do it")
    assert not _is_bare_confirmation("confirm with the client that the invoice arrived")

    user_id = str(uuid.uuid4())
    token = create_session_token(user_id=user_id, username="confirmer", is_admin=True)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    scoped = _CSRFTestClient(client, csrf)

    prop = AIProposal(
        confirmation_token=uuid.uuid4(),
        user_id=user_id,
        tool_name="propose_create_transaction",
        tool_input={"date": date.today().isoformat(), "lines": []},
        status="pending",
    )
    db.add(prop)
    db.commit()

    n_before = len(db.execute(select(AIProposal)).scalars().all())
    resp = scoped.post("/ai-accountant/chat", json={
        "message": "confirm the transaction", "session_id": None, "attachment_ids": [],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["stop_reason"] == "intake"       # deterministic, no LLM
    assert "Confirm" in body["text"] or "تأیید" in body["text"] or "کارت" in body["text"]
    assert body["proposals"] == []               # no duplicate card
    assert len(db.execute(select(AIProposal)).scalars().all()) == n_before


# ---------------------------------------------------------------------------
# 3. Server-side reversal tool
# ---------------------------------------------------------------------------

def _ctx(db, msg="please undo the last transaction"):
    return ToolContext(db=db, user_id=USER, username="t", user_message=msg)


def test_reverse_tool_mirrors_legs_exactly(db, make_transaction):
    ref = f"ORIG-{uuid.uuid4().hex[:6]}"
    make_transaction(reference=ref, lines=[("1110", 4_048_000_000, 0), ("4110", 0, 4_048_000_000)])

    out = asyncio.run(ProposeReverseTransaction().run(
        _ctx(db), ProposeReverseTransactionInput(reference=ref)))
    assert out["status"] == "pending"
    lines = out["preview"]["lines"]
    by_code = {l["account_code"]: l for l in lines}
    # exact mirror: the original DEBITED 1110, so the reversal CREDITS it
    assert by_code["1110"]["credit"] == 4_048_000_000 and by_code["1110"]["debit"] == 0
    assert by_code["4110"]["debit"] == 4_048_000_000 and by_code["4110"]["credit"] == 0

    res = execute_proposal(db, confirmation_token=out["confirmation_token"],
                           actor_user_id=USER, actor_username="t")
    rev = db.get(Transaction, uuid.UUID(res.transaction_id))
    assert rev.reference == f"REV-{ref}"
    rev_lines = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == rev.id)
    ).scalars().all()
    assert sum(l.debit for l in rev_lines) == sum(l.credit for l in rev_lines) == 4_048_000_000

    # net effect on the bank account is zero after the reversal
    acc = db.execute(select(Account).where(Account.code == "1110")).scalars().first()
    both = db.execute(
        select(TransactionLine).join(Transaction).where(
            TransactionLine.account_id == acc.id,
            Transaction.reference.in_([ref, f"REV-{ref}"]),
        )
    ).scalars().all()
    assert sum(l.debit - l.credit for l in both) == 0


def test_reverse_tool_refuses_double_reversal(db, make_transaction):
    ref = f"ONCE-{uuid.uuid4().hex[:6]}"
    make_transaction(reference=ref, lines=[("1110", 100, 0), ("4110", 0, 100)])
    out = asyncio.run(ProposeReverseTransaction().run(
        _ctx(db), ProposeReverseTransactionInput(reference=ref)))
    execute_proposal(db, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    with pytest.raises(ToolError) as exc:
        asyncio.run(ProposeReverseTransaction().run(
            _ctx(db), ProposeReverseTransactionInput(reference=ref)))
    assert exc.value.code == "already_reversed"


def test_reverse_last_targets_most_recent(db, make_transaction):
    from datetime import timedelta

    ref = f"LAST-{uuid.uuid4().hex[:6]}"
    txn = make_transaction(reference=ref, lines=[("6112", 777, 0), ("1110", 0, 777)])
    # created_at has second precision on SQLite — make this strictly newest
    txn.created_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    db.commit()
    out = asyncio.run(ProposeReverseTransaction().run(
        _ctx(db), ProposeReverseTransactionInput(last=True)))
    assert out["preview"]["reference"] == f"REV-{ref}"


# ---------------------------------------------------------------------------
# 4. Ledger robustness
# ---------------------------------------------------------------------------

def test_ledger_excludes_soft_deleted_journals(auth_client, db, make_transaction):
    ref = f"GHOST-{uuid.uuid4().hex[:6]}"
    txn = make_transaction(reference=ref, lines=[("1110", 123_456, 0), ("4110", 0, 123_456)])

    def _row(code):
        rows = auth_client.get("/reports/ledger-summary").json()["rows"]
        return next((r for r in rows if r["account_code"] == code), None)

    before = _row("1110")["debit_turnover"]
    txn.deleted_at = datetime.now(timezone.utc)
    db.commit()
    after = _row("1110")["debit_turnover"]
    assert after == before - 123_456  # the ghost no longer counts

"""AI equity tools: confirm-gated propose→execute for the four cues
(contribution / capital increase / dividend / current account)."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.company import Company
from app.models.entity import Entity
from app.models.equity import Shareholding
from app.models.transaction import TransactionLine
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.equity_tools import (
    ProposeCapitalIncrease,
    ProposeCapitalIncreaseInput,
    ProposeContribution,
    ProposeContributionInput,
    ProposeCurrentAccount,
    ProposeCurrentAccountInput,
    ProposeDeclareDividend,
    ProposeDeclareDividendInput,
)
from app.services.ai_accountant.execute_service import execute_proposal

USER = "ai-user"


@pytest.fixture(autouse=True)
def company(db):
    c = Company(id=uuid.uuid4(), name="EqCo", slug=f"eq-{uuid.uuid4().hex[:8]}",
                locale="ir", base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.flush()
    try:
        with use_company(c.id):
            yield c
    finally:
        _purge_company(db, c.id)


def _purge_company(db, company_id):
    from app.db.tenant import tenant_bypass
    from app.models.account import Account
    from app.models.ai_accountant import AIProposal
    from app.models.audit_log import AuditLog
    from app.models.entity import TransactionEntity
    from app.models.equity import EquityEvent
    from app.models.transaction import Transaction, TransactionLine
    db.rollback()
    with tenant_bypass():
        for Model in (TransactionLine, TransactionEntity, EquityEvent, Shareholding,
                      AIProposal, AuditLog, Transaction, Entity, Account):
            db.query(Model).filter(Model.company_id == company_id).delete(synchronize_session=False)
        db.commit()


def _sh(db, name, percent):
    e = Entity(name=name, type="shareholder")
    db.add(e)
    db.flush()
    db.add(Shareholding(entity_id=e.id, percent=percent))
    db.flush()
    return e


def _ctx(db, msg="do it"):
    return ToolContext(db=db, user_id=USER, username="ai", user_message=msg)


def _exec(db, out):
    return execute_proposal(db, confirmation_token=out["confirmation_token"],
                            actor_user_id=USER, actor_username="ai")


def _balanced(db, txn_id):
    ls = db.execute(select(TransactionLine).where(
        TransactionLine.transaction_id == uuid.UUID(txn_id))).scalars().all()
    return ls and sum(x.debit for x in ls) == sum(x.credit for x in ls)


def test_contribution_confirm_gated(db):
    cyrus = _sh(db, "Cyrus", 100)
    out = asyncio.run(ProposeContribution().run(
        _ctx(db, "Cyrus contributed 500m as capital"),
        ProposeContributionInput(shareholder_name="Cyrus", amount=500_000_000, date="2026-03-01")))
    assert out["status"] == "pending"
    # nothing posted before confirm
    assert db.execute(select(TransactionLine)).first() is None
    res = _exec(db, out)
    assert res.transaction_id is not None and _balanced(db, res.transaction_id)


def test_dividend_allocates_by_cap_table(db):
    _sh(db, "Cyrus", 60)
    _sh(db, "Sara", 40)
    out = asyncio.run(ProposeDeclareDividend().run(
        _ctx(db, "distribute a 100m dividend"),
        ProposeDeclareDividendInput(total_amount=100_000_000, date="2026-03-10")))
    allocs = {a["entity_name"]: a["amount"] for a in out["preview"]["allocations"]}
    assert allocs == {"Cyrus": 60_000_000, "Sara": 40_000_000}
    res = _exec(db, out)
    assert res.transaction_id is not None


def test_capital_increase(db):
    _sh(db, "Cyrus", 100)
    out = asyncio.run(ProposeCapitalIncrease().run(
        _ctx(db, "increase capital by 1bn from retained earnings"),
        ProposeCapitalIncreaseInput(amount=1_000_000_000, source="retained_earnings", date="2026-03-15")))
    res = _exec(db, out)
    assert res.transaction_id is not None and _balanced(db, res.transaction_id)


def test_current_account_withdrawal(db):
    _sh(db, "Sara", 100)
    out = asyncio.run(ProposeCurrentAccount().run(
        _ctx(db, "Sara withdrew 50m"),
        ProposeCurrentAccountInput(shareholder_name="Sara", amount=50_000_000, direction="out", date="2026-03-12")))
    res = _exec(db, out)
    assert res.transaction_id is not None and _balanced(db, res.transaction_id)


def test_unknown_shareholder_errors(db):
    with pytest.raises(ToolError):
        asyncio.run(ProposeContribution().run(
            _ctx(db), ProposeContributionInput(shareholder_name="Ghost", amount=1000)))


def test_dividend_without_cap_table_errors(db):
    with pytest.raises(ToolError):
        asyncio.run(ProposeDeclareDividend().run(
            _ctx(db), ProposeDeclareDividendInput(total_amount=1000)))


def test_tools_registered():
    from app.services.ai_accountant.orchestrator import build_default_registry
    names = {t.name for t in build_default_registry()}
    assert {"propose_shareholder_contribution", "propose_capital_increase",
            "propose_declare_dividend", "propose_shareholder_current_account"} <= names

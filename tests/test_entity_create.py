"""AI-accountant entity creation (confirm-gated): standalone, combined with a
transaction, contractor→supplier mapping, bank GL-account allocation, and the
no-create-without-confirm guard — on UK + Iran seeds.
"""
from __future__ import annotations

import asyncio
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.entity_create import normalize_entity_type
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateEntity,
    ProposeCreateEntityInput,
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
)
from app.services.locale_service import set_reporting_locale

USER = "u1"


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _account_exists(db: Session, code: str) -> bool:
    return db.execute(select(Account).where(Account.code == code)).scalar_one_or_none() is not None


# ─── type mapping ──────────────────────────────────────────────────────

def test_contractor_maps_to_supplier():
    assert normalize_entity_type("contractor") == "supplier"
    assert normalize_entity_type("freelancer") == "supplier"
    assert normalize_entity_type("subcontractor") == "supplier"
    assert normalize_entity_type("vendor") == "supplier"
    assert normalize_entity_type("customer") == "client"
    assert normalize_entity_type("bank") == "bank"
    assert normalize_entity_type("nonsense") == "supplier"


# ─── standalone propose_create_entity ──────────────────────────────────

def test_standalone_entity_create_confirm_gated(uk):
    ctx = ToolContext(db=uk, user_id=USER, username="t", user_message="add Acme Ltd as a client")
    out = asyncio.run(ProposeCreateEntity().run(
        ctx, ProposeCreateEntityInput(name="Acme Ltd", type="client")))
    token = out["confirmation_token"]
    # Nothing created before Confirm.
    assert uk.execute(select(func.count()).select_from(Entity)).scalar() == 0

    res = execute_proposal(uk, confirmation_token=token, actor_user_id=USER, actor_username="t")
    assert res.transaction_id is None
    ent = uk.execute(select(Entity).where(Entity.name == "Acme Ltd")).scalar_one()
    assert ent.type == "client"


def test_no_create_without_confirm(uk):
    ctx = ToolContext(db=uk, user_id=USER, user_message="add Beta as supplier")
    asyncio.run(ProposeCreateEntity().run(ctx, ProposeCreateEntityInput(name="Beta", type="supplier")))
    # Proposal registered, but no entity persisted until execute.
    assert uk.execute(select(func.count()).select_from(Entity)).scalar() == 0


# ─── combined: create entity + transaction, linked ─────────────────────

@pytest.mark.parametrize("fixture,expense,bank", [("uk", "7100", "1200"), ("ir", "6112", "1110")])
def test_combined_create_entity_and_link(fixture, expense, bank, request):
    db = request.getfixturevalue(fixture)
    ctx = ToolContext(db=db, user_id=USER, username="t",
                      user_message="paid Dan Campbell 10000 from the bank")
    payload = ProposeCreateTransactionInput(
        date=date(2025, 6, 18).isoformat(),
        description="paid Dan Campbell — OTL project",
        currency="GBP" if fixture == "uk" else "IRR",
        lines=[
            {"account_code": expense, "debit": 10_000, "credit": 0},
            {"account_code": bank, "debit": 0, "credit": 10_000},
        ],
        new_entities=[{"name": "Dan Campbell", "type": "supplier", "role": "supplier"}],
    )
    out = asyncio.run(ProposeCreateTransaction().run(ctx, payload))
    assert out["new_entities"][0]["name"] == "Dan Campbell"
    assert "Will create supplier: Dan Campbell" in out["summary"]
    # No entity until confirm.
    assert db.execute(select(func.count()).select_from(Entity)).scalar() == 0

    res = execute_proposal(db, confirmation_token=out["confirmation_token"],
                           actor_user_id=USER, actor_username="t")
    dan = db.execute(select(Entity).where(Entity.name == "Dan Campbell")).scalar_one()
    assert dan.type == "supplier"
    # Linked to the posted transaction.
    link = db.execute(
        select(TransactionEntity).where(TransactionEntity.entity_id == dan.id)
    ).scalar_one()
    assert str(link.transaction_id) == res.transaction_id
    # Posting balanced.
    lines = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == UUID(res.transaction_id))
    ).scalars().all()
    assert sum(li.debit for li in lines) == sum(li.credit for li in lines) == 10_000


# ─── bank entity gets a usable GL cash account ─────────────────────────

@pytest.mark.parametrize("fixture,prefix", [("uk", "12"), ("ir", "111")])
def test_bank_entity_creates_gl_account(fixture, prefix, request):
    db = request.getfixturevalue(fixture)
    ctx = ToolContext(db=db, user_id=USER, username="t",
                      user_message="we opened a new business account at Barclays")
    out = asyncio.run(ProposeCreateEntity().run(
        ctx, ProposeCreateEntityInput(name="Barclays", type="bank")))
    assert out["new_entities"][0]["account_code"].startswith(prefix)
    assert "new cash account" in out["summary"]

    execute_proposal(db, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    bank = db.execute(select(Entity).where(Entity.name == "Barclays")).scalar_one()
    assert bank.type == "bank"
    # Entity.code points at a real GL account → usable as a payment source.
    assert bank.code and bank.code.startswith(prefix)
    assert _account_exists(db, bank.code)


def test_bank_links_existing_account_when_referenced(uk):
    ctx = ToolContext(db=uk, user_id=USER, username="t", user_message="add HSBC, use account 1200")
    out = asyncio.run(ProposeCreateEntity().run(
        ctx, ProposeCreateEntityInput(name="HSBC Main", type="bank", existing_account_code="1200")))
    assert out["new_entities"][0]["account_code"] == "1200"
    execute_proposal(uk, confirmation_token=out["confirmation_token"],
                     actor_user_id=USER, actor_username="t")
    bank = uk.execute(select(Entity).where(Entity.name == "HSBC Main")).scalar_one()
    assert bank.code == "1200"  # reused, no new account invented

"""Time-billing AI tools: confirm-gated propose→execute for logging time
(one card even when it creates a new client + project + worker), setting a rate,
and invoicing time — on UK seed.
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
from app.db.seed import UK_SEED_ACCOUNTS, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.time_billing import Project, TimeEntry
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.time_tools import (
    ProposeCreateInvoiceFromTime,
    ProposeInvoiceFromTimeInput,
    ProposeLogTime,
    ProposeLogTimeInput,
    ProposeSetBillableRate,
    ProposeSetBillableRateInput,
)
from app.services.locale_service import set_reporting_locale
from app.services.tax_rate_service import seed_tax_rates

USER = "u1"


@pytest.fixture
def uk():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code = {}
    for code, name, level in UK_SEED_ACCOUNTS:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in UK_SEED_ACCOUNTS:
        p = _parent_code_uk(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, "uk")
    db.commit()
    seed_tax_rates(db)
    try:
        yield db
    finally:
        db.close()


def _client(db, name="Acme Group"):
    e = Entity(type="client", name=name)
    db.add(e); db.commit(); db.refresh(e)
    return e


def _worker(db, name="Sarah Lee", type_="employee"):
    e = Entity(type=type_, name=name)
    db.add(e); db.commit(); db.refresh(e)
    return e


def test_log_time_new_client_project_one_card(uk):
    # Nothing exists yet: the single proposal must create client + project +
    # worker and the entry, in ONE card.
    ctx = ToolContext(db=uk, user_id=USER, username="t",
                      user_message="Log 3 hours for Sarah on the Acme OTL project today, dashboard build")
    out = asyncio.run(ProposeLogTime().run(ctx, ProposeLogTimeInput(
        employee="Sarah", client="Acme", project="OTL", hours=3, description="dashboard build")))
    assert "confirmation_token" in out
    names = {ne["name"] for ne in out["new_entities"]}
    assert {"Sarah", "Acme"} <= names                  # folded into the one card
    assert out["new_project"] == "OTL"
    # Nothing persisted before Confirm.
    assert uk.execute(select(func.count()).select_from(TimeEntry)).scalar() == 0

    execute_proposal(uk, confirmation_token=out["confirmation_token"], actor_user_id=USER, actor_username="t")
    entry = uk.execute(select(TimeEntry)).scalars().one()
    assert float(entry.hours) == 3
    assert uk.get(Entity, entry.client_id).name == "Acme"
    proj = uk.get(Project, entry.project_id)
    assert proj.name == "OTL"
    assert uk.get(Entity, entry.employee_id).type == "employee"


def test_log_time_freelancer_worker_is_supplier(uk):
    ctx = ToolContext(db=uk, user_id=USER, username="t",
                      user_message="Log 2 hours for Nina, a freelancer, on Acme today")
    out = asyncio.run(ProposeLogTime().run(ctx, ProposeLogTimeInput(
        employee="Nina", client="Acme", hours=2)))
    execute_proposal(uk, confirmation_token=out["confirmation_token"], actor_user_id=USER, actor_username="t")
    nina = uk.execute(select(Entity).where(Entity.name == "Nina")).scalar_one()
    assert nina.type == "supplier"   # freelancer → supplier


def test_set_rate_then_invoice_from_time(uk):
    client = _client(uk)
    worker = _worker(uk)
    proj = Project(client_id=client.id, name="OTL", status="active")
    uk.add(proj); uk.commit(); uk.refresh(proj)

    # Set a project rate via the proposal tool.
    ctx = ToolContext(db=uk, user_id=USER, username="t", user_message="set Sarah's rate to 90 for the OTL project")
    rate_out = asyncio.run(ProposeSetBillableRate().run(ctx, ProposeSetBillableRateInput(
        employee="Sarah", rate=90, client="Acme", project="OTL")))
    execute_proposal(uk, confirmation_token=rate_out["confirmation_token"], actor_user_id=USER, actor_username="t")

    # Log time directly (model would use propose_log_time; use the entry model here).
    uk.add(TimeEntry(employee_id=worker.id, client_id=client.id, project_id=proj.id,
                     work_date=date(2026, 6, 10), hours=4, billable=True, status="unbilled"))
    uk.commit()

    inv_ctx = ToolContext(db=uk, user_id=USER, username="t", user_message="invoice Acme for the OTL hours")
    out = asyncio.run(ProposeCreateInvoiceFromTime().run(inv_ctx, ProposeInvoiceFromTimeInput(client="Acme")))
    assert out["status"] == "pending"
    assert out["invoice_preview"]["subtotal"] == 360   # 4 × 90
    assert "Includes 1 time entries" in out["summary"]

    execute_proposal(uk, confirmation_token=out["confirmation_token"], actor_user_id=USER, actor_username="t")
    inv = uk.execute(select(Invoice).where(Invoice.kind == "sales")).scalars().one()
    assert inv.transaction_id is not None
    entry = uk.execute(select(TimeEntry)).scalars().one()
    assert entry.status == "invoiced" and entry.invoice_id == inv.id


def test_invoice_empty_is_friendly_noop(uk):
    _client(uk)
    ctx = ToolContext(db=uk, user_id=USER, username="t", user_message="invoice Acme for hours")
    out = asyncio.run(ProposeCreateInvoiceFromTime().run(ctx, ProposeInvoiceFromTimeInput(client="Acme")))
    assert out["status"] == "no_op"
    assert "No unbilled time" in out["summary"]

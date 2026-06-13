"""Tests for the convergence tools added to fix the AI accountant being
unable to post entries or build statements:

* search_accounts — resolves a plain category ("office supplies", "cash") to
  a real chart code, locale-aware, so the model stops guessing prefixes.
* get_financial_statement — deterministic balance sheet / P&L / trial
  balance / cash flow (A = L + E; Dr == Cr) instead of hand-summing.

Also re-confirms the amount guard passes a valid balanced 300/300 entry.

These use their OWN isolated in-memory SQLite engine (not the shared session
fixture) so seeding the UK chart and flipping the reporting locale can't leak
into the rest of the suite.
"""
from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import date as _date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import (
    SEED_ACCOUNTS,
    UK_SEED_ACCOUNTS,
    _parent_code_ir,
    _parent_code_uk,
)
from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
    _guard_amount,
)
from app.services.ai_accountant.read_tools import (
    GetFinancialStatement,
    GetFinancialStatementInput,
    SearchAccounts,
    SearchAccountsInput,
)
from app.services.locale_service import set_reporting_locale


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover - trivial
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
        parent = parent_fn(code)
        if parent and parent in by_code:
            by_code[code].parent_id = by_code[parent].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk_db() -> Generator[Session, None, None]:
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir_db() -> Generator[Session, None, None]:
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _post(db: Session, lines: list[tuple[str, int, int]], on: str = "2026-03-01") -> None:
    accounts = {a.code: a for a in db.query(Account).all()}
    txn = Transaction(date=_date.fromisoformat(on), description="t", currency="GBP")
    db.add(txn)
    db.flush()
    for code, dr, cr in lines:
        db.add(TransactionLine(transaction_id=txn.id, account_id=accounts[code].id, debit=dr, credit=cr))
    db.commit()


class TestSearchAccounts:
    def test_office_supplies_resolves_to_7600(self, uk_db: Session):
        out = asyncio.run(SearchAccounts().run(
            ToolContext(db=uk_db, user_id="u"), SearchAccountsInput(query="office supplies")))
        assert out["matches"], "expected at least one match"
        assert out["matches"][0]["code"] == "7600"
        assert out["matches"][0]["normal_balance"] == "debit"

    def test_cash_resolves_to_bank_current(self, uk_db: Session):
        out = asyncio.run(SearchAccounts().run(
            ToolContext(db=uk_db, user_id="u"), SearchAccountsInput(query="cash")))
        codes = [m["code"] for m in out["matches"]]
        assert "1200" in codes
        assert codes[0] in ("1200", "1220")

    def test_rent_and_sales_resolve(self, uk_db: Session):
        rent = asyncio.run(SearchAccounts().run(
            ToolContext(db=uk_db, user_id="u"), SearchAccountsInput(query="rent")))
        assert rent["matches"][0]["code"] == "7200"
        sales = asyncio.run(SearchAccounts().run(
            ToolContext(db=uk_db, user_id="u"), SearchAccountsInput(query="sales")))
        assert sales["matches"][0]["code"] == "4000"

    def test_type_filter(self, uk_db: Session):
        out = asyncio.run(SearchAccounts().run(
            ToolContext(db=uk_db, user_id="u"),
            SearchAccountsInput(query="bank", account_type="ASSET")))
        assert all(m["type"] == "ASSET" for m in out["matches"])

    def test_iranian_chart_cash_resolves(self, ir_db: Session):
        out = asyncio.run(SearchAccounts().run(
            ToolContext(db=ir_db, user_id="u"), SearchAccountsInput(query="cash")))
        codes = [m["code"] for m in out["matches"]]
        assert "1110" in codes


class TestFinancialStatement:
    def test_trial_balance_balances(self, uk_db: Session):
        _post(uk_db, [("1200", 1000, 0), ("3000", 0, 1000)])
        _post(uk_db, [("7600", 300, 0), ("1200", 0, 300)])
        out = asyncio.run(GetFinancialStatement().run(
            ToolContext(db=uk_db, user_id="u"),
            GetFinancialStatementInput(statement="trial_balance")))
        assert out["balanced"] is True
        assert out["total_debit"] == out["total_credit"]

    def test_balance_sheet_assets_equal_liab_plus_equity(self, uk_db: Session):
        _post(uk_db, [("1200", 1000, 0), ("3000", 0, 1000)])   # capital
        _post(uk_db, [("7600", 300, 0), ("1200", 0, 300)])     # expense
        _post(uk_db, [("1200", 500, 0), ("4000", 0, 500)])     # sales
        out = asyncio.run(GetFinancialStatement().run(
            ToolContext(db=uk_db, user_id="u"),
            GetFinancialStatementInput(statement="balance_sheet")))
        assert out["balanced"] is True
        assert out["assets_total"] == out["liabilities_total"] + out["equity_total"]
        assert out["assets_total"] == 1200          # bank 1000 - 300 + 500
        assert out["equity_total"] == 1200          # capital 1000 + net income 200
        assert out["retained_earnings"] == 200

    def test_income_statement_net(self, uk_db: Session):
        _post(uk_db, [("1200", 500, 0), ("4000", 0, 500)])
        _post(uk_db, [("7600", 300, 0), ("1200", 0, 300)])
        out = asyncio.run(GetFinancialStatement().run(
            ToolContext(db=uk_db, user_id="u"),
            GetFinancialStatementInput(statement="income_statement",
                                       from_date="2026-01-01", to_date="2026-12-31")))
        assert out["revenue_total"] == 500
        assert out["expense_total"] == 300
        assert out["net_income"] == 200


class TestGuardAllowsValidEntry:
    def test_balanced_300_with_source_300_passes(self):
        # The amount guard must NOT reject a valid balanced 300/300 entry whose
        # source amount is 300 (proposed_total = sum of debits = 300).
        ctx = ToolContext(db=None, user_id="u", source_amounts=[300])
        _guard_amount(ctx, 300)  # no raise

    def test_propose_300_gbp_office_supplies(self, uk_db: Session):
        ctx = ToolContext(db=uk_db, user_id="u", username="t",
                          user_message="300 GBP office supplies from cash")
        out = asyncio.run(ProposeCreateTransaction().run(ctx, ProposeCreateTransactionInput(
            date="2026-03-01", description="office supplies", currency="GBP",
            lines=[
                {"account_code": "7600", "debit": 300, "credit": 0},
                {"account_code": "1200", "debit": 0, "credit": 300},
            ],
        )))
        assert out["confirmation_token"]

"""Unit tests for the AI accountant read tools.

These tools never touch the LLM — they're pure DB queries with Pydantic
input validation, so they're fully testable offline. Live Anthropic
calls are tested separately in the orchestrator suite.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.services.ai_accountant.base import ToolContext, ToolError, ToolRegistry
from app.services.ai_accountant.read_tools import (
    FindEntity, FindEntityInput,
    GetAccountBalance, GetAccountBalanceInput,
    GetCompanyDefaults, GetCompanyDefaultsInput,
    ListEntities, ListEntitiesInput,
    QueryLedger, QueryLedgerInput,
    register_read_tools,
)


def _run(coro):
    """Drive a coroutine to completion in the test (no real async loop)."""
    return asyncio.get_event_loop().run_until_complete(coro) \
        if asyncio.get_event_loop_policy().get_event_loop().is_running() is False \
        else asyncio.run(coro)


def _ctx(db: Session) -> ToolContext:
    return ToolContext(db=db, user_id="test-user", username="tester")


@pytest.fixture()
def entities(db: Session) -> list[Entity]:
    """Seed three entities with distinguishable names + roles."""
    rows = [
        Entity(name="Kim Nguyen", type="employee"),
        Entity(name="Kim Industries", type="client"),
        Entity(name="Acme Corp", type="client"),
    ]
    for r in rows:
        db.add(r)
    db.commit()
    yield rows
    for r in rows:
        db.delete(r)
    db.commit()


# ---------------------------------------------------------------------------
# find_entity
# ---------------------------------------------------------------------------


class TestFindEntity:
    def test_exact_match_top_ranked(self, db: Session, entities: list[Entity]) -> None:
        tool = FindEntity()
        result = asyncio.run(tool.run(_ctx(db), FindEntityInput(query="Kim Nguyen")))
        assert result["matches"], "expected at least one match"
        top = result["matches"][0]
        assert top["name"] == "Kim Nguyen"
        assert top["confidence"] >= 0.95

    def test_multiple_matches_returned(self, db: Session, entities: list[Entity]) -> None:
        """\"Kim\" matches both Kim Nguyen and Kim Industries — both surface."""
        tool = FindEntity()
        result = asyncio.run(tool.run(_ctx(db), FindEntityInput(query="Kim")))
        names = [m["name"] for m in result["matches"]]
        assert "Kim Nguyen" in names
        assert "Kim Industries" in names

    def test_type_filter_narrows(self, db: Session, entities: list[Entity]) -> None:
        tool = FindEntity()
        result = asyncio.run(
            tool.run(_ctx(db), FindEntityInput(query="Kim", type="client"))
        )
        names = [m["name"] for m in result["matches"]]
        assert "Kim Industries" in names
        assert "Kim Nguyen" not in names  # employee — filtered out

    def test_no_match_returns_empty_list(self, db: Session, entities: list[Entity]) -> None:
        tool = FindEntity()
        result = asyncio.run(tool.run(_ctx(db), FindEntityInput(query="Zelenoff")))
        assert result["matches"] == []

    def test_empty_query_raises(self, db: Session) -> None:
        tool = FindEntity()
        with pytest.raises(Exception):  # ValidationError from Pydantic
            asyncio.run(tool.run(_ctx(db), FindEntityInput(query="")))


# ---------------------------------------------------------------------------
# list_entities
# ---------------------------------------------------------------------------


class TestListEntities:
    def test_returns_all_when_unfiltered(self, db: Session, entities: list[Entity]) -> None:
        tool = ListEntities()
        result = asyncio.run(tool.run(_ctx(db), ListEntitiesInput()))
        assert result["count"] >= 3

    def test_type_filter(self, db: Session, entities: list[Entity]) -> None:
        tool = ListEntities()
        result = asyncio.run(tool.run(_ctx(db), ListEntitiesInput(type="employee")))
        for e in result["entities"]:
            assert e["type"] == "employee"


# ---------------------------------------------------------------------------
# get_account_balance
# ---------------------------------------------------------------------------


class TestGetAccountBalance:
    def test_returns_cash_balance(self, db: Session, make_transaction) -> None:
        make_transaction(
            [("1110", 5_000_000, 0), ("3110", 0, 5_000_000)],
            description="seed capital",
        )
        db.commit()
        tool = GetAccountBalance()
        result = asyncio.run(
            tool.run(_ctx(db), GetAccountBalanceInput(account_code="1110"))
        )
        assert result["account_code"] == "1110"
        assert result["balance"] == 5_000_000

    def test_missing_account_raises_tool_error(self, db: Session) -> None:
        tool = GetAccountBalance()
        with pytest.raises(ToolError) as ei:
            asyncio.run(
                tool.run(_ctx(db), GetAccountBalanceInput(account_code="9999"))
            )
        assert ei.value.code == "account_not_found"


# ---------------------------------------------------------------------------
# query_ledger
# ---------------------------------------------------------------------------


class TestQueryLedger:
    def test_empty_period_returns_zero_totals(self, db: Session) -> None:
        tool = QueryLedger()
        # Force a period that has no data so the totals must be zero.
        result = asyncio.run(
            tool.run(
                _ctx(db),
                QueryLedgerInput(
                    from_date=date(2020, 1, 1),
                    to_date=date(2020, 12, 31),
                    account_code="6110",
                ),
            )
        )
        assert result["total_debit"] == 0
        assert result["total_credit"] == 0

    def test_account_prefix_filter(self, db: Session, make_transaction) -> None:
        make_transaction(
            [("6110", 500_000, 0), ("1110", 0, 500_000)],
            description="payroll",
            tx_date=date(2026, 3, 1),
        )
        db.commit()
        tool = QueryLedger()
        result = asyncio.run(
            tool.run(
                _ctx(db),
                QueryLedgerInput(
                    from_date=date(2026, 1, 1),
                    to_date=date(2026, 12, 31),
                    account_code="61",  # SG&A prefix
                ),
            )
        )
        assert result["total_debit"] >= 500_000


# ---------------------------------------------------------------------------
# get_company_defaults
# ---------------------------------------------------------------------------


class TestGetCompanyDefaults:
    def test_returns_locale_calendar_currency_and_today(self, db: Session) -> None:
        tool = GetCompanyDefaults()
        result = asyncio.run(tool.run(_ctx(db), GetCompanyDefaultsInput()))
        assert "reporting_locale" in result
        assert "display_calendar" in result
        assert "default_currency" in result
        assert "today" in result
        assert len(result["today"]) == 10  # ISO date


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registers_five_read_tools(self) -> None:
        reg = ToolRegistry()
        register_read_tools(reg)
        assert len(reg) == 5
        for name in ("find_entity", "list_entities", "query_ledger",
                     "get_account_balance", "get_company_defaults"):
            assert name in reg

    def test_to_anthropic_shape(self) -> None:
        reg = ToolRegistry()
        register_read_tools(reg)
        for tool_def in reg.to_anthropic():
            assert set(tool_def.keys()) >= {"name", "description", "input_schema"}
            assert tool_def["input_schema"]["type"] == "object"
            # No $ref / $defs should leak through — Anthropic doesn't accept them.
            assert "$defs" not in tool_def["input_schema"]
            assert "$ref" not in str(tool_def["input_schema"])

    def test_duplicate_registration_rejected(self) -> None:
        reg = ToolRegistry()
        reg.register(FindEntity())
        with pytest.raises(ValueError):
            reg.register(FindEntity())

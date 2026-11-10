"""Spending and ledger answers from the books as they are (chat review 2026-11-10, scenarios BR1–BR6).

From the production chats, in a person's books kept in rials whose money is mostly Omani rials: the ledger with no
currency asked came back as 0 rials with "add rates" beside it, and nothing found was answered from the chat as if
it were recorded; food asked about missed the meals eaten out; a bank's ledger brought every line of each voucher
it was on, the whole opening voucher with it; "the last 5" were five lines, and a day's entries in random order.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.account import Account
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.read_tools import QueryLedger, QueryLedgerInput
from app.services.ai_eval.fixture import GREEN_WALLET, MELLAT, PRETTY_WALLET, seed_personal_books

USER = "u-ledger"


@pytest.fixture()
def books(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _ask(db, **kw):
    return asyncio.run(QueryLedger().run(ToolContext(db=db, user_id=USER, mode="personal"), QueryLedgerInput(**kw)))


def _since(n):
    return date.today() - timedelta(days=n)


def test_br1_each_currency_in_its_own_amounts(db, books):
    with use_company(books.company_id):
        out = _ask(db, account_code="61", from_date=_since(7), to_date=date.today())
        assert set(out["totals_by_currency"]) == {"OMR", "IRR"}
        assert Decimal(str(out["totals_by_currency"]["OMR"]["signed_net"])) == Decimal("36.5")      # 12.5 + 20.25 + 3.75
        assert out["totals_by_currency"]["IRR"]["signed_net"] == 3_000_000
        assert "total_debit" not in out and "unconverted" not in out and "Don't add them up" in out["note"]
        dining = _ask(db, account_code="6180", from_date=_since(7), to_date=date.today())
        assert dining["currency"] == "OMR" and Decimal(str(dining["signed_net"])) == Decimal("12.5")
        assert "unconverted" not in dining                                       # no "add rates" for one currency


def test_br2_nothing_found_is_said_so(db, books):
    with use_company(books.company_id):
        out = _ask(db, account_code="6", from_date=_since(1), to_date=_since(1))
    assert out["row_count"] == 0 and "never answer from the conversation" in out["note"]


def test_br3_food_is_groceries_and_eating_out(db, books):
    from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
    with use_company(books.company_id):
        out = _ask(db, account_code="6110,6180", group_by="account", from_date=_since(7), to_date=date.today())
        got = {(a["account_code"], a.get("currency")): a["debit"] for a in out["by_account"]}
        assert got == {("6110", "IRR"): 3_000_000, ("6110", "OMR"): Decimal("20.25"), ("6180", "OMR"): Decimal("12.5")}
        week = asyncio.run(GetSpendingSummary().run(
            ToolContext(db=db, user_id=USER, mode="personal"),
            GetSpendingSummaryInput(period="custom", from_date=_since(7), to_date=date.today(), category_code="6110،6180")))
    omr = next(c for c in week["by_currency"] if c["currency"] == "OMR")
    assert {c["category_code"] for c in omr["by_category"]} == {"6110", "6180"}
    assert Decimal(str(omr["total"])) == Decimal("32.75")


def test_br4_a_banks_ledger_is_its_own_lines(db, books):
    with use_company(books.company_id):
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        bank = Entity(id=uuid.uuid4(), name="حساب ملت", type="bank", code=MELLAT, company_id=uuid.UUID(books.company_id))
        db.add(bank)
        opening = Transaction(id=uuid.uuid4(), date=_since(2), description="سند افتتاحیه", currency="IRR")
        db.add(opening)
        db.flush()
        for code, d, c in ((MELLAT, 7_000_000, 0), (GREEN_WALLET, 0, 0), (PRETTY_WALLET, 2_000_000, 0), ("3110", 0, 9_000_000)):
            db.add(TransactionLine(transaction_id=opening.id, account_id=acc[code].id, debit=d, credit=c))
        db.add(TransactionEntity(transaction_id=opening.id, entity_id=bank.id, role="bank"))
        db.commit()
        out = _ask(db, entity_id=str(bank.id), from_date=_since(3), to_date=date.today())
    assert {r["account_code"] for r in out["sample"]} == {MELLAT} and out["lines_on_accounts"] == [MELLAT]
    assert out["total_debit"] == 7_000_000


def test_br5_the_latest_entries_newest_first(db, books):
    with use_company(books.company_id):
        day = db.execute(select(Transaction).where(Transaction.date == _since(5))).scalars().all()
        assert len(day) == 2
        lulu, taxi = sorted(day, key=lambda t: t.description != "لولو")
        now = datetime.now(timezone.utc)
        lulu.created_at, taxi.created_at = now - timedelta(hours=2), now - timedelta(hours=1)    # the taxi came later
        db.commit()
        out = _ask(db, from_date=_since(5), to_date=_since(4), limit=2)
    entries = list(dict.fromkeys(r["transaction_id"] for r in out["sample"]))
    assert len(entries) == 2 and len(out["sample"]) == 4                         # two entries, each with both lines
    assert [r["description"] for r in out["sample"]][::2] == ["خرید ماهانه", "تاکسی"]      # newest first
    assert out["entry_count"] == 3


def test_br6_the_assistant_answers_from_what_the_tools_find():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM
    assert "is groceries 6110 and eating out 6180 together: pass «6110,6180»" in PERSONAL_MODE_ADDENDUM
    assert "never list what the user told you earlier as if it were in the books" in PERSONAL_MODE_ADDENDUM

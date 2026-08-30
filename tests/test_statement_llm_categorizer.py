"""The LLM tier for statement rows the rules can't place.

Everything here is mocked: the import path must work offline, so the tests
that matter most are the failure ones — a model that is down, unconfigured,
or returns nonsense must degrade to "no suggestion", never to a wrong account
and never to a broken import.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import PERSONAL_SEED_ACCOUNTS, _parent_code_ir
from app.models.account import Account
from app.services import statement_llm_categorizer as mod
from app.services.locale_service import set_reporting_locale


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code = {}
    for code, name_fa, _en, level in PERSONAL_SEED_ACCOUNTS:
        acc = Account(code=code, name=name_fa, level=level)
        s.add(acc)
        by_code[code] = acc
    s.flush()
    for code, *_ in PERSONAL_SEED_ACCOUNTS:
        p = _parent_code_ir(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(s, "ir")
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _fake_ask(reply: str):
    """Replace the network call with a canned model reply."""
    async def _ask(accounts, narrations):
        return mod._extract_json_object(reply)
    return _ask


def _run(db, items):
    return asyncio.run(mod.suggest_unknown(db, items))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_places_an_unknown_merchant(db, monkeypatch):
    monkeypatch.setattr(mod, "_ask", _fake_ask('{"1": "6130"}'))
    out = _run(db, [("r1", "ZZQX TRANSPORT LLC", True)])
    assert out["r1"].account_code == "6130"
    assert out["r1"].source == "llm"
    assert out["r1"].confidence == mod.CONFIDENCE_LLM


def test_null_means_no_suggestion(db, monkeypatch):
    """'I don't know' must stay unanswered — a wrong guess is worse."""
    monkeypatch.setattr(mod, "_ask", _fake_ask('{"1": null}'))
    assert _run(db, [("r1", "???", True)]) == {}


def test_debits_and_credits_are_asked_separately(db, monkeypatch):
    """Disjoint candidate sets make it structurally impossible to file a
    payment as income."""
    seen: list[set[str]] = []

    async def _ask(accounts, narrations):
        seen.append({a.code for a in accounts})
        return {}

    monkeypatch.setattr(mod, "_ask", _ask)
    _run(db, [("r1", "spend", True), ("r2", "receive", False)])

    assert len(seen) == 2
    expense, revenue = seen
    assert all(c.startswith(("61", "62")) for c in expense)
    assert all(c.startswith("4") for c in revenue)
    assert not (expense & revenue)


def test_empty_input_makes_no_call(db, monkeypatch):
    called = False

    async def _ask(accounts, narrations):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(mod, "_ask", _ask)
    assert _run(db, []) == {}
    assert called is False


def test_blank_narrations_are_not_sent(db, monkeypatch):
    called = False

    async def _ask(accounts, narrations):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(mod, "_ask", _ask)
    assert _run(db, [("r1", "   ", True)]) == {}
    assert called is False


# ---------------------------------------------------------------------------
# The model is not trusted
# ---------------------------------------------------------------------------
def test_a_code_outside_the_chart_is_discarded(db, monkeypatch):
    """Hallucinated codes must never reach the books."""
    monkeypatch.setattr(mod, "_ask", _fake_ask('{"1": "9999"}'))
    assert _run(db, [("r1", "x", True)]) == {}


def test_a_wrong_nature_code_is_discarded(db, monkeypatch):
    """4110 is income; it can't be the counter leg of a payment. It isn't even
    offered for a debit, so naming it anyway is rejected."""
    monkeypatch.setattr(mod, "_ask", _fake_ask('{"1": "4110"}'))
    assert _run(db, [("r1", "paid something", True)]) == {}


def test_a_non_string_answer_is_ignored(db, monkeypatch):
    monkeypatch.setattr(mod, "_ask", _fake_ask('{"1": 6130}'))
    assert _run(db, [("r1", "x", True)]) == {}


def test_answers_for_rows_that_were_not_asked_are_ignored(db, monkeypatch):
    monkeypatch.setattr(mod, "_ask", _fake_ask('{"1": "6130", "7": "6110"}'))
    out = _run(db, [("r1", "x", True)])
    assert set(out) == {"r1"}


# ---------------------------------------------------------------------------
# Failure must degrade, never break
# ---------------------------------------------------------------------------
def test_a_backend_error_yields_no_suggestions(db, monkeypatch):
    async def _boom(accounts, narrations):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mod, "_ask", _boom)
    assert _run(db, [("r1", "x", True)]) == {}


def test_unparseable_output_yields_no_suggestions(db, monkeypatch):
    monkeypatch.setattr(mod, "_ask", _fake_ask("I think it's probably transport?"))
    assert _run(db, [("r1", "x", True)]) == {}


def test_partial_results_survive_a_later_failure(db, monkeypatch):
    """Debits answered, then the credit call dies: keep what we already have."""
    calls = {"n": 0}

    async def _ask(accounts, narrations):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"1": "6130"}
        raise RuntimeError("backend died")

    monkeypatch.setattr(mod, "_ask", _ask)
    out = _run(db, [("r1", "spend", True), ("r2", "receive", False)])
    assert set(out) == {"r1"}


def test_large_imports_are_chunked(db, monkeypatch):
    sizes: list[int] = []

    async def _ask(accounts, narrations):
        sizes.append(len(narrations))
        return {}

    monkeypatch.setattr(mod, "_ask", _ask)
    items = [(f"r{i}", f"merchant {i}", True) for i in range(mod.MAX_ROWS_PER_CALL + 5)]
    _run(db, items)
    assert sizes == [mod.MAX_ROWS_PER_CALL, 5]


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ('{"1": "6130"}', {"1": "6130"}),
    ('```json\n{"1": "6130"}\n```', {"1": "6130"}),
    ('<think>hmm, a taxi</think>{"1": "6130"}', {"1": "6130"}),
    ('Sure! Here you go: {"1": "6130"} hope that helps', {"1": "6130"}),
    ("no json here", {}),
    ('{"1": "6130"', {}),          # truncated
    ('["6130"]', {}),              # not an object
])
def test_reply_extraction(raw, expected):
    assert mod._extract_json_object(raw) == expected


def test_prompt_lists_codes_and_numbered_narrations(db):
    accounts = mod._candidates(db, "EXPENSE")
    prompt = mod._build_prompt(accounts[:2], ["اسنپ", "TESCO"])
    assert "1. اسنپ" in prompt and "2. TESCO" in prompt
    assert accounts[0].code in prompt


def test_candidate_list_is_capped(db, monkeypatch):
    monkeypatch.setattr(mod, "MAX_CANDIDATE_ACCOUNTS", 3)
    assert len(mod._candidates(db, "EXPENSE")) == 3

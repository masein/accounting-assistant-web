"""Money changed from one currency to another, and the owner's phone on 2026-10-22 (scenarios AZ1–AZ5).

«۱۹۰۰ دلار از کیف پول وال ای چنج کردم که تبدیل شد به ۷۰۲.۲ ریال عمان» had no card: «کیف پول والای» kept its
$2,400 and went to −389.935 OMR as the gym, the gym clothes and a transfer were paid from it."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_eval.fixture import GREEN_WALLET, WALL_E, seed_personal_books

USER = "u-exchange"


@pytest.fixture()
def person(db):
    """The eval person: «کیف پول والای» holds 2,400 dollars and 1,785 dirhams, no Omani rials."""
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _ctx(db, message="", session=None):
    return ToolContext(db=db, user_id=USER, username="me", user_message=message, mode="personal",
                       chat_session_id=str(session) if session else None)


def _held(db, code) -> dict[str, Decimal]:
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    rows = db.execute(select(Transaction.currency, func.sum(TransactionLine.debit - TransactionLine.credit))
                      .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
                      .join(Account, Account.id == TransactionLine.account_id)
                      .where(Account.code == code, Transaction.deleted_at.is_(None))
                      .group_by(Transaction.currency)).all()
    return {c: Decimal(str(v)) for c, v in rows if v}


def _exchange(db, **kw):
    from app.services.ai_accountant.money_places import ProposeCurrencyExchange, ProposeCurrencyExchangeInput
    args = {"from_place": "کیف پول والای", "from_currency": "USD", "from_amount": "1900", "to_currency": "OMR",
            "to_amount": "702.2", **kw}
    return asyncio.run(ProposeCurrencyExchange().run(_ctx(db, "۱۹۰۰ دلار از کیف پول والای چنج کردم شد ۷۰۲.۲ ریال عمان",
                                                          kw.pop("session", None)),
                                                     ProposeCurrencyExchangeInput(**args)))


def test_az1_an_exchange_is_one_card_and_two_entries(db, person):
    from app.services.ai_accountant.execute_service import execute_proposal, undo_action
    from app.services.net_worth_service import compute_net_worth
    with use_company(person.company_id):
        card = _exchange(db)
        p = card["preview"]
        assert (p["from_code"], p["to_code"], p["from_currency"], p["to_currency"]) == (WALL_E, WALL_E, "USD", "OMR")
        assert p["description"].startswith("تبدیل ") and "ریال عمان" in p["description"] and "کیف پول والای" in p["description"]
        worth = {c: compute_net_worth(db, currency=c).net_worth for c in ("USD", "OMR")}
        done = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id=USER)
        held = _held(db, WALL_E)
        assert held == {"USD": Decimal("500"), "AED": Decimal("1785"), "OMR": Decimal("702.2")}
        assert _held(db, "3120") == {"USD": Decimal("1900"), "OMR": Decimal("-702.2")}   # «تبدیل ارز», net worth
        assert compute_net_worth(db, currency="USD").net_worth == worth["USD"] - 1900
        assert compute_net_worth(db, currency="OMR").net_worth == worth["OMR"] + Decimal("702.2")
        undone = undo_action(db, audit_log_id=done.audit_log_id, actor_user_id=USER)
        assert undone.mode == "deleted"
        assert _held(db, WALL_E) == {"USD": Decimal("2400"), "AED": Decimal("1785")}   # both entries taken back


def test_az1_spending_does_not_move(db, person):
    from app.services.ai_accountant.execute_service import execute_proposal
    from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
    with use_company(person.company_id):
        before = asyncio.run(GetSpendingSummary().run(_ctx(db), GetSpendingSummaryInput(days=30)))
        execute_proposal(db, confirmation_token=_exchange(db)["confirmation_token"], actor_user_id=USER)
        after = asyncio.run(GetSpendingSummary().run(_ctx(db), GetSpendingSummaryInput(days=30)))
    assert after["total"] == before["total"] and after.get("by_currency") == before.get("by_currency")


def test_az1_one_currency_is_a_move_not_an_exchange(db, person):
    with use_company(person.company_id), pytest.raises(ToolError) as e:
        _exchange(db, to_currency="USD", to_amount="1900")
    assert e.value.code == "same_currency"


def _pay(db, lines, currency, *, message="", session=None):
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    return asyncio.run(ProposeCreateTransaction().run(_ctx(db, message, session), ProposeCreateTransactionInput(
        date=date.today(), description="test", currency=currency,
        lines=[{"account_code": code, "debit": dr, "credit": cr} for code, dr, cr in lines])))


def test_az3_rials_from_a_wallet_of_omani_rials_are_omani_rials(db, person):
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _pay(db, [("6150", 7, 0), (GREEN_WALLET, 0, 7)], "IRR", message="۷.۱ ریال از کیف پول سبز قرص خریدم")
        assert e.value.code == "place_currency" and "OMR" in str(e.value) and "7.100" in str(e.value)
        card = _pay(db, [("6150", "7.1", 0), (GREEN_WALLET, 0, "7.1")], "OMR", message="۷.۱ ریال از کیف پول سبز قرص خریدم")
        assert card["status"] == "pending"


def test_az3_omani_rials_from_a_wallet_of_dollars_need_the_exchange_first(db, person):
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _pay(db, [("6190", 200, 0), (WALL_E, 0, 200)], "OMR")
        assert e.value.code == "place_currency" and "AED, USD" in str(e.value) and "propose_currency_exchange" in str(e.value)
        # AZ2: the exchange card of the same conversation brings the Omani rials
        session = _get_or_create_session(db, user_id=USER, session_id=None).id
        _exchange(db, session=session)
        assert _pay(db, [("6190", 200, 0), (WALL_E, 0, 200)], "OMR", session=session)["status"] == "pending"
        # …in that conversation only
        with pytest.raises(ToolError):
            _pay(db, [("6190", 200, 0), (WALL_E, 0, 200)], "OMR")


def test_az3_money_coming_in_may_be_in_any_currency(db, person):
    with use_company(person.company_id):
        assert _pay(db, [(GREEN_WALLET, 50, 0), ("4140", 0, 50)], "USD")["status"] == "pending"


def test_az5_the_briefing_speaks_the_language_the_person_writes_in(db, person):
    from app.models.ai_accountant import AIChatMessage
    from app.services.ai_accountant.orchestrator import _get_or_create_session, thread_language
    with use_company(person.company_id):
        session = _get_or_create_session(db, user_id=USER, session_id=None)
        assert thread_language(db, session.id, "en") == "en"                    # nothing said yet: the interface
        db.add(AIChatMessage(session_id=session.id, role="user", content={"role": "user", "text": "چقدر پول داریم"}))
        db.commit()
        assert thread_language(db, str(session.id), "en") == "fa"
        assert thread_language(db, None, "en") == "en"


def test_az5_a_persons_books_skip_the_company_audit_checks(db, person, monkeypatch):
    from app.services import insight_service
    ran = []

    def detector(name):
        def run(_db, _today):
            ran.append(name)
            return []
        return run
    names = ("duplicate_payment", "just_under_threshold", "new_vendor_large", "round_weekend", "category_drift",
             "reversal_pattern")
    monkeypatch.setattr(insight_service, "all_detectors", lambda: [(n, detector(n)) for n in names])
    with use_company(person.company_id):
        insight_service.compute_insights(db, use_cache=False)
    assert ran == ["category_drift", "reversal_pattern"]
    ran.clear()
    insight_service.compute_insights(db, use_cache=False)                       # a company's books: all of them
    assert ran == list(names)


def test_az1_the_exchange_is_offered_with_the_money_tools():
    from app.services.ai_accountant import tool_routing as r
    groups = r.route("۱۹۰۰ دلار رو چنج کردم شد ۷۰۲.۲ ریال عمان", personal=True)
    assert "money" in groups and "propose_currency_exchange" in r.GROUPS["money"][1]
    assert "money" in r.route("I changed 500 dollars into 192 OMR", personal=True)


def test_az2_rials_from_the_wallet_the_dollars_were_changed_in_are_omani_rials(db, person):
    from app.services.ai_accountant.orchestrator import _get_or_create_session
    with use_company(person.company_id):
        session = _get_or_create_session(db, user_id=USER, session_id=None).id
        _exchange(db, session=session)
        with pytest.raises(ToolError) as e:
            _pay(db, [("6190", 200, 0), (WALL_E, 0, 200)], "IRR", session=session)
        assert "means OMR — not a question for the user" in str(e.value)


def test_az2_a_refused_card_beside_another_is_drafted_again(db, person):
    """The exchange's card stood, the gym's was refused in Iranian rials: the model is told once more."""
    from app.services.ai_accountant.orchestrator import run_chat_turn
    from tests.test_ai_accountant_orchestrator import _assistant_text, _assistant_tool_call
    seen = []

    class _Model:
        shape = "fake"

        def __init__(self, *replies):
            self.replies = list(replies)

        async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
            seen.append([m.text for m in messages if m.role == "user"])
            return self.replies.pop(0)

    gym = {"date": date.today().isoformat(), "description": "باشگاه",
           "lines": [{"account_code": "6190", "debit": 200, "credit": 0}, {"account_code": WALL_E, "debit": 0, "credit": 200}]}
    model = _Model(
        _assistant_tool_call("propose_currency_exchange", {"from_place": "کیف پول والای", "from_currency": "USD",
                                                           "from_amount": "1900", "to_currency": "OMR", "to_amount": "702.2"}),
        _assistant_tool_call("propose_create_transaction", {**gym, "currency": "IRR"}),
        _assistant_text("کارت تبدیل آماده است؛ ۲۰۰ ریال را به ریال عمان ثبت کنم؟"),
        _assistant_tool_call("propose_create_transaction", {**gym, "currency": "OMR"}),
        _assistant_text("دو کارت آماده است."))
    with use_company(person.company_id):
        out = asyncio.run(run_chat_turn(db, user_id=USER, user_message="۱۹۰۰ دلار چنج کردم شد ۷۰۲.۲ ریال عمان که ازش ۲۰۰ ریال باشگاه دادم",
                                        client=model, mode="personal", lang="fa"))
    assert [p["tool_name"] for p in out.proposals] == ["propose_currency_exchange", "propose_create_transaction"]
    assert any("The card you drafted was refused" in (t or "") for t in seen[-2])

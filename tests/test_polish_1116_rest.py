"""The rest of the chat review's small issues (2026-11-10, item 25, scenarios CI1–CI4). Names and figures made up.

CI1  Omani rials in a reply to their three places, as the app's own figures are.
CI2  A name the tools gave, copied with a letter dropped or moved, is the name.
CI3  English typed with the Persian keyboard layout is read as English.
CI4  An entry a confirmed card records, an undo, a reversal: each leaves a version in the entry's history.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIProposal
from app.models.audit_log import TransactionVersion
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.keyboard_layout import as_english
from app.services.ai_accountant.name_spelling import as_given
from app.services.ai_accountant.orchestrator import _currency_places


# --- CI1 ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("said, shown", [
    ("کارت ثبت ۱٫۷ ریال عمان آماده است", "کارت ثبت ۱٫۷۰۰ ریال عمان آماده است"),
    ("۶۲۹ ریال عمان از شرکت", "۶۲۹٫۰۰۰ ریال عمان از شرکت"),
    ("۱,۲۰۰ ریال عمان و ۲,۰۰۰ دلار آمریکا", "۱,۲۰۰٫۰۰۰ ریال عمان و ۲,۰۰۰ دلار آمریکا"),   # dollars as they are
    ("the receipt for 59.90 OMR is ready", "the receipt for 59.900 OMR is ready"),
    ("OMR 12.5 in all", "OMR 12.500 in all"),
    ("3 Omani rials", "3.000 Omani rials"),
    ("۴٫۵ دینار کویت", "۴٫۵۰۰ دینار کویت"),
    ("۹۸۷٫۵۹۰ ریال عمان", "۹۸۷٫۵۹۰ ریال عمان"),                       # already three places
    ("۱۲٫۳۴۵۶ ریال عمان", "۱۲٫۳۴۵۶ ریال عمان"),                       # more: left alone, never cut
    ("۵۰ ریال و ۵۰۰,۰۰۰ ریال ایران", "۵۰ ریال و ۵۰۰,۰۰۰ ریال ایران"),   # rials of Iran have no places
])
def test_ci1_omani_rials_to_three_places(said, shown):
    assert _currency_places(said) == shown


# --- CI2 ------------------------------------------------------------------------------------------------------

_LOG = [{"result": {"accounts": [{"account_name": "بانک آینده نیلوفر — bank account", "balance": 12},
                                 {"account_name": "بانک دی نارنجستان"}, {"account_name": "بانک ملت"},
                                 {"label": "کیف پول سبز"}]}}]


@pytest.mark.parametrize("said, shown", [
    ("موجودی «آینه نیلوفر» ۱۲ ریال است.", "موجودی «آینده نیلوفر» ۱۲ ریال است."),       # a letter dropped
    ("بانک دی نارنجتسان: ۵", "بانک دی نارنجستان: ۵"),                                 # a letter moved
    ("حساب آیندده نیلوفر", "حساب آینده نیلوفر"),                                       # a letter too many
    ("بانک ملی را ندارید", "بانک ملی را ندارید"),                                     # another letter: another bank
    ("کیف پولم سبز است", "کیف پولم سبز است"),                                         # Persian's own suffix
    ("کیف پول سبزی", "کیف پول سبزی"),
    ("بانک دی نارنجستان درست است", "بانک دی نارنجستان درست است"),
])
def test_ci2_names_as_the_tools_gave_them(said, shown):
    assert as_given(said, _LOG) == shown


def test_ci2_no_names_no_change():
    assert as_given("آینه نیلوفر", []) == "آینه نیلوفر"


# --- CI3 ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, english", [
    ("یثغ ذشممشدزث", "dey ballance"),                   # the owner's own message, 2026-09-19
    ("اخص پعزا پخدثغ یهی ه سحثدی", "how much money did i spend"),
    ("ذشمشدزث", "balance"),
])
def test_ci3_english_on_the_persian_layout(typed, english):
    assert as_english(typed) == english


@pytest.mark.parametrize("persian", [
    "سلام", "موجودی بانک دی", "تاکسی", "امروز صبح ۱۶ ریال بابت سیم کارت دادم", "خب ولش", "هربار چیا بوده",
    "توی هفته گذشته منظورمه", "", "dey balance",
])
def test_ci3_persian_is_persian(persian):
    assert as_english(persian) is None


def test_ci3_the_model_is_told_what_it_says(db):
    from app.services.ai_accountant.orchestrator import run_chat_turn
    from tests.test_ai_accountant_orchestrator import _FakeClient, _assistant_text

    client = _FakeClient([_assistant_text("موجودی بانک دی …")])
    out = asyncio.run(run_chat_turn(db, user_id="u-ci3", user_message="یثغ ذشممشدزث", client=client))
    sent = client.sent[0][-1].to_dict()["text"]
    assert sent.startswith("یثغ ذشممشدزث") and 'Persian keyboard layout on: "dey ballance"' in sent
    stored = db.execute(select(AIChatMessage).where(AIChatMessage.session_id == uuid.UUID(out.session_id),
                                                    AIChatMessage.role == "user")).scalars().one()
    assert stored.content["said"] == "یثغ ذشممشدزث"      # the conversation shows what was typed


def test_ci1_the_reply_is_written_to_places(db):
    from app.services.ai_accountant.orchestrator import run_chat_turn
    from tests.test_ai_accountant_orchestrator import _FakeClient, _assistant_text

    client = _FakeClient([_assistant_text("کارت ۱٫۷ ریال عمان برای تاکسی آماده است.")])
    out = asyncio.run(run_chat_turn(db, user_id="u-ci1", user_message="تاکسی ۱.۷ ریال عمان", client=client))
    assert "۱٫۷۰۰ ریال عمان" in out.text


# --- CI4 ------------------------------------------------------------------------------------------------------

USER = "u-ci4"


@pytest.fixture()
def _clean(db: Session):
    yield
    db.execute(delete(TransactionVersion))
    db.commit()


def _card(db: Session) -> str:
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    ctx = ToolContext(db=db, user_id=USER, username="tester", user_message="paid the cleaner")
    out = asyncio.run(ProposeCreateTransaction().run(ctx, ProposeCreateTransactionInput(
        date="2026-05-20", description="Cleaner CI4", currency="IRR",
        lines=[{"account_code": "6110", "debit": 730_000, "credit": 0},
               {"account_code": "1110", "debit": 0, "credit": 730_000}], entity_links=[])))
    return out["confirmation_token"]


def _versions(db: Session, txn_id: str) -> list[tuple[int, str]]:
    rows = db.execute(select(TransactionVersion).where(TransactionVersion.transaction_id == txn_id)
                      .order_by(TransactionVersion.version)).scalars().all()
    return [(v.version, v.action) for v in rows]


def test_ci4_a_confirmed_card_and_its_undo_have_versions(db, _clean):
    from app.services.ai_accountant.execute_service import execute_proposal, undo_action
    done = execute_proposal(db, confirmation_token=_card(db), actor_user_id=USER, actor_username="tester")
    assert _versions(db, done.transaction_id) == [(1, "create")]
    snap = json.loads(db.execute(select(TransactionVersion.snapshot).where(
        TransactionVersion.transaction_id == done.transaction_id)).scalar())
    assert snap["description"] == "Cleaner CI4" and {ln["account_code"] for ln in snap["lines"]} == {"6110", "1110"}
    v = db.execute(select(TransactionVersion).where(TransactionVersion.transaction_id == done.transaction_id)).scalar()
    assert v.user_id == USER and v.username == "tester"
    undo_action(db, audit_log_id=done.audit_log_id, actor_user_id=USER, actor_username="tester")
    assert _versions(db, done.transaction_id) == [(1, "create"), (2, "delete")]


def test_ci4_a_reversal_versions_the_reversing_entry(db, _clean):
    from app.services.ai_accountant.execute_service import execute_proposal, reverse_action
    done = execute_proposal(db, confirmation_token=_card(db), actor_user_id=USER, actor_username="tester")
    back = reverse_action(db, audit_log_id=done.audit_log_id, actor_user_id=USER, actor_username="tester")
    if back.reversal_transaction_id:
        assert _versions(db, back.reversal_transaction_id) == [(1, "create")]
    else:                                                    # a soft delete, as the undo
        assert _versions(db, done.transaction_id)[-1][1] == "delete"


def test_ci4_a_cancelled_card_leaves_no_version(db, _clean):
    from app.services.ai_accountant.execute_service import cancel_proposal
    token = _card(db)
    cancel_proposal(db, confirmation_token=token, actor_user_id=USER)
    assert db.execute(select(TransactionVersion)).scalars().all() == []
    assert db.execute(select(AIProposal.status).where(AIProposal.confirmation_token == uuid.UUID(token))).scalar() == "cancelled"

"""A lunch with a friend, from the phone (scenarios AK1–AK8).

From the owner's phone, in their personal books, which count in Omani rials: «ناهار با امیر ۷ ریال عمان از کیف پول
قشنگم» (2026-10-20). The card posted 7.000 OMR to 6110 «خوراک و سوپرمارکت» from 112001 «کیف پول سبز»; the reply said
Amir had been added as a new counterparty, though the card named no one, and that nothing is posted until the card
is confirmed, under a card stamped Posted. «کیف پول قشنگم» is the green wallet's new name, which the assistant had
only remembered as a wording: no tool renamed the place itself. The model here is scripted as that one answered."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures
from tests.test_personal_people import _a_list_is_many_requests, _card, _confirm, _say  # noqa: F401


def _in(cid):
    from app.db.tenant import use_company
    return use_company(cid)


def _wallet(db, cid, name="کیف پول سبز") -> str:
    from app.services.chart_service import create_account
    with _in(cid):
        code = create_account(db, name=name, parent_code="1120").code
        db.commit()
    return code


def _stored(db, cid, token):
    from app.models.ai_accountant import AIProposal
    with _in(cid):
        db.expire_all()
        return db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalar_one().tool_input


def _people(db, cid, name):
    from app.models.entity import Entity
    with _in(cid):
        db.expire_all()
        return db.execute(select(Entity).where(Entity.name == name)).scalars().all()


def _proposal(body):
    return next(b for b in body["blocks"] if b["type"] == "proposal")


# --- AK1: a meal out is dining out, even in books that never used it -----------------------------------------

def test_ak1_lunch_is_dining_out_in_books_full_of_groceries(person, db):
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    from app.services.ledger_posting import create_transaction_from_payload
    wallet = _wallet(db, person["cid"])
    with _in(person["cid"]):
        for i in range(16):                                   # 32 lines: books past the "established" mark
            create_transaction_from_payload(db, TransactionCreate(
                date="2026-10-01", description=f"سوپرمارکت {i}", lines=[
                    TransactionLineCreate(account_code="6110", debit=2, credit=0),
                    TransactionLineCreate(account_code=wallet, debit=0, credit=2)]))
        db.commit()
        ctx = ToolContext(db=db, user_id="u", username="me", mode="personal")

        def top(q):
            return asyncio.run(SearchAccounts().run(ctx, SearchAccountsInput(query=q)))["matches"][0]["code"]
        assert top("ناهار") == "6180" and top("lunch") == "6180" and top("شام") == "6180" and top("کافه") == "6180"
        assert top("سوپرمارکت") == "6110" and top("میوه") == "6110"          # food for home stays groceries


# --- AK2: someone the user was with is no party ---------------------------------------------------------------

def test_ak2_a_lunch_with_amir_names_no_one(person, scripted, db):
    wallet = _wallet(db, person["cid"])
    card = _card("ناهار با امیر", "6180", wallet, "7", new={"name": "امیر", "type": "client"}, currency="OMR")
    body, phone = _say(person, scripted, "ناهار با امیر ۷ ریال عمان از کیف پول سبز", card, text="کارت آماده است.")
    block = _proposal(body)
    assert block["people"] == [] and block["new_entities"] == []
    stored = _stored(db, person["cid"], block["token"])
    assert stored["new_entities"] == [] and stored.get("entity_links", []) == []
    _confirm(person, phone, block["token"])
    assert _people(db, person["cid"], "امیر") == []                 # not in People for a lunch he only shared


def test_ak2_amir_paying_still_names_him(person, scripted, db):
    card = _card("ناهار با امیر — امیر حساب کرد", "6180", "2130", "7", new={"name": "امیر", "type": "supplier"},
                 currency="OMR")
    body, _phone = _say(person, scripted, "ناهار با امیر، امیر حساب کرد ۷ ریال عمان", card, text="کارت آماده است.")
    assert _proposal(body)["people"] == [{"name": "امیر", "new": True}]       # money is owed: he's on it


@pytest.mark.parametrize("words, name, alone", [
    ("ناهار با امیر", "امیر", True),
    ("dinner with Sara and Ali", "Ali", True),
    ("شام با امیر و سارا", "سارا", True),
    ("امیر حساب کرد، ناهار با امیر", "امیر", False),     # he paid
    ("paid Amir back", "Amir", False),
    ("ناهار", "امیر", False),                           # not named at all
])
def test_who_is_only_company(words, name, alone):
    from app.services.ai_accountant.orchestrator import _only_with
    assert _only_with([words], name) is alone


# --- AK3: one Amir --------------------------------------------------------------------------------------------

def test_ak3_amir_already_in_the_books_is_linked_not_made_again(person, scripted, db):
    wallet = _wallet(db, person["cid"])
    body, phone = _say(person, scripted, "امیر پول بلیط رو داد ۵۰ ریال عمان",
                       _card("بلیط — امیر حساب کرد", "6185", "2130", "50", new={"name": "امیر", "type": "supplier"},
                             currency="OMR"), text="کارت آماده است.")
    _confirm(person, phone, _proposal(body)["token"])
    [amir] = _people(db, person["cid"], "امیر")
    # the model makes him again, as another type: «پول امیر رو دادم»
    body, phone = _say(person, scripted, "۲۰ ریال عمان از پول امیر رو از کیف پول دادم",
                       _card("پرداخت به امیر", "2130", wallet, "20", new={"name": "امیر", "type": "client"},
                             currency="OMR"), text="کارت آماده است.")
    block = _proposal(body)
    assert block["people"] == [{"name": "امیر", "new": False}]
    stored = _stored(db, person["cid"], block["token"])
    assert stored["new_entities"] == [] and [e["entity_id"] for e in stored["entity_links"]] == [str(amir.id)]
    _confirm(person, phone, block["token"])
    assert len(_people(db, person["cid"], "امیر")) == 1


# --- AK5, AK6: a wallet renamed, the account itself ------------------------------------------------------------

def test_ak5_a_wallet_is_renamed_with_its_entries(person, scripted, db):
    from app.models.account import Account
    wallet = _wallet(db, person["cid"])
    body, phone = _say(person, scripted, "اسم کیف پول سبز رو بکن کیف پول قشنگم",
                       ("propose_rename_place", {"place": "کیف پول سبز", "new_name": "کیف پول قشنگم"}),
                       text="کارت آماده است.")
    block = _proposal(body)
    assert block["title"] == "کیف پول سبز ← کیف پول قشنگم" and block["lines"] == []
    _confirm(person, phone, block["token"])
    with _in(person["cid"]):
        db.expire_all()
        assert db.execute(select(Account.name).where(Account.code == wallet)).scalar_one() == "کیف پول قشنگم"
    # a card on it now says the one name
    body, _phone = _say(person, scripted, "ناهار ۷ ریال عمان از کیف پول قشنگم",
                        _card("ناهار", "6180", wallet, "7", currency="OMR"), text="کارت آماده است.")
    assert [ln["name"] for ln in _proposal(body)["lines"] if ln["account"] == wallet] == ["کیف پول قشنگم"]


def test_ak6_a_name_only_remembered_is_put_right(person, scripted, db):
    from app.models.account import Account
    from app.services.learned_preferences import remember
    wallet = _wallet(db, person["cid"])
    with _in(person["cid"]):
        remember(db, "کیف پول قشنگم", account_code=wallet, source="chat")       # what the rename used to be
        db.commit()
    body, phone = _say(person, scripted, "اسم کیف پول قشنگم رو واقعاً عوض کن",
                       ("propose_rename_place", {"place": "کیف پول قشنگم", "new_name": "کیف پول قشنگم"}),
                       text="کارت آماده است.")
    _confirm(person, phone, _proposal(body)["token"])
    with _in(person["cid"]):
        db.expire_all()
        assert db.execute(select(Account.name).where(Account.code == wallet)).scalar_one() == "کیف پول قشنگم"


def test_a_rename_to_another_places_name_or_its_own_is_refused(person, db):
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.money_places import ProposeRenamePlace, ProposeRenamePlaceInput
    _wallet(db, person["cid"])
    _wallet(db, person["cid"], name="گاوصندوق")
    with _in(person["cid"]):
        ctx = ToolContext(db=db, user_id="u", username="me", mode="personal")
        for place, new, code in (("کیف پول سبز", "گاوصندوق", "name_taken"), ("کیف پول سبز", "کیف پول سبز", "same_name"),
                                 ("کیف پول آبی", "کیف", "no_place")):
            with pytest.raises(ToolError) as e:
                asyncio.run(ProposeRenamePlace().run(ctx, ProposeRenamePlaceInput(place=place, new_name=new)))
            assert e.value.code == code


# --- AK7: a bank party renamed takes its own account with it --------------------------------------------------

def test_ak7_a_bank_party_takes_the_account_named_for_it(person, db):
    from types import SimpleNamespace

    from app.models.account import Account, AccountLevel
    from app.services.ai_accountant.entity_create import rename_bank_account_with
    from app.services.book_text import bt
    with _in(person["cid"]):
        for before, old, after in ((bt(db, "bank_account_name", name="ملت"), "ملت", bt(db, "bank_account_name", name="ملت مرکزی")),
                                   ("ملت", "ملت", "ملت مرکزی"),
                                   ("بانک ملت شعبه ۱۲ حساب ۱۲۳", "ملت", "بانک ملت شعبه ۱۲ حساب ۱۲۳")):
            code = f"1110{uuid.uuid4().int % 10_000:04d}"
            db.add(Account(code=code, name=before, level=AccountLevel.DETAIL))
            db.flush()
            rename_bank_account_with(db, SimpleNamespace(type="bank", code=code, name="ملت مرکزی"), old)
            db.flush()                                   # as the rename's own flush does
            assert db.execute(select(Account.name).where(Account.code == code)).scalar_one() == after
        db.rollback()


def test_ak7_a_bank_renamed_on_the_parties_page_takes_its_account(auth_client, db):
    """Found by the deep test of 2026-10-20: the Parties page renamed the bank and left its account
    «حساب بانکی تجارت» — only the chat's card renamed both."""
    from app.models.account import Account
    tag = uuid.uuid4().hex[:6]
    bank = auth_client.post("/entities", json={"type": "bank", "name": f"تجارت {tag}"}).json()
    name = lambda: db.execute(select(Account.name).where(Account.code == bank["code"])).scalar_one()   # noqa: E731
    before = name()
    assert f"تجارت {tag}" in before
    assert auth_client.patch(f"/entities/{bank['id']}", json={"name": f"تجارت مرکزی {tag}"}).status_code == 200
    db.expire_all()
    assert name() == before.replace(f"تجارت {tag}", f"تجارت مرکزی {tag}")
    assert auth_client.patch(f"/entities/{bank['id']}", json={"notes": "شعبه مرکزی"}).status_code == 200
    db.expire_all()
    assert f"تجارت مرکزی {tag}" in name()                                       # nothing else renames it


# --- AK8: the reply doesn't go stale --------------------------------------------------------------------------

def test_ak8_the_reply_is_not_told_to_say_nothing_is_recorded_yet():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, SYSTEM_PROMPT
    assert "nothing is recorded until they confirm it on the card" not in SYSTEM_PROMPT
    assert "propose_rename_place" in PERSONAL_MODE_ADDENDUM and "NOT A PARTY" in PERSONAL_MODE_ADDENDUM

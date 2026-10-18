"""Money between you and a person, in a person's books (scenarios AG1–AG8).

From the owner's own personal books: «Amir payed for these:», nine lines from
«$333.77 - Muscat Airbnb» to «$350.00 - Yerevan Airbnb», and «i should pay him
back someday keep track of the payments». With the real model: nine cards in
USD, each owed to Amir, but only the first made and named Amir; the other eight
named no one, nor did «I paid Amir back 1000 dollars in cash»; «how much do I
owe Amir?» was read off the whole «بدهی به دیگران» account; the stays went under
«متفرقه»; and the confirmed cards were still «pending» to the assistant. The
model here is scripted to answer as that one did."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from tests.test_ai_guardrails import _calls, _text
from tests.test_personal_money import API, person, scripted  # noqa: F401 — fixtures

ROOT = Path(__file__).resolve().parents[1]
AMIR = [("333.77", "Muscat Airbnb"), ("851.06", "Muscat Airbnb"), ("525.63", "Muscat tickets"),
        ("727.97", "Kandy Airbnb"), ("630.00", "Ahangama Airbnb"), ("315.00", "Ahangama Airbnb"),
        ("331.63", "Wattala Airbnb"), ("490.00", "Yerevan Airbnb"), ("350.00", "Yerevan Airbnb")]
MESSAGE = ("Amir payed for these:\n\n" + "\n".join(f"${a} - {w}" for a, w in AMIR)
           + "\n\ni should pay him back someday keep track of the payments")


def _card(desc, dr, cr, amount, *, new=None, currency="USD"):
    body = {"date": "2026-10-18", "description": desc, "currency": currency,
            "lines": [{"account_code": dr, "debit": amount, "credit": 0},
                      {"account_code": cr, "debit": 0, "credit": amount}]}
    if new:
        body["new_entities"] = [new]
    return ("propose_create_transaction", body)


def _say(person, scripted, message, *cards, text="Done."):
    scripted(_calls(*cards), _text(text))
    phone = person["phone"]()
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json(), phone


def _confirm(person, phone, token):
    r = person["client"].post(f"{API}/proposals/{token}/confirm", headers=phone, json={})
    assert r.status_code == 200, r.text
    return r.json()


def _balances(db, cid, who=None):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.personal_tools import GetPeopleBalances, GetPeopleBalancesInput
    with use_company(cid):
        return asyncio.run(GetPeopleBalances().run(ToolContext(db=db, user_id="u", username="me", mode="personal"),
                                                   GetPeopleBalancesInput(person=who)))


def _amirs_nine(person, scripted):
    """The model's answer to the owner's message: the first card makes Amir, the other eight only name him."""
    amir = {"name": "Amir", "type": "supplier", "role": "payee"}
    cards = [_card(f"{w} — paid by Amir", "6185", "2130", a, new=amir if i == 0 else None)
             for i, (a, w) in enumerate(AMIR)]
    return _say(person, scripted, MESSAGE, *cards, text="Nine cards are ready, each owed to Amir: 4,555.06 USD.")


# --- AG1, AG2: every card is Amir's, in whatever order they're confirmed ----------------------------------------

def test_ag1_ag2_every_card_names_amir_and_one_amir_is_made_in_any_order(person, scripted, db):
    body, phone = _amirs_nine(person, scripted)
    cards = [b for b in body["blocks"] if b["type"] == "proposal"]
    assert len(cards) == 9
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    with use_company(person["cid"]):
        stored = {str(p.confirmation_token): p.tool_input for p in db.execute(select(AIProposal)).scalars()}
    for c in cards:
        assert [(e["name"], e["type"]) for e in stored[c["token"]]["new_entities"]] == [("Amir", "supplier")], c
    assert all(stored[c["token"]]["new_entities"][0].get("shared") for c in cards[1:])   # «made once» on the card
    for c in reversed(cards):                                   # the last first
        _confirm(person, phone, c["token"])
    from app.models.entity import Entity, TransactionEntity
    from app.models.transaction import Transaction
    with use_company(person["cid"]):
        db.expire_all()
        amirs = db.execute(select(Entity).where(Entity.name == "Amir")).scalars().all()
        assert len(amirs) == 1
        linked = db.execute(select(TransactionEntity.transaction_id).where(
            TransactionEntity.entity_id == amirs[0].id)).scalars().all()
        txns = db.execute(select(Transaction).where(Transaction.deleted_at.is_(None))).scalars().all()
    assert len(linked) == 9 and set(linked) == {t.id for t in txns}


# --- AG3, AG4: what I owe each person, each in its currency --------------------------------------------------

def test_ag3_ag4_how_much_i_owe_amir_and_sara_each_on_their_own(person, scripted, db):
    body, phone = _amirs_nine(person, scripted)
    for c in [b for b in body["blocks"] if b["type"] == "proposal"]:
        _confirm(person, phone, c["token"])
    assert _balances(db, person["cid"], "Amir")["people"][0]["you_owe"] == {"USD": Decimal("4555.06")}
    body, phone = _say(person, scripted, "Sara paid 40 dollars for my taxi",
                       _card("Taxi — paid by Sara", "6130", "2130", "40", new={"name": "Sara", "type": "supplier"}))
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    assert _balances(db, person["cid"], "Amir")["people"][0]["you_owe"] == {"USD": Decimal("4555.06")}
    everyone = _balances(db, person["cid"])
    assert [(p["person"], p["you_owe"]) for p in everyone["people"]] == [
        ("Amir", {"USD": Decimal("4555.06")}), ("Sara", {"USD": 40})]
    assert everyone["not_tied_to_anyone"] == {"you_owe": {}, "owes_you": {}}
    # the assistant has the tool and is told to use it, never the whole account
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, build_personal_registry
    assert build_personal_registry().get("get_people_balances") is not None
    assert "never read the whole 2130 or 1150 account as one person's" in PERSONAL_MODE_ADDENDUM


# --- AG5: paying him back names him -----------------------------------------------------------------------------

def test_ag5_paying_amir_back_names_him(person, scripted, db):
    body, phone = _amirs_nine(person, scripted)
    for c in [b for b in body["blocks"] if b["type"] == "proposal"]:
        _confirm(person, phone, c["token"])
    # as the real model answered: no link, Amir only in the words
    body, phone = _say(person, scripted, "I paid Amir back 1000 dollars in cash today",
                       _card("Repayment to Amir — cash", "2130", "1120", "1000"))
    done = _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    assert done["state"] == "posted"
    assert _balances(db, person["cid"], "Amir")["people"][0]["you_owe"] == {"USD": Decimal("3555.06")}


def test_ag5_a_name_inside_another_is_not_taken_for_it(person, scripted, db):
    """«Amir Reza» is not «Amir»: the fuller name named is the one linked."""
    body, phone = _say(person, scripted, "Amir paid my lunch, Amir Reza my dinner",
                       _card("Lunch — paid by Amir", "6180", "2130", "10", new={"name": "Amir", "type": "supplier"}),
                       _card("Dinner — paid by Amir Reza", "6180", "2130", "20",
                             new={"name": "Amir Reza", "type": "supplier"}))
    for c in [b for b in body["blocks"] if b["type"] == "proposal"]:
        _confirm(person, phone, c["token"])
    body, phone = _say(person, scripted, "I paid Amir Reza back 20", _card("Paid Amir Reza back", "2130", "1120", "20"))
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    owed = {p["person"]: p["you_owe"] for p in _balances(db, person["cid"])["people"]}
    assert owed == {"Amir": {"USD": 10}}                       # Amir Reza is settled; Amir still 10


# --- AG6: someone owes me ---------------------------------------------------------------------------------------

def test_ag6_reza_owes_me_then_pays_some_back(person, scripted, db):
    body, phone = _say(person, scripted, "I lent Reza 200 dollars from my card",
                       _card("Lent to Reza", "1150", "1110", "200", new={"name": "Reza", "type": "client"}))
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    assert _balances(db, person["cid"], "Reza")["people"][0]["owes_you"] == {"USD": 200}
    body, phone = _say(person, scripted, "Reza paid me back 50", _card("Reza paid me back", "1110", "1150", "50"))
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    reza = _balances(db, person["cid"], "Reza")["people"][0]
    assert (reza["owes_you"], reza["you_owe"]) == ({"USD": 150}, {})


def test_ag6_a_shop_named_on_a_spending_card_is_not_linked(person, scripted, db):
    """Only money owed between people is linked by name: «Sara's café» on a lunch paid by card stays as it is."""
    body, phone = _say(person, scripted, "Sara paid 5 for my coffee",
                       _card("Coffee — paid by Sara", "6180", "2130", "5", new={"name": "Sara", "type": "supplier"}))
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])
    body, phone = _say(person, scripted, "Lunch at Sara cafe 12 by card", _card("Lunch at Sara cafe", "6180", "1110", "12"))
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    token = next(b for b in body["blocks"] if b["type"] == "proposal")["token"]
    with use_company(person["cid"]):
        row = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(token))).scalar_one()
    assert not row.tool_input.get("entity_links")


# --- AG7: travel in every person's books ------------------------------------------------------------------------

def test_ag7_hotel_airbnb_and_flight_find_travel(person, db):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    with use_company(person["cid"]):
        for words in ("Muscat Airbnb", "hotel", "flight", "هتل", "سفر"):
            out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u", username="me", mode="personal"),
                                                   SearchAccountsInput(query=words)))
            assert out["matches"][0]["code"] == "6185", (words, out["matches"][:3])
        for words in ("excellent coffee", "Stripe fee", "Visa card"):      # no stray word takes them to money owed or travel
            out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u", username="me", mode="personal"),
                                                   SearchAccountsInput(query=words)))
            assert out["matches"][0]["code"] not in ("6185", "1150", "2130"), (words, out["matches"][:3])


def test_ag7_books_kept_from_before_get_travel_once(db):
    """Migration 079: a person's books made before 6185 get «سفر» (English books: Travel), under 61; run twice, once."""
    from app.db.tenant import use_company
    from app.models.account import Account
    from app.services.company_service import provision_company
    from tests.test_admin_audit import _purge_company
    made = []
    for locale in ("default", "uk"):
        company, _user = provision_company(db, name=f"old {locale}", locale=locale, base_currency="IRR",
                                           username=f"old-{uuid.uuid4().hex[:6]}", password="Str0ng-personal-pass!",
                                           kind="personal")
        made.append((str(company.id), locale))
    db.commit()
    try:
        for cid, _locale in made:                               # as they were before: no travel category
            with use_company(cid):
                db.delete(db.execute(select(Account).where(Account.code == "6185")).scalar_one())
                db.commit()
        spec = importlib.util.spec_from_file_location("m079", ROOT / "alembic/versions/079_personal_travel.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        for _ in range(2):                                      # safe to run twice
            with db.get_bind().begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    mod.upgrade()
        db.expire_all()
        for cid, locale in made:
            with use_company(cid):
                travel = db.execute(select(Account).where(Account.code == "6185")).scalars().all()
                assert len(travel) == 1
                assert travel[0].name == ("Travel" if locale == "uk" else "سفر")
                assert travel[0].parent.code == "61" and travel[0].is_active
    finally:
        for cid, _locale in made:
            _purge_company(db, cid)


# --- AG8: the assistant knows what was confirmed ----------------------------------------------------------------

def test_ag8_confirmed_and_cancelled_cards_are_replayed_as_they_stand(person, scripted, db):
    body, phone = _say(person, scripted, "two things",
                       _card("Taxi — paid by Sara", "6130", "2130", "7", new={"name": "Sara", "type": "supplier"}),
                       _card("Bus — paid by Sara", "6130", "2130", "3"),
                       _card("Train — paid by Sara", "6130", "2130", "4"))
    first, second, third = [b["token"] for b in body["blocks"] if b["type"] == "proposal"]
    _confirm(person, phone, first)
    r = person["client"].post(f"{API}/proposals/{second}/cancel", headers=phone, json={})
    assert r.status_code == 200, r.text
    from app.db.tenant import use_company
    from app.services.ai_accountant.orchestrator import _replay_history
    with use_company(person["cid"]):
        history = _replay_history(db, uuid.UUID(body["thread_id"]))
    states = {}
    for m in history:
        if m.role == "tool" and m.text and "confirmation_token" in m.text:
            out = json.loads(m.text)
            states[out["confirmation_token"]] = (out["status"], out["next_steps"])
    assert states[first][0] == "executed" and "in the books now" in states[first][1]
    assert states[second][0] == "cancelled" and "nothing of it was recorded" in states[second][1]
    assert states[third][0] == "pending"                        # still waiting: as it was


def test_ag4_who_do_i_owe_and_who_owes_me_person_by_person(person, scripted, db):
    """The quick answers read a person's people, not a company's invoices: «who do I owe?» said nobody."""
    body, phone = _amirs_nine(person, scripted)
    for c in [b for b in body["blocks"] if b["type"] == "proposal"]:
        _confirm(person, phone, c["token"])
    body, phone = _say(person, scripted, "Sara paid 40 dollars for my taxi",
                       _card("Taxi — paid by Sara", "6130", "2130", "40", new={"name": "Sara", "type": "supplier"}))
    _confirm(person, phone, next(b for b in body["blocks"] if b["type"] == "proposal")["token"])

    def ask(message):
        scripted(_text("MODEL"))                                # the model is never asked these
        r = person["client"].post(f"{API}/chat", headers=phone, json={"message": message})
        assert r.status_code == 200, r.text
        return " ".join(b["text"] for b in r.json()["blocks"] if b["type"] == "text")

    assert ask("who do I owe?") == "You owe Amir 4,555.06 USD, Sara 40.00 USD."
    assert ask("who owes me?") == "Nobody owes you anything."
    assert ask("به کی بدهکارم؟") == ("بدهی‌های شما: \u2068Amir\u2069 ۴٬۵۵۵٫۰۶ دلار آمریکا، "
                                     "\u2068Sara\u2069 ۴۰٫۰۰ دلار آمریکا.")
    one = ask("به Sara چقدر بدهکارم؟")                                                  # the one named, alone
    assert "Sara" in one and "Amir" not in one, one
    assert ask("how much do I owe Amir?") == "MODEL"                                   # not a quick question: the model

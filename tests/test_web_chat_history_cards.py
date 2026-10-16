"""A web conversation opened again keeps its cards (scenarios AE1–AE4).

The web history brought back only the words: every card in a conversation
was gone, the draft, the posted one, the cancelled one, though the phone
redraws them. Each answer now carries the cards that came with it, each as
it stands now."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.tenant import use_company
from tests.test_ai_guardrails import _calls, _text
from tests.test_chat_replies import model  # noqa: F401  (fixture)
from tests.test_personal_money import person  # noqa: F401  (fixture)


def _place(place, amount):
    return ("propose_money_place", {"place": place, "kind": "cash", "currency": "OMR", "amount": amount})


def _chat(web, message, session_id=None):
    r = web.post("/ai-accountant/chat", json={"message": message, "session_id": session_id})
    assert r.status_code == 200, r.text
    return r.json()


def _history(web, sid):
    r = web.get(f"/ai-accountant/sessions/{sid}/messages")
    assert r.status_code == 200, r.text
    return [m for m in r.json() if m["role"] == "assistant" and (m["content"].get("text") or "").strip()]


def test_ae1_a_card_comes_back_under_its_answer_and_still_works(person, model):
    web = person["web"]()
    model(_calls(_place("کیف پول سبز", "77.500")), _text("کارت کیف پول سبز آماده است."))
    first = _chat(web, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم")
    sid, green = first["session_id"], first["proposals"][0]["confirmation_token"]
    model(_calls(_place("کیف پول آبی", "10")), _text("کارت کیف پول آبی آماده است."))
    blue = _chat(web, "۱۰ ریال عمان توی کیف پول آبی", sid)["proposals"][0]["confirmation_token"]

    answers = _history(web, sid)
    assert [[c["confirmation_token"] for c in a["cards"]] for a in answers] == [[green], [blue]]   # each under its own
    card = answers[0]["cards"][0]
    assert card["state"] == "pending" and card["tool_name"] == "propose_money_place"
    assert card["card"]["title"] == "کیف پول سبز — موجودی اولیه"
    assert [ln["name"] for ln in card["card"]["lines"]] == ["کیف پول سبز", "خالص دارایی اولیه"]
    # still pending: Confirm works from the history
    r = web.post("/ai-accountant/execute", json={"confirmation_token": green})
    assert r.status_code == 200, r.text


def test_ae2_each_card_as_it_stands(person, model, db):
    from app.models.ai_accountant import AIProposal
    web = person["web"]()
    model(_calls(_place("کیف پول سبز", "77.500")), _text("آماده است."))
    first = _chat(web, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم")
    sid, posted = first["session_id"], first["proposals"][0]["confirmation_token"]
    assert web.post("/ai-accountant/execute", json={"confirmation_token": posted}).status_code == 200
    model(_calls(_place("کیف پول آبی", "10")), _text("آماده است."))
    cancelled = _chat(web, "۱۰ ریال عمان توی کیف پول آبی", sid)["proposals"][0]["confirmation_token"]
    assert web.post(f"/ai-accountant/proposals/{cancelled}/cancel").status_code == 200
    model(_calls(_place("گاوصندوق", "5")), _text("آماده است."))
    stale = _chat(web, "۵ ریال عمان توی گاوصندوق", sid)["proposals"][0]["confirmation_token"]
    model(_calls(_place("قلک", "3")), _text("آماده است."))
    waiting = _chat(web, "۳ ریال عمان توی قلک", sid)["proposals"][0]["confirmation_token"]
    with use_company(person["cid"]):
        rows = {str(p.confirmation_token): p for p in db.execute(select(AIProposal)).scalars()}
        rows[stale].created_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        rows[waiting].approval_status = "requested"
        db.commit()

    cards = {c["confirmation_token"]: c for a in _history(web, sid) for c in a["cards"]}
    assert cards[posted]["state"] == "executed"
    assert cards[posted]["posted"]["date"]["display"]                  # a starting balance has no reference of its own
    assert cards[posted]["posted"]["undone"] is False
    assert cards[cancelled]["state"] == "cancelled"
    assert cards[stale]["state"] == "expired"
    assert (cards[waiting]["state"], cards[waiting]["approval_status"]) == ("pending", "requested")


def test_ae2_a_corrected_card_comes_back_replaced(person, model):
    """And a person's starting balance can be corrected now: «مبلغش رو ۸۰ کن» left the old card
    confirmable beside the new one (propose_money_place took no ``replaces``)."""
    web = person["web"]()
    model(_calls(_place("کیف پول سبز", "77.500")), _text("آماده است."))
    first = _chat(web, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم")
    sid, old = first["session_id"], first["proposals"][0]["confirmation_token"]
    model(_calls(("propose_money_place", {"place": "کیف پول سبز", "kind": "cash", "currency": "OMR", "amount": "80",
                                          "replaces": "previous"})), _text("درست شد."))
    new = _chat(web, "مبلغش رو ۸۰ کن", sid)["proposals"][0]["confirmation_token"]
    cards = {c["confirmation_token"]: c for a in _history(web, sid) for c in a["cards"]}
    assert cards[old]["state"] == "replaced" and cards[new]["state"] == "pending"


def test_ae4_only_the_users_own_cards(person, model, db):
    from app.models.ai_accountant import AIProposal
    web = person["web"]()
    model(_calls(_place("کیف پول سبز", "77.500")), _text("آماده است."))
    first = _chat(web, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم")
    sid = first["session_id"]
    with use_company(person["cid"]):
        db.add(AIProposal(confirmation_token=uuid.uuid4(), user_id=str(uuid.uuid4()), session_id=sid,
                          tool_name="propose_create_transaction", tool_input={}, user_message="x", status="pending"))
        db.commit()
    cards = [c for a in _history(web, sid) for c in a["cards"]]
    assert [c["confirmation_token"] for c in cards] == [first["proposals"][0]["confirmation_token"]]

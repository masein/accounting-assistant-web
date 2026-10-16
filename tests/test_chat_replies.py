"""A message sent in reply to an earlier one, on the phone and the web (scenarios AC4–AC7).

From the owner: "I want to be able to reply, share or copy a message in the
app's chat and on the web as well." Copy and share stay on the phone and in
the browser; a reply reaches the server: the quoted words go to the model
ahead of the user's own, and stay beside them when the thread is opened again."""
from __future__ import annotations

import uuid
from functools import partial

import pytest

from app.db.tenant import use_company
from tests.test_ai_guardrails import _FakeClient, _calls, _text
from tests.test_personal_money import API, person  # noqa: F401  (fixture)

ANSWER = "۷۷٫۵۰۰ ریال عمان در کیف پول سبز دارید."


@pytest.fixture()
def model(monkeypatch):
    """The real orchestrator with a scripted model; ``model.sent`` keeps the messages of each call."""
    from app.api import ai_accountant as api_mod
    real = api_mod.run_chat_turn
    fake = _FakeClient([])

    def script(*responses):
        fake._queue = list(responses)
        monkeypatch.setattr(api_mod, "run_chat_turn", partial(real, client=fake))
    script.fake = fake
    return script


def _last_user_text(fake) -> str:
    return [m for m in fake.sent[-1] if m.role == "user"][-1].text


def _say(person, phone, message, **extra):
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _card(reply):
    return next(b for b in reply["blocks"] if b["type"] == "proposal")


def _place(place, amount):
    return ("propose_money_place", {"place": place, "kind": "cash", "currency": "OMR", "amount": amount})


def test_ac4_the_model_reads_the_quote_and_the_thread_keeps_it(person, model):
    phone = person["phone"]()
    model(_text(ANSWER))
    first = _say(person, phone, "پول من کجاست؟")
    thread = first["thread_id"]
    model(_text("به دلار حدود ۲۰۰ دلار است."))
    _say(person, phone, "و به دلار؟", thread_id=thread, client_message_id="phone-1",
         reply_to={"role": "assistant", "text": ANSWER})
    said = _last_user_text(model.fake)
    assert said.startswith(f"[The user is replying to your earlier message: «{ANSWER}»]")
    assert said.endswith("و به دلار؟")

    # opened again on the phone: the user's words, the quote above them
    r = person["client"].get(f"{API}/threads/{thread}/messages", headers=phone)
    mine = [m for m in r.json() if m["role"] == "user"]
    assert mine[-1]["text"] == "و به دلار؟"
    assert mine[-1]["reply_to"] == {"role": "assistant", "text": ANSWER}
    assert mine[-1]["client_message_id"] == "phone-1"
    assert "reply_to" not in mine[0]

    # and on the web
    web = person["web"]()
    rows = web.get(f"/ai-accountant/sessions/{thread}/messages").json()
    kept = [m["content"] for m in rows if m["role"] == "user"][-1]
    assert kept["said"] == "و به دلار؟" and kept["reply_to"] == {"role": "assistant", "text": ANSWER}


def test_ac4_a_reply_from_the_web_and_to_ones_own_words(person, model):
    web = person["web"]()
    model(_text("Noted."))
    r = web.post("/ai-accountant/chat", json={"message": "the same for last month",
                                               "reply_to": {"role": "user", "text": "  how much did I spend\non food?  "}})
    assert r.status_code == 200, r.text
    assert _last_user_text(model.fake).startswith(
        "[The user is replying to the user's own earlier message: «how much did I spend on food?»]")


def test_ac4_a_reply_is_the_models_even_when_a_fast_path_knows_the_question(person, model):
    """«how much money do I have?» alone is answered from the books; under a quote it means something more."""
    phone = person["phone"]()
    model()
    _say(person, phone, "how much money do I have?")
    assert model.fake.sent == []                                     # the books answered, no model
    model(_text("In Omani rials, 77.500."))
    _say(person, phone, "how much money do I have?", reply_to={"role": "assistant", "text": "Your wallets: green, blue."})
    assert len(model.fake.sent) == 1


def test_ac5_a_reply_to_a_pending_card_names_that_card(person, model):
    phone = person["phone"]()
    model(_calls(_place("کیف پول سبز", "77.500")), _text("کارت آماده است."))
    green = _card(_say(person, phone, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم"))
    model(_calls(_place("کیف پول آبی", "10")), _text("کارت آماده است."))
    _say(person, phone, "۱۰ ریال عمان توی کیف پول آبی")              # a newer card below it

    model(_text("درست شد."))
    _say(person, phone, "مبلغش رو ۸۰ کن", reply_to={"role": "assistant", "text": green["title"], "token": green["token"]})
    said = _last_user_text(model.fake)
    assert f"confirmation_token {green['token']}, pending" in said
    assert f'replaces="{green["token"]}"' in said


def test_ac5_a_reply_to_a_posted_card_is_a_correction(person, model):
    phone = person["phone"]()
    model(_calls(_place("کیف پول سبز", "77.500")), _text("کارت آماده است."))
    green = _card(_say(person, phone, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم"))
    r = person["client"].post(f"{API}/proposals/{green['token']}/confirm", headers=phone, json={})
    assert r.status_code == 200, r.text
    model(_text("باشد."))
    _say(person, phone, "اشتباه بود، ۸۰ بود", reply_to={"role": "assistant", "text": green["title"], "token": green["token"]})
    said = _last_user_text(model.fake)
    assert "executed" in said and "never a second copy" in said and "replaces=" not in said


def test_ac7_someone_elses_card_is_left_out(person, model, db):
    from app.models.ai_accountant import AIProposal
    theirs = uuid.uuid4()
    with use_company(person["cid"]):
        db.add(AIProposal(confirmation_token=theirs, user_id=str(uuid.uuid4()), tool_name="propose_create_transaction",
                          tool_input={}, user_message="x", status="pending"))
        db.commit()
    phone = person["phone"]()
    model(_text("OK."))
    _say(person, phone, "and this one?", reply_to={"role": "assistant", "text": "Rent 500", "token": str(theirs)})
    said = _last_user_text(model.fake)
    assert str(theirs) not in said and "«Rent 500»" in said
    thread = person["client"].get(f"{API}/threads", headers=phone).json()[0]["id"]
    web = person["web"]()
    kept = [m["content"] for m in web.get(f"/ai-accountant/sessions/{thread}/messages").json() if m["role"] == "user"][-1]
    assert "token" not in kept["reply_to"]


def test_the_quote_is_short_and_an_empty_one_is_none(db):
    from app.services.ai_accountant.replies import QUOTE_CHARS, quoted
    long = quoted(db, user_id="u", role="assistant", text="word " * 300, token=None)
    assert len(long["text"]) <= QUOTE_CHARS + 1 and long["text"].endswith("word…")
    assert quoted(db, user_id="u", role="assistant", text="  \n ", token=None) is None
    assert quoted(db, user_id="u", role="assistant", text="", token="not-a-token") is None
    assert quoted(db, user_id="u", role="system", text="hi", token=None)["role"] == "assistant"

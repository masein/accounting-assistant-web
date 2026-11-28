"""Support from the Telegram/Bale bot (scenario DA9): ``/support`` in a signed-in chat writes to the support desk
instead of the assistant — text, a screenshot, a voice note — into the person's one support conversation; the desk's
reply comes back to that chat: a bot on a webhook sends it at once, the relay fetches it from the outbox; ``/done``
goes back to the assistant."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.tenant import use_company
from app.models.messenger import MessengerOutbox
from app.models.support import SupportMessage, SupportThread
from app.services import messenger
from app.services import support_desk as S
from tests.test_messenger_bot import (  # noqa: F401 — the fixtures, shared
    FakeResult, _link, _msg, _relay, _run, _sent, _start, bot_api, co, fake_turn, relay)


def _thread(db, cid):
    with use_company(cid):
        db.expire_all()
        return db.execute(select(SupportThread)).scalars().first()


def test_da9_support_from_the_chat_and_the_reply_comes_back(co, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    seen = fake_turn(FakeResult("never"))
    _run(db, _msg("/support"))
    assert _sent(calls)[-1]["text"] == messenger._t("en", "support_on")
    _run(db, _msg("The VAT report is empty"))
    assert _sent(calls)[-1]["text"] == messenger._t("en", "support_sent") and seen == []   # not the assistant
    thread = _thread(db, cid)
    assert (thread.channel, thread.chat_platform, thread.chat_id) == ("telegram", "telegram", "5550001")
    with use_company(cid):
        [m] = db.execute(select(SupportMessage)).scalars().all()
    assert m.text == "The VAT report is empty" and m.context["page"] == "bot"
    n = len(_sent(calls))
    S.reply(db, thread_id=str(thread.id), text="Which period?")         # the desk answers: the webhook bot sends it
    assert _sent(calls)[n:] == [{"chat_id": "5550001", "text": "💬 Support: Which period?"}]
    assert db.execute(select(MessengerOutbox.sent_at)).scalar_one() is not None
    _run(db, _msg("/done"))
    assert _sent(calls)[-1]["text"] == messenger._t("en", "support_off")
    fake_turn(FakeResult("Lunch, 20?"))
    _run(db, _msg("paid 20 for lunch"))                                  # the assistant again
    assert _sent(calls)[-1]["text"] == "Lunch, 20?"


def test_da9_a_one_line_support_message_and_a_screenshot(co, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    messenger._TRANSPORT.handler.files["S1"] = b"\xff\xd8\xff\xe0" + b"\x00" * 300
    _link(db, uid)
    fake_turn(FakeResult("never"))
    _run(db, _msg("/support the ledger shows an error"))
    _run(db, _msg(photo=[{"file_id": "S1", "file_size": 304, "width": 800}], caption="this one"))
    with use_company(cid):
        msgs = db.execute(select(SupportMessage).order_by(SupportMessage.created_at)).scalars().all()
    assert [m.text for m in msgs] == ["the ledger shows an error", "this one"] and len(msgs[1].attachment_ids) == 1


def test_da9_through_the_relay_the_reply_waits_in_the_outbox(client, co, relay, bot_api, db, fake_turn):
    cid, uid = co
    calls, _ = bot_api
    out = _start(db, uid)
    _relay(client, relay, "update", {"update": _msg(f"/start {out['code']}", update_id=4900)})
    _relay(client, relay, "update", {"update": _msg("/support the bank page is blank", update_id=4901)})
    thread = _thread(db, cid)
    S.reply(db, thread_id=str(thread.id), text="Fixed — reload the page.")
    r = client.post("/bots/telegram/relay/outbox", headers={"Authorization": f"Bearer {relay}"})
    assert r.json()["calls"] == [{"method": "sendMessage",
                                  "params": {"chat_id": "5550001", "text": "💬 Support: Fixed — reload the page."}}]
    assert client.post("/bots/telegram/relay/outbox", headers={"Authorization": f"Bearer {relay}"}).json()["calls"] == []
    assert client.post("/bots/telegram/relay/outbox", headers={"Authorization": "Bearer nope"}).status_code == 404
    assert calls == []                                                    # the server never called Telegram


def test_da9_a_reply_after_the_person_wrote_from_the_web_stays_in_the_web(client, co, bot_api, db, fake_turn):
    from tests.test_ai_guardrails import _login
    from app.models.user import User
    cid, uid = co
    calls, _ = bot_api
    _link(db, uid)
    fake_turn(FakeResult("never"))
    _run(db, _msg("/support from the chat"))
    user = db.get(User, uuid.UUID(uid))
    _login(client, cid, user).post("/support/messages", json={"text": "and now from the web"})
    thread = _thread(db, cid)
    assert thread.chat_platform is None                                  # the web's: no chat to tell
    n, before = len(_sent(calls)), len(db.execute(select(MessengerOutbox.id)).all())
    S.reply(db, thread_id=str(thread.id), text="Got it")
    assert _sent(calls)[n:] == [] and len(db.execute(select(MessengerOutbox.id)).all()) == before

"""The phone's outbox and catch-up on the server (scenarios N25, N26;
roadmap ROADMAP_ANDROID_CHAT P0.9, P1.5).

A message typed offline is sent again until it is answered, so it must be
answered once however often it arrives; and a phone coming back fetches only
what it hasn't seen."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest

from app.models.mobile_turn import MobileTurn
from app.services import mobile_turns as turns
from tests.test_ai_guardrails import _calls, _FakeClient, _text, _txn, co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def _script(monkeypatch, *responses):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import run_chat_turn
    fake = _FakeClient(list(responses))
    monkeypatch.setattr(api_mod, "run_chat_turn", partial(run_chat_turn, client=fake))
    return fake


def _never(monkeypatch):
    from app.api import ai_accountant as api_mod

    async def never(*a, **kw):
        raise AssertionError("the model was asked twice")
    monkeypatch.setattr(api_mod, "run_chat_turn", never)


def test_n25_the_first_message_of_a_new_conversation_is_answered_once(client, co, phone, monkeypatch):
    _script(monkeypatch, _calls(_txn(5_000_000)), _text("Drafted."))
    body = {"message": "record the rent", "client_message_id": "out-1"}
    a = client.post(f"{API}/chat", headers=phone, json=body).json()
    count = client.get(f"{API}/threads", headers=phone).json()[0]["message_count"]
    _never(monkeypatch)
    b = client.post(f"{API}/chat", headers=phone, json=body).json()        # no thread yet on the phone
    assert b["stop_reason"] == "repeat" and b["thread_id"] == a["thread_id"] and b["blocks"] == a["blocks"]
    threads = client.get(f"{API}/threads", headers=phone).json()
    assert len(threads) == 1 and threads[0]["message_count"] == count      # nothing asked or stored twice
    # the streamed route answers the repeat too, without a step
    r = client.post(f"{API}/chat/stream", headers=phone, json=body)
    assert '"stop_reason": "repeat"' in r.text and "event: status" not in r.text


def test_n25_a_turn_that_failed_runs_again_on_the_next_try(client, co, phone, monkeypatch):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import AIAccountantError

    async def down(*a, **kw):
        raise AIAccountantError("provider unreachable")
    monkeypatch.setattr(api_mod, "run_chat_turn", down)
    body = {"message": "record the rent", "client_message_id": "out-2"}
    r = client.post(f"{API}/chat", headers=phone, json=body)
    assert r.status_code == 502
    _script(monkeypatch, _calls(_txn(5_000_000)), _text("Drafted."))
    again = client.post(f"{API}/chat", headers=phone, json=body).json()
    assert again["stop_reason"] != "repeat" and "proposal" in [b["type"] for b in again["blocks"]]


def test_n25_another_users_same_id_is_their_own(client, db, co, phone, monkeypatch):
    # the id is scoped to its user: the CFO's answered "shared-id" is not the owner's reply
    db.add(MobileTurn(user_id=uuid.UUID(str(co["cfo"].id)), client_message_id="shared-id", state="done",
                      reply={"thread_id": None, "blocks": [{"type": "text", "id": "x", "text": "theirs"}]}))
    db.commit()
    _script(monkeypatch, _text("Yours."))
    r = client.post(f"{API}/chat", headers=phone, json={"message": "hello", "client_message_id": "shared-id"}).json()
    assert r["stop_reason"] != "repeat" and r["blocks"][-1]["text"] == "Yours."


def test_n25_a_second_arrival_waits_for_the_first(db, co):
    uid = co["owner"].id
    db.add(MobileTurn(user_id=uuid.UUID(str(uid)), client_message_id="busy", state="running",
                      started_at=datetime.now(timezone.utc)))
    db.commit()
    said = []
    with pytest.raises(turns.TurnInProgress):
        asyncio.run(turns.begin(db, uid, "busy", waiting=lambda: said.append("working"), wait_seconds=0.2, poll=0.05))
    assert said == ["working"]                                 # the stream says it is still working
    reply = {"thread_id": "t-1", "blocks": [{"type": "text", "id": "a", "text": "done"}], "stop_reason": "end_turn"}
    turns.finish(db, uid, "busy", reply)
    got = asyncio.run(turns.begin(db, uid, "busy", wait_seconds=0.2, poll=0.05))
    assert got["stop_reason"] == "repeat" and got["blocks"] == reply["blocks"]


def test_n25_an_abandoned_turn_is_taken_over_once(db, co):
    uid = uuid.UUID(str(co["owner"].id))
    db.add(MobileTurn(user_id=uid, client_message_id="died", state="running",
                      started_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
    db.commit()
    assert asyncio.run(turns.begin(db, uid, "died", wait_seconds=0.1, poll=0.05)) is None
    db.expire_all()
    row = db.get(MobileTurn, (uid, "died"))
    assert row.state == "running" and row.attempt == 2
    # the takeover is fresh, so a third arrival now waits instead of taking it again
    with pytest.raises(turns.TurnInProgress):
        asyncio.run(turns.begin(db, uid, "died", wait_seconds=0.1, poll=0.05))


def _thread_with(client, phone, monkeypatch, n: int) -> str:
    thread, tag = None, uuid.uuid4().hex[:6]
    for i in range(n):
        _script(monkeypatch, _text(f"answer {i}"))
        thread = client.post(f"{API}/chat", headers=phone, json={
            "message": f"question {i}", "thread_id": thread, "client_message_id": f"{tag}-q{i}"}).json()["thread_id"]
    return thread


def test_n26_a_thread_comes_in_pages_and_catches_up(client, co, phone, monkeypatch):
    thread = _thread_with(client, phone, monkeypatch, 3)                     # six messages
    r = client.get(f"{API}/threads/{thread}/messages?limit=2", headers=phone)
    page = r.json()
    assert [m.get("text") or m["blocks"][0]["text"] for m in page] == ["question 2", "answer 2"]
    assert r.headers["X-More-Before"] == "true"
    assert page[0]["client_message_id"].endswith("-q2")                     # the outbox knows its own
    r = client.get(f"{API}/threads/{thread}/messages?limit=10&before={page[0]['id']}", headers=phone)
    assert [m.get("text") for m in r.json() if m["role"] == "user"] == ["question 0", "question 1"]
    assert r.headers["X-More-Before"] == "false"
    first = r.json()[0]["id"]
    later = client.get(f"{API}/threads/{thread}/messages?after={first}", headers=phone).json()
    assert len(later) == 5 and later[-1]["blocks"][0]["text"] == "answer 2"
    assert client.get(f"{API}/threads/{thread}/messages?after={page[-1]['id']}", headers=phone).json() == []
    # a cursor from another conversation is not this one's
    other = _thread_with(client, phone, monkeypatch, 1)
    assert client.get(f"{API}/threads/{other}/messages?after={first}", headers=phone).status_code == 404
    assert client.get(f"{API}/threads/{thread}/messages?before=nonsense", headers=phone).status_code == 404


def test_n26_threads_since_returns_only_what_changed(client, co, phone, monkeypatch):
    a = _thread_with(client, phone, monkeypatch, 1)
    b = _thread_with(client, phone, monkeypatch, 1)
    listed = client.get(f"{API}/threads", headers=phone).json()
    assert [t["id"] for t in listed] == [b, a]
    stamp = listed[0]["updated_at"]
    assert client.get(f"{API}/threads", headers=phone, params={"since": stamp}).json() == []
    # a new message in the older conversation brings it back, first
    _script(monkeypatch, _text("more"))
    client.post(f"{API}/chat", headers=phone, json={"message": "again", "thread_id": a})
    changed = client.get(f"{API}/threads", headers=phone, params={"since": stamp}).json()
    assert [t["id"] for t in changed] == [a] and changed[0]["message_count"] == 4   # two questions, two answers
    assert client.get(f"{API}/threads", headers=phone, params={"since": "yesterday"}).status_code == 422

"""Streamed turns and retried messages on the phone (scenarios N20, N21,
roadmap ROADMAP_ANDROID_CHAT P0.5)."""
from __future__ import annotations

import json
from functools import partial

from tests.test_ai_guardrails import _calls, _FakeClient, _text, _txn, co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _script(monkeypatch, *responses):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import run_chat_turn
    monkeypatch.setattr(api_mod, "run_chat_turn", partial(run_chat_turn, client=_FakeClient(list(responses))))


def test_n20_a_turn_streams_its_steps_then_the_reply(client, co, phone, monkeypatch):
    _script(monkeypatch, _calls(("get_account_balance", {"account_code": "1110"})),
            _calls(_txn(80_000_000)), _text("Here is the balance, and the rent to confirm."))
    r = client.post(f"{API}/chat/stream", headers=phone, json={"message": "bank balance, and record the rent"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    statuses = [(d["stage"], d.get("tool"), d["text"]) for e, d in events if e == "status"]
    assert statuses == [
        ("thinking", None, "Thinking…"),
        ("tool", "get_account_balance", "Checking the books…"),
        ("thinking", None, "Thinking…"),
        ("tool", "propose_create_transaction", "Drafting the voucher…"),
        ("thinking", None, "Thinking…"),
    ]
    kinds = [e for e, _ in events]
    assert kinds[-2:] == ["reply", "done"]
    reply = events[-2][1]
    assert [b["type"] for b in reply["blocks"]] == ["figure", "proposal", "text"] and reply["thread_id"]


def test_n20_the_steps_are_said_in_the_users_language(client, co, phone, monkeypatch):
    assert client.put(f"{API}/me/language", headers=phone, json={"language": "fa"}).json() == {"language": "fa"}
    assert client.put(f"{API}/me/language", headers=phone, json={"language": "xx"}).status_code == 400
    _script(monkeypatch, _calls(_txn(5_000_000)), _text("آماده است."))
    events = _events(client.post(f"{API}/chat/stream", headers=phone, json={"message": "اجاره را ثبت کن"}).text)
    assert [d["text"] for e, d in events if e == "status"] == [
        "در حال فکر کردن…", "در حال نوشتن پیش‌نویس سند…", "در حال فکر کردن…"]


def test_n20_with_the_ai_down_the_stream_ends_with_an_error(client, co, phone, monkeypatch):
    from app.api import ai_accountant as api_mod
    from app.services.ai_accountant.orchestrator import AIAccountantError

    async def down(*a, **kw):
        raise AIAccountantError("provider unreachable")
    monkeypatch.setattr(api_mod, "run_chat_turn", down)
    events = _events(client.post(f"{API}/chat/stream", headers=phone, json={"message": "record the rent"}).text)
    error = next(d for e, d in events if e == "error")
    assert error["status"] == 502 and error["code"] == "ai_unavailable"
    assert [e for e, _ in events][-1] == "done"


def test_n20_a_question_the_books_answer_streams_just_its_reply(client, co, phone, monkeypatch):
    from app.api import ai_accountant as api_mod

    async def never(*a, **kw):
        raise AssertionError("the model was called")
    monkeypatch.setattr(api_mod, "run_chat_turn", never)
    events = _events(client.post(f"{API}/chat/stream", headers=phone, json={"message": "how much cash do we have?"}).text)
    assert [e for e, _ in events] == ["reply", "done"]
    assert [b["type"] for b in events[0][1]["blocks"]] == ["figure", "text"]


def test_n21_a_retried_message_gets_the_reply_it_already_had(client, co, phone, monkeypatch):
    # one scripted turn only: a second turn would exhaust the script and fail
    _script(monkeypatch, _calls(_txn(5_000_000)), _text("Drafted."))
    first = client.post(f"{API}/chat", headers=phone, json={"message": "record the rent"}).json()
    thread = first["thread_id"]
    _script(monkeypatch, _calls(_txn(7_000_000)), _text("Drafted."))
    a = client.post(f"{API}/chat", headers=phone,
                    json={"message": "and the fuel", "thread_id": thread, "client_message_id": "m-42"}).json()
    _script(monkeypatch)                                    # no script left: the model must not be asked
    b = client.post(f"{API}/chat", headers=phone,
                    json={"message": "and the fuel", "thread_id": thread, "client_message_id": "m-42"}).json()
    assert b["stop_reason"] == "repeat" and b["blocks"] == a["blocks"]
    tokens = [blk["token"] for m in client.get(f"{API}/threads/{thread}/messages", headers=phone).json()
              if m["role"] == "assistant" for blk in m["blocks"] if blk["type"] == "proposal"]
    assert len(tokens) == 2                                  # rent and fuel, fuel drafted once

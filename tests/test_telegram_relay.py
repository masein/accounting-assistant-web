"""The Telegram relay (ops/telegram-relay/relay.py, DEPLOY.md §14): it polls Telegram, hands each update to the
server's relay endpoint — with a voice note's audio — and makes the calls the server answers with, in order; a
chat's updates in order, different chats together; the offset kept across restarts; a server that doesn't answer
retried, then the update dropped; the bot token never in a log line."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("telegram_relay", ROOT / "ops/telegram-relay/relay.py")
relay_mod = importlib.util.module_from_spec(_spec)
sys.modules["telegram_relay"] = relay_mod             # dataclasses look their module up while it loads
_spec.loader.exec_module(relay_mod)

TOKEN = "123456789:AAFakeTokenForRelayTests_abcdefgh"
KEY = "relay-key-for-tests"


def _msg(update_id, chat_id, text=None, **extra):
    m = {"message_id": update_id, "chat": {"id": chat_id, "type": "private"}, "from": {"id": chat_id}, **extra}
    if text is not None:
        m["text"] = text
    return {"update_id": update_id, "message": m}


class World:
    """Telegram and the server, mocked: what each was asked, and what they answer."""

    def __init__(self, tmp_path, updates=(), answers=None, server_fails=0):
        self.tg_calls: list[tuple[str, dict]] = []
        self.app_calls: list[tuple[str, dict, dict]] = []
        self.batches = [list(updates)]
        self.answers = answers or {}
        self.server_fails = server_fails
        self.cfg = relay_mod.Config(token=TOKEN, app_url="https://books.example.com", relay_key=KEY,
                                    state_dir=tmp_path, poll_timeout=1, typing_every=0.01, retry_pause=0.0)

    def tg(self, request: httpx.Request) -> httpx.Response:
        if "/file/bot" in request.url.path:
            self.tg_calls.append(("<file>", {"path": request.url.path.rsplit("/", 1)[-1]}))
            return httpx.Response(200, content=b"OggS" + b"\x01" * 30)
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else {}
        self.tg_calls.append((method, body))
        result = {"getMe": {"id": 42, "username": "BooksRelayBot"},
                  "getFile": {"file_path": "voice/f.oga", "file_size": 34}}.get(method, True)
        if method == "getUpdates":
            result = self.batches.pop(0) if self.batches else []
        if method == "sendMessage" and body.get("text") == "boom":
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200, json={"ok": True, "result": result})

    async def app(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path.rsplit("/", 1)[-1]
        self.app_calls.append((path, body, dict(request.headers)))
        if path == "hello":
            return httpx.Response(200, json={"ok": True})
        if self.server_fails:
            self.server_fails -= 1
            return httpx.Response(502, text="bad gateway")
        await asyncio.sleep(0.03)                       # an assistant turn takes a moment: "typing…" shows
        uid = body["update"]["update_id"]
        return httpx.Response(200, json={"ok": True, "calls": self.answers.get(uid, [])})

    def relay(self):
        return relay_mod.Relay(self.cfg, tg_transport=httpx.MockTransport(self.tg),
                               app_transport=httpx.MockTransport(self.app))

    def sent(self, method="sendMessage"):
        return [b for m, b in self.tg_calls if m == method]


def _call(text, chat_id):
    return {"method": "sendMessage", "params": {"chat_id": str(chat_id), "text": text}}


def test_start_drops_a_webhook_and_checks_in(tmp_path):
    w = World(tmp_path)
    r = w.relay()
    asyncio.run(r.start())
    assert [m for m, _ in w.tg_calls] == ["getMe", "deleteWebhook"]
    [(path, body, headers)] = w.app_calls
    assert path == "hello" and body == {"username": "BooksRelayBot"}
    assert headers["authorization"] == f"Bearer {KEY}"


def test_each_update_goes_to_the_server_and_its_calls_go_to_telegram_in_order(tmp_path):
    updates = [_msg(10, 1, "hi"), _msg(11, 2, "paid 20 for lunch"), _msg(12, 1, "/new")]
    answers = {10: [_call("Hello!", 1)],
               11: [_call("Lunch, 20 — confirm?", 2), {"method": "sendMessage", "params": {"chat_id": "2", "text": "…",
                     "reply_markup": {"inline_keyboard": [[{"text": "✅ Confirm", "callback_data": "ok:t"}]]}}}],
               12: [_call("New conversation started.", 1)]}
    w = World(tmp_path, updates, answers)
    r = w.relay()
    assert asyncio.run(r.poll_once()) == 3
    assert [b["update"]["update_id"] for p, b, _ in w.app_calls if p == "update"] in ([10, 12, 11], [10, 11, 12], [11, 10, 12])
    to_1 = [b["text"] for b in w.sent() if b["chat_id"] == "1"]
    assert to_1 == ["Hello!", "New conversation started."]                    # a chat's updates in order
    assert any("reply_markup" in b for b in w.sent())                         # the Confirm button goes through
    assert any(b["action"] == "typing" for b in w.sent("sendChatAction"))     # "typing…" while the server works
    assert r.load_offset() == 13                                              # kept: a restart starts after it
    w.batches = [[]]
    asyncio.run(r.poll_once())
    assert [b for m, b in w.tg_calls if m == "getUpdates"][-1]["offset"] == 13


def test_a_voice_note_goes_with_its_audio(tmp_path):
    voice = {"file_id": "V1", "file_size": 34, "duration": 2, "mime_type": "audio/ogg"}
    big = {"file_id": "V2", "file_size": relay_mod.MAX_VOICE_BYTES + 1, "duration": 900}
    w = World(tmp_path, [_msg(20, 1, voice=voice), _msg(21, 3, voice=big)])
    asyncio.run(w.relay().poll_once())
    bodies = {b["update"]["update_id"]: b for p, b, _ in w.app_calls if p == "update"}
    assert base64.b64decode(bodies[20]["files"]["V1"]).startswith(b"OggS")
    assert bodies[21]["files"] == {}                                          # too large: not fetched at all
    assert [b["file_id"] for b in w.sent("getFile")] == ["V1"]


def test_a_server_that_does_not_answer_is_retried_then_the_update_dropped(tmp_path):
    w = World(tmp_path, [_msg(30, 1, "hi")], {30: [_call("Hello!", 1)]}, server_fails=2)
    asyncio.run(w.relay().poll_once())
    assert len([p for p, _, _ in w.app_calls if p == "update"]) == 3 and [b["text"] for b in w.sent()] == ["Hello!"]
    w2 = World(tmp_path / "b", [_msg(31, 1, "hi")], {31: [_call("Hello!", 1)]}, server_fails=9)
    r2 = w2.relay()
    asyncio.run(r2.poll_once())
    assert w2.sent() == [] and r2.load_offset() == 32                         # dropped, not retried forever


def test_one_failing_call_does_not_stop_the_rest(tmp_path):
    w = World(tmp_path, [_msg(40, 1, "x")], {40: [_call("boom", 1), _call("after", 1)]})
    asyncio.run(w.relay().poll_once())
    assert [b["text"] for b in w.sent()] == ["boom", "after"]


def test_a_wrong_relay_key_is_said_plainly(tmp_path):
    w = World(tmp_path)

    def refuse(request):
        return httpx.Response(404, json={"detail": "Not Found"})
    r = relay_mod.Relay(w.cfg, tg_transport=httpx.MockTransport(w.tg), app_transport=httpx.MockTransport(refuse))
    with pytest.raises(PermissionError, match="relay key"):
        asyncio.run(r.start())


def test_the_token_is_never_logged(tmp_path, caplog):
    w = World(tmp_path, [_msg(50, 1, "x")], {50: [_call("boom", 1)]})
    with caplog.at_level(logging.DEBUG):
        asyncio.run(w.relay().poll_once())
    assert "chat not found" in caplog.text and TOKEN not in caplog.text and TOKEN.split(":")[1] not in caplog.text


def test_the_server_side_and_the_relay_agree():
    from app.services import messenger
    assert relay_mod.MAX_VOICE_BYTES == messenger.MAX_VOICE_BYTES
    env = (ROOT / "ops/telegram-relay/.env.example").read_text(encoding="utf-8")
    assert all(k in env for k in ("TELEGRAM_BOT_TOKEN=", "APP_URL=", "RELAY_KEY="))
    assert {".env", "state/"} <= set((ROOT / "ops/telegram-relay/.gitignore").read_text().split())

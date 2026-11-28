"""The Telegram relay (ops/telegram-relay/relay.py, DEPLOY.md §14): it polls Telegram, hands each update to the
server's relay endpoint — with a voice note's, a photo's or a file's bytes — and makes the calls the server answers
with, in order, uploading a document's bytes (scenario CU8); a chat's updates in order, different chats together;
the offset kept across restarts; a server that doesn't answer retried, then the round stopped without moving the
offset, so nothing is dropped (CU9); the bot token never in a log line."""
from __future__ import annotations

import asyncio
import base64
import email.parser
import email.policy
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


def _form(request: httpx.Request) -> tuple[dict, dict[str, bytes]]:
    """A multipart request's fields and files."""
    raw = request.read()
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + raw)
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        if part.get_filename():
            files[name] = data
        else:
            fields[name] = data.decode()
    return fields, files


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
        if (request.headers.get("content-type") or "").startswith("multipart/"):
            fields, files = _form(request)
            body = {**fields, "_files": files}
        else:
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
        if (request.headers.get("content-type") or "").startswith("multipart/"):
            fields, files = _form(request)
            body = {k: json.loads(v) for k, v in fields.items()}
            body["files"] = {k[2:]: v for k, v in files.items()}
        else:
            body = json.loads(request.content)
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "update":
            body.setdefault("files", {})
        if path == "outbox":
            self.app_calls.append((path, body, dict(request.headers)))
            out, self.outbox = getattr(self, "outbox", []), []
            return httpx.Response(200, json={"ok": True, "calls": out})
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
    assert bodies[20]["files"]["V1"].startswith(b"OggS")                     # a multipart part, not base64
    assert bodies[21]["files"] == {}                                          # too large: not fetched at all
    assert [b["file_id"] for b in w.sent("getFile")] == ["V1"]


def test_a_photo_and_a_file_go_with_their_bytes(tmp_path):
    """CU8: the photo's largest size within the limit (as the server picks it), a document; too large, nothing."""
    photo = [{"file_id": "S", "file_size": 10, "width": 90}, {"file_id": "L", "file_size": 34, "width": 1280},
             {"file_id": "XL", "file_size": relay_mod.MAX_FILE_BYTES + 1, "width": 4000}]
    doc = {"file_id": "D", "file_name": "statement.csv", "mime_type": "text/csv", "file_size": 34}
    big = {"file_id": "B", "file_name": "huge.pdf", "file_size": relay_mod.MAX_FILE_BYTES + 1}
    w = World(tmp_path, [_msg(25, 1, photo=photo, caption="receipt"), _msg(26, 2, document=doc),
                         _msg(27, 3, document=big)])
    asyncio.run(w.relay().poll_once())
    bodies = {b["update"]["update_id"]: b for p, b, _ in w.app_calls if p == "update"}
    assert list(bodies[25]["files"]) == ["L"] and list(bodies[26]["files"]) == ["D"] and bodies[27]["files"] == {}
    assert relay_mod.MAX_FILE_BYTES == 20 * 1024 * 1024


def test_a_document_from_the_server_is_uploaded(tmp_path):
    """CU8: the journal's Excel, an invoice's PDF — its bytes come in the answer, and go up as a multipart upload."""
    xlsx = b"PK\x03\x04" + b"\x00" * 40
    call = {"method": "sendDocument", "params": {"chat_id": "1", "caption": "📎 journal.xlsx"},
            "files": {"document": {"name": "journal.xlsx", "mime": "application/vnd.ms-excel",
                                   "b64": base64.b64encode(xlsx).decode()}}}
    w = World(tmp_path, [_msg(28, 1, "journal please")], {28: [_call("Here it is.", 1), call]})
    asyncio.run(w.relay().poll_once())
    [doc] = w.sent("sendDocument")
    assert doc["chat_id"] == "1" and doc["caption"] == "📎 journal.xlsx" and doc["_files"]["document"] == xlsx
    assert [b["text"] for b in w.sent()] == ["Here it is."]                  # the words first, then the file


def test_a_server_that_does_not_answer_is_retried_then_sent_again_next_round(tmp_path):
    w = World(tmp_path, [_msg(30, 1, "hi")], {30: [_call("Hello!", 1)]}, server_fails=2)
    asyncio.run(w.relay().poll_once())
    assert len([p for p, _, _ in w.app_calls if p == "update"]) == 3 and [b["text"] for b in w.sent()] == ["Hello!"]
    w2 = World(tmp_path / "b", [_msg(31, 1, "hi")], {31: [_call("Hello!", 1)]}, server_fails=3)
    r2 = w2.relay()
    with pytest.raises(relay_mod.ServerUnavailable):
        asyncio.run(r2.poll_once())
    assert w2.sent() == [] and r2.load_offset() is None                      # the offset stays: not dropped (CU9)
    w2.batches = [[_msg(31, 1, "hi")]]                                         # Telegram gives it again
    asyncio.run(r2.poll_once())
    assert [b["text"] for b in w2.sent()] == ["Hello!"] and r2.load_offset() == 32


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
    assert relay_mod.MAX_FILE_BYTES == messenger.MAX_FILE_BYTES
    msg = {"photo": [{"file_id": "a", "file_size": 5, "width": 90}, {"file_id": "b", "file_size": 9, "width": 800}]}
    assert relay_mod._photo_of(msg)["file_id"] == messenger._document_of(msg)["file_id"] == "b"
    env = (ROOT / "ops/telegram-relay/.env.example").read_text(encoding="utf-8")
    assert all(k in env for k in ("APP_URL=", "RELAY_KEY=")) and "TELEGRAM_BOT_TOKEN=" not in env
    # the token alone, in its own file: the watchdog reads it (ops/watchdog/watchdog.sh) and never the relay key
    assert "TELEGRAM_BOT_TOKEN=" in (ROOT / "ops/telegram-relay/token.env.example").read_text(encoding="utf-8")
    assert "token.env" in (ROOT / "ops/telegram-relay/compose.yml").read_text(encoding="utf-8")
    assert {".env", "token.env", "state/"} <= set((ROOT / "ops/telegram-relay/.gitignore").read_text().split())


def test_the_outbox_is_fetched_and_sent(tmp_path):
    """DA9: the support desk's reply waits on the server; the relay fetches it and sends it, once."""
    w = World(tmp_path)
    w.outbox = [_call("💬 Support: Which period?", 7)]
    r = w.relay()
    assert asyncio.run(r.outbox_once()) == 1
    assert [b["text"] for b in w.sent()] == ["💬 Support: Which period?"]
    assert asyncio.run(r.outbox_once()) == 0                                  # handed over once
    assert [p for p, _, h in w.app_calls if p == "outbox" and h["authorization"] == f"Bearer {KEY}"]

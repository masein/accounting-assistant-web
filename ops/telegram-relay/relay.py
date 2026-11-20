"""Telegram relay for the accounting assistant (DEPLOY.md §14).

Production can't reach Telegram (it is filtered where the server runs), so the bot's Telegram side runs here, on a
machine that can: this program polls Telegram for the bot's updates (getUpdates), hands each one to the server
(POST /bots/telegram/relay/update, with the relay key) and makes the Bot API calls the server answers with —
sendMessage, answerCallbackQuery, … Everything the bot *does* — linking a chat to a login, the same assistant as the
web chat, Confirm / Cancel — stays on the server (app/services/messenger.py, "Relay mode"). The bot token lives only
here; the server keeps only the relay key's hash.

A voice note's audio is fetched here and sent along (the server can't download it). The chat shows "typing…" while
the server works. Updates of one chat are handled in order; different chats at the same time. The offset of the
last update handled is kept in STATE_DIR, so a restart neither loses nor repeats updates (and the server ignores an
update it has already seen).

Settings (environment): TELEGRAM_BOT_TOKEN, APP_URL, RELAY_KEY; optional PLATFORM (telegram), TELEGRAM_API
(https://api.telegram.org), STATE_DIR (/state), POLL_TIMEOUT (50 s), HELLO_EVERY (600 s).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("relay")
MAX_VOICE_BYTES = 10 * 1024 * 1024       # as the server's (MAX_VOICE_BYTES in app/services/messenger.py)
APP_TIMEOUT = 300.0                      # an assistant turn can take a while
APP_ATTEMPTS = 3


class TelegramError(RuntimeError):
    pass


class _Redact(logging.Filter):
    """Takes the bot token out of a log line: httpx logs every request's URL, and a Bot API URL holds the token."""

    def __init__(self, secret: str):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret:
            msg = record.getMessage()
            if self.secret in msg:
                record.msg, record.args = msg.replace(self.secret, "<token>"), ()
        return True


@dataclass
class Config:
    token: str
    app_url: str
    relay_key: str
    platform: str = "telegram"
    telegram_api: str = "https://api.telegram.org"
    state_dir: Path = Path("/state")
    poll_timeout: int = 50
    hello_every: float = 600.0
    typing_every: float = 4.0
    retry_pause: float = 5.0

    @classmethod
    def from_env(cls) -> "Config":
        def need(name: str) -> str:
            value = (os.environ.get(name) or "").strip()
            if not value:
                raise SystemExit(f"{name} is not set (see .env.example)")
            return value
        return cls(token=need("TELEGRAM_BOT_TOKEN"), app_url=need("APP_URL").rstrip("/"), relay_key=need("RELAY_KEY"),
                   platform=os.environ.get("PLATFORM", "telegram"),
                   telegram_api=os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/"),
                   state_dir=Path(os.environ.get("STATE_DIR", "/state")),
                   poll_timeout=int(os.environ.get("POLL_TIMEOUT", "50")),
                   hello_every=float(os.environ.get("HELLO_EVERY", "600")))


@dataclass
class Relay:
    cfg: Config
    tg_transport: httpx.AsyncBaseTransport | None = None      # tests: httpx.MockTransport
    app_transport: httpx.AsyncBaseTransport | None = None
    username: str = ""
    _last_hello: float = field(default=0.0)

    def __post_init__(self) -> None:
        for name in ("httpx", "httpcore"):                  # whatever the logging setup, never the token
            lg = logging.getLogger(name)
            if not any(isinstance(f, _Redact) and f.secret == self.cfg.token for f in lg.filters):
                lg.addFilter(_Redact(self.cfg.token))

    # --- the two sides ---------------------------------------------------------------------------------------

    async def tg(self, method: str, timeout: float = 30.0, **params) -> Any:
        """One Bot API call. The URL holds the token: it is never logged."""
        url = f"{self.cfg.telegram_api}/bot{self.cfg.token}/{method}"
        async with httpx.AsyncClient(timeout=timeout, transport=self.tg_transport) as client:
            r = await client.post(url, json={k: v for k, v in params.items() if v is not None})
        body = r.json() if r.content else {}
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description') or r.status_code}")
        return body.get("result")

    async def tg_file(self, file_path: str) -> bytes:
        url = f"{self.cfg.telegram_api}/file/bot{self.cfg.token}/{file_path}"
        async with httpx.AsyncClient(timeout=60.0, transport=self.tg_transport) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

    async def app(self, path: str, body: dict, timeout: float = APP_TIMEOUT) -> dict:
        url = f"{self.cfg.app_url}/bots/{self.cfg.platform}/relay/{path}"
        async with httpx.AsyncClient(timeout=timeout, transport=self.app_transport) as client:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {self.cfg.relay_key}"})
        if r.status_code == 404:
            raise PermissionError("the server doesn't know this relay key (or isn't in relay mode for this bot)")
        r.raise_for_status()
        return r.json()

    # --- start, check in -------------------------------------------------------------------------------------

    async def start(self) -> None:
        me = await self.tg("getMe")
        self.username = me.get("username") or ""
        # getUpdates and a webhook exclude each other: a webhook left from the server's own setup goes
        await self.tg("deleteWebhook", drop_pending_updates=False)
        await self.hello()
        log.info("relaying @%s to %s", self.username, self.cfg.app_url)

    async def hello(self) -> None:
        await self.app("hello", {"username": self.username}, timeout=30.0)
        self._last_hello = time.monotonic()

    # --- updates ---------------------------------------------------------------------------------------------

    def _offset_file(self) -> Path:
        return self.cfg.state_dir / f"{self.cfg.platform}-offset"

    def load_offset(self) -> int | None:
        try:
            return int(self._offset_file().read_text().strip())
        except (OSError, ValueError):
            return None

    def save_offset(self, offset: int) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._offset_file().with_suffix(".tmp")
        tmp.write_text(str(offset))
        tmp.replace(self._offset_file())

    async def poll_once(self) -> int:
        """One getUpdates round: every update handled, then the offset kept. Returns how many came."""
        updates = await self._get_updates(self.load_offset())
        if not updates:
            return 0
        by_chat: dict[str, list[dict]] = {}
        for u in updates:
            by_chat.setdefault(_chat_of(u), []).append(u)

        async def one_chat(items: list[dict]) -> None:
            for u in items:                       # a chat's updates in order
                await self.handle(u)
        await asyncio.gather(*(one_chat(items) for items in by_chat.values()))
        self.save_offset(max(u["update_id"] for u in updates) + 1)
        return len(updates)

    async def _get_updates(self, offset: int | None) -> list[dict]:
        url = f"{self.cfg.telegram_api}/bot{self.cfg.token}/getUpdates"
        params = {"timeout": self.cfg.poll_timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        async with httpx.AsyncClient(timeout=self.cfg.poll_timeout + 15, transport=self.tg_transport) as client:
            r = await client.post(url, json=params)
        body = r.json() if r.content else {}
        if not body.get("ok"):
            raise TelegramError(f"getUpdates: {body.get('description') or r.status_code}")
        return body.get("result") or []

    async def handle(self, update: dict) -> None:
        """One update: to the server (with a voice note's audio), then the server's calls to Telegram."""
        files = await self.voice_files(update)
        chat_id = _chat_of(update)
        typing = asyncio.create_task(self._typing(chat_id)) if "message" in update and chat_id else None
        try:
            answer = await self._to_server(update, files)
        finally:
            if typing:
                typing.cancel()
        for call in (answer or {}).get("calls") or []:
            try:
                await self.tg(call["method"], **(call.get("params") or {}))
            except (TelegramError, httpx.HTTPError, KeyError) as exc:
                log.warning("update %s: %s failed: %s", update.get("update_id"), call.get("method"), exc)

    async def _to_server(self, update: dict, files: dict[str, str]) -> dict | None:
        for attempt in range(1, APP_ATTEMPTS + 1):
            try:
                return await self.app("update", {"update": update, "files": files})
            except PermissionError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("update %s: the server didn't answer (attempt %s): %s", update.get("update_id"), attempt, exc)
                if attempt < APP_ATTEMPTS:
                    await asyncio.sleep(self.cfg.retry_pause * attempt)
        log.error("update %s dropped: the server didn't answer %s times", update.get("update_id"), APP_ATTEMPTS)
        return None

    async def _typing(self, chat_id: str) -> None:
        try:
            while True:
                try:
                    await self.tg("sendChatAction", chat_id=chat_id, action="typing", timeout=10.0)
                except (TelegramError, httpx.HTTPError):
                    pass
                await asyncio.sleep(self.cfg.typing_every)
        except asyncio.CancelledError:
            pass

    async def voice_files(self, update: dict) -> dict[str, str]:
        """{file_id: base64} of a voice note or audio the server will transcribe (only when small enough)."""
        msg = update.get("message") or {}
        media = msg.get("voice") or msg.get("audio")
        if not media or msg.get("text"):
            return {}
        if int(media.get("file_size") or 0) > MAX_VOICE_BYTES:
            return {}
        try:
            info = await self.tg("getFile", file_id=media["file_id"])
            if int(info.get("file_size") or 0) > MAX_VOICE_BYTES:
                return {}
            data = await self.tg_file(info["file_path"])
        except (TelegramError, httpx.HTTPError, KeyError) as exc:
            log.warning("update %s: couldn't fetch the voice note: %s", update.get("update_id"), exc)
            return {}
        return {media["file_id"]: base64.b64encode(data[: MAX_VOICE_BYTES + 1]).decode()}

    # --- the loop --------------------------------------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self.start()
                break
            except PermissionError as exc:
                log.error("%s — waiting 5 minutes", exc)
                await asyncio.sleep(300)
            except (TelegramError, httpx.HTTPError) as exc:
                log.warning("couldn't start: %s — retrying in 30 s", exc)
                await asyncio.sleep(30)
        pause = 0.0
        while True:
            try:
                if time.monotonic() - self._last_hello > self.cfg.hello_every:
                    await self.hello()
                await self.poll_once()
                (self.cfg.state_dir / "alive").touch()
                pause = 0.0
            except PermissionError as exc:
                log.error("%s — waiting 5 minutes", exc)
                await asyncio.sleep(300)
            except (TelegramError, httpx.HTTPError, ValueError, OSError) as exc:
                pause = min(60.0, (pause or 1.0) * 2)
                log.warning("polling failed (%s) — again in %.0f s", exc, pause)
                await asyncio.sleep(pause)


def _chat_of(update: dict) -> str:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    return str((msg.get("chat") or {}).get("id") or "")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)        # its request lines would print the token
    asyncio.run(Relay(Config.from_env()).run())


if __name__ == "__main__":
    main()

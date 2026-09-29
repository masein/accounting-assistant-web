"""Messenger bots (roadmap 2026-09 §5.7, part 2).

Telegram, and Bale (بله) — which speaks the Telegram Bot API at its own host
and is reachable where Telegram is filtered. One adapter, two base URLs.

Flow
----
* The platform admin saves a bot token (Settings → Messenger bots): ``getMe``
  checks it, ``setWebhook`` points the bot at
  ``{APP_PUBLIC_URL}/bots/<platform>/webhook/<secret>`` with the same secret
  as ``secret_token`` (both are checked on every call).
* A user opens the account menu → Connect: a one-time code, valid 10
  minutes, as ``https://t.me/<bot>?start=<code>``. The bot's ``/start
  <code>`` links that private chat to the login.
* A message from a linked chat is one turn of the same assistant as the web
  chat, run as that user — their company, role (the chat permission is
  checked every time), language and AI budget. A voice message is
  transcribed first. Each proposal comes back with Confirm / Cancel buttons;
  nothing is written until Confirm, exactly as on the web.
* Unlinked chats and groups only get instructions. A re-delivered update is
  ignored (``messenger_updates``). The webhook answers at once and does the
  work in the background: an AI turn can take longer than a platform waits.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.messenger import MessengerLink, MessengerUpdate

log = logging.getLogger("app.messenger")

PLATFORMS: dict[str, dict[str, str]] = {
    "telegram": {"api": "https://api.telegram.org", "link": "https://t.me/{bot}?start={code}", "name": "Telegram"},
    "bale": {"api": "https://tapi.bale.ai", "link": "https://ble.ir/{bot}?start={code}", "name": "Bale"},
}
SETTINGS_KEY = "messenger_bots"
CODE_TTL = timedelta(minutes=10)
MAX_TEXT = 3900                      # Telegram allows 4096 per message
MAX_VOICE_BYTES = 10 * 1024 * 1024
_TRANSPORT = None                    # tests put an httpx.MockTransport here

TEXTS = {
    "en": {
        "hello": "Hi! To use me, link this chat to your account: in the app, open the account menu → "
                 "Connect Telegram or Bale, and tap the link it shows.",
        "linked": "Linked to {company} as {user}. Tell me what you spent or earned — for example "
                  "\"paid 20 for lunch from the card\" — or send a voice note. /new starts a new conversation, "
                  "/stop unlinks this chat.",
        "bad_code": "That link has expired or was already used — make a new one in the app.",
        "unlinked": "This chat is no longer linked. You can link it again from the app.",
        "new": "New conversation started.",
        "not_allowed": "Your role in {company} can't use the assistant.",
        "suspended": "This account is suspended.",
        "groups": "I only work in a private chat.",
        "only_text": "Send text or a voice note — receipts and files go in through the app for now.",
        "voice_failed": "I couldn't make out that voice note — try again, or type it.",
        "heard": "🎙 {text}",
        "confirm": "✅ Confirm", "cancel": "✖ Cancel",
        "done": "✅ Done.", "cancelled": "Cancelled — nothing was saved.",
        "awaiting_approval": "⏳ Sent for approval — it is recorded once someone who can approve confirms it.",
        "expired": "This card has expired — ask again.", "not_yours": "This card isn't yours.",
        "failed": "Something went wrong — nothing was saved. Try again in the app.",
        "busy": "The assistant is busy or over today's limit — try again later.",
    },
    "fa": {
        "hello": "سلام! برای استفاده، این گفتگو را به حساب خود وصل کنید: در برنامه منوی حساب ← «اتصال تلگرام یا بله» را باز "
                 "کنید و روی پیوندی که نشان می‌دهد بزنید.",
        "linked": "به {company} با کاربر {user} وصل شد. بگویید چه خرج یا دریافتی داشتید — مثلاً «۵۰ هزار تومن نون "
                  "از کارت» — یا پیام صوتی بفرستید. /new گفتگوی تازه، /stop قطع اتصال.",
        "bad_code": "این پیوند منقضی شده یا قبلاً استفاده شده — در برنامه پیوند تازه بسازید.",
        "unlinked": "اتصال این گفتگو قطع شد. می‌توانید دوباره از برنامه وصلش کنید.",
        "new": "گفتگوی تازه شروع شد.",
        "not_allowed": "نقش شما در {company} اجازهٔ استفاده از دستیار را ندارد.",
        "suspended": "این حساب معلق است.",
        "groups": "فقط در گفتگوی خصوصی کار می‌کنم.",
        "only_text": "متن یا پیام صوتی بفرستید — رسید و فایل را فعلاً از خود برنامه بفرستید.",
        "voice_failed": "پیام صوتی را متوجه نشدم — دوباره بفرستید یا بنویسید.",
        "heard": "🎙 {text}",
        "confirm": "✅ تأیید", "cancel": "✖ لغو",
        "done": "✅ ثبت شد.", "cancelled": "لغو شد — چیزی ثبت نشد.",
        "awaiting_approval": "⏳ برای تأیید فرستاده شد — پس از تأیید یک نفر دیگر ثبت می‌شود.",
        "expired": "این کارت منقضی شده — دوباره بپرسید.", "not_yours": "این کارت مال شما نیست.",
        "failed": "مشکلی پیش آمد — چیزی ثبت نشد. در برنامه دوباره امتحان کنید.",
        "busy": "دستیار مشغول است یا سهمیهٔ امروز تمام شده — بعداً امتحان کنید.",
    },
}


def _t(lang: str, key: str, **kw) -> str:
    return (TEXTS.get(lang) or TEXTS["en"])[key].format(**kw)


# --- settings ---------------------------------------------------------------------------------------------

def _row(db: Session):
    from app.models.app_setting import AppSetting
    return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY,
                                               AppSetting.company_id.is_(None))).scalars().first()


def load_settings(db: Session) -> dict[str, dict[str, Any]]:
    """{platform: {token (plain), secret, username}} of the connected bots."""
    import json
    from app.core.secrets import decrypt_secret
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        row = _row(db)
    try:
        raw = json.loads(row.value) if row and row.value else {}
    except ValueError:
        raw = {}
    out = {}
    for platform, cfg in raw.items():
        if platform in PLATFORMS and isinstance(cfg, dict) and cfg.get("token"):
            out[platform] = {**cfg, "token": decrypt_secret(cfg["token"])}
    return out


def _save(db: Session, bots: dict[str, dict[str, Any]]) -> None:
    import json
    from app.core.secrets import encrypt_secret
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    value = json.dumps({p: {**c, "token": encrypt_secret(c["token"])} for p, c in bots.items()})
    with tenant_bypass():
        row = _row(db)
        if row is None:
            db.add(AppSetting(key=SETTINGS_KEY, value=value, company_id=None))
        else:
            row.value = value
        db.flush()


def public_status(db: Session) -> dict[str, Any]:
    bots = load_settings(db)
    return {p: {"name": meta["name"], "connected": p in bots, "username": (bots.get(p) or {}).get("username")}
            for p, meta in PLATFORMS.items()}


# --- the Bot API ----------------------------------------------------------------------------------------------

async def api(platform: str, token: str, method: str, **params) -> Any:
    url = f"{PLATFORMS[platform]['api']}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), transport=_TRANSPORT) as client:
        r = await client.post(url, json={k: v for k, v in params.items() if v is not None})
    body = r.json() if r.content else {}
    if not body.get("ok"):
        raise RuntimeError(f"{platform} {method}: {body.get('description') or r.status_code}")
    return body.get("result")


async def download(platform: str, token: str, file_id: str) -> bytes:
    info = await api(platform, token, "getFile", file_id=file_id)
    if int(info.get("file_size") or 0) > MAX_VOICE_BYTES:
        raise RuntimeError("voice note too large")
    url = f"{PLATFORMS[platform]['api']}/file/bot{token}/{info['file_path']}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0), transport=_TRANSPORT) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content[: MAX_VOICE_BYTES + 1]


async def connect(db: Session, platform: str, token: str) -> dict[str, Any]:
    """Check the token, register the webhook, keep the bot. Raises ValueError."""
    from app.core.config import settings
    if platform not in PLATFORMS:
        raise ValueError("Unknown platform.")
    token = (token or "").strip()
    if not re.fullmatch(r"\d{3,}:[A-Za-z0-9_-]{20,}", token):
        raise ValueError("That does not look like a bot token (digits, a colon, then letters).")
    public = (settings.app_public_url or "").rstrip("/")
    if not public.startswith("https://"):
        raise ValueError("APP_PUBLIC_URL must be the app's public https address — the platform posts messages there.")
    try:
        me = await api(platform, token, "getMe")
        secret = secrets.token_urlsafe(24)
        await api(platform, token, "setWebhook", url=f"{public}/bots/{platform}/webhook/{secret}",
                  secret_token=secret, allowed_updates=["message", "callback_query"])
    except (httpx.HTTPError, RuntimeError) as exc:
        raise ValueError(f"{PLATFORMS[platform]['name']} refused it: {exc}") from exc
    bots = load_settings(db)
    bots[platform] = {"token": token, "secret": secret, "username": me.get("username") or "",
                      "connected_at": datetime.now(timezone.utc).isoformat()}
    _save(db, bots)
    return public_status(db)[platform]


async def disconnect(db: Session, platform: str) -> None:
    bots = load_settings(db)
    cfg = bots.pop(platform, None)
    if cfg:
        try:
            await api(platform, cfg["token"], "deleteWebhook")
        except Exception:  # noqa: BLE001 — forget it locally either way
            log.warning("deleteWebhook failed for %s", platform, exc_info=True)
    _save(db, bots)


def check_secret(db: Session, platform: str, path_secret: str, header_secret: str | None) -> bool:
    cfg = load_settings(db).get(platform)
    if not cfg or not cfg.get("secret"):
        return False
    ok = secrets.compare_digest(path_secret or "", cfg["secret"])
    if header_secret is not None:                    # Telegram sends it; accept its absence (Bale)
        ok = ok and secrets.compare_digest(header_secret, cfg["secret"])
    return ok


# --- linking --------------------------------------------------------------------------------------------------

def start_link(db: Session, *, user_id: str, platform: str) -> dict[str, Any]:
    """A pending link with a fresh code; the old pending ones of this user go."""
    bots = load_settings(db)
    if platform not in bots:
        raise ValueError("That messenger is not connected on this server.")
    uid = uuid.UUID(str(user_id))
    db.execute(delete(MessengerLink).where(MessengerLink.user_id == uid, MessengerLink.status == "pending"))
    code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
    expires = datetime.now(timezone.utc) + CODE_TTL
    db.add(MessengerLink(platform=platform, user_id=uid, status="pending", code=code, code_expires_at=expires))
    db.flush()
    return {"platform": platform, "code": code, "expires_at": expires.isoformat(),
            "url": PLATFORMS[platform]["link"].format(bot=bots[platform]["username"], code=code),
            "bot": bots[platform]["username"]}


def links_of(db: Session, user_id: str) -> list[dict[str, Any]]:
    rows = db.execute(select(MessengerLink).where(MessengerLink.user_id == uuid.UUID(str(user_id)),
                                                  MessengerLink.status == "active")).scalars().all()
    return [{"id": str(r.id), "platform": r.platform, "name": PLATFORMS[r.platform]["name"],
             "chat_name": r.chat_name, "linked_at": r.created_at.isoformat() if r.created_at else None,
             "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None} for r in rows]


def _link_for_chat(db: Session, platform: str, chat_id: str) -> MessengerLink | None:
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        return db.execute(select(MessengerLink).where(MessengerLink.platform == platform,
                                                      MessengerLink.chat_id == chat_id,
                                                      MessengerLink.status == "active")).scalars().first()


def _redeem(db: Session, platform: str, chat_id: str, chat_name: str | None, code: str) -> MessengerLink | None:
    from app.db.tenant import tenant_bypass
    now = datetime.now(timezone.utc)
    with tenant_bypass():
        link = db.execute(select(MessengerLink).where(MessengerLink.code == code, MessengerLink.platform == platform,
                                                      MessengerLink.status == "pending")).scalars().first()
        if link is None:
            return None
        exp = link.code_expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp is None or exp < now:
            db.delete(link)
            db.flush()
            return None
        # a chat belongs to one login: an older link of this chat goes
        db.execute(delete(MessengerLink).where(MessengerLink.platform == platform, MessengerLink.chat_id == chat_id,
                                               MessengerLink.id != link.id))
        link.status, link.chat_id, link.chat_name = "active", chat_id, (chat_name or "")[:128]
        link.code = link.code_expires_at = None
        link.last_seen_at = now
        db.flush()
        return link


# --- handling one update ---------------------------------------------------------------------------------------

def _seen(db: Session, platform: str, update_id: int | None) -> bool:
    """True when this update was handled before (and records it otherwise)."""
    if update_id is None:
        return False
    from sqlalchemy.exc import IntegrityError
    try:
        with db.begin_nested():
            db.add(MessengerUpdate(platform=platform, update_id=int(update_id)))
            db.flush()
    except IntegrityError:
        return True
    return False


def _session_user(db: Session, link: MessengerLink):
    """(SessionUser, User, Company) for the link, or a reason key."""
    from app.core.auth import SessionUser
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    from app.models.user import User
    with tenant_bypass():
        user = db.get(User, link.user_id)
        company = db.get(Company, user.company_id) if user and user.company_id else None
    if user is None or not user.is_active:
        return None, None, None, "unlinked"
    if company is not None and company.status != "active":
        return None, user, company, "suspended"
    su = SessionUser(user_id=str(user.id), username=user.username, is_admin=bool(user.is_admin),
                     company_id=str(user.company_id) if user.company_id else None,
                     is_superadmin=bool(user.is_superadmin), role=user.role or "owner",
                     entity_id=str(user.entity_id) if user.entity_id else None)
    return su, user, company, None


def _plain(text: str) -> str:
    """The assistant writes Markdown; a bot message without parse_mode shows it raw."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t.strip()


def _chunks(text: str) -> list[str]:
    out, t = [], text
    while len(t) > MAX_TEXT:
        cut = t.rfind("\n", 0, MAX_TEXT)
        cut = cut if cut > MAX_TEXT // 2 else MAX_TEXT
        out.append(t[:cut])
        t = t[cut:].lstrip()
    if t:
        out.append(t)
    return out


class _Reply:
    """What to send back: collected, then sent after the database work."""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.messages: list[dict[str, Any]] = []
        self.callback: dict[str, Any] | None = None

    def say(self, text: str, **extra) -> None:
        for part in _chunks(text):
            self.messages.append({"method": "sendMessage", "chat_id": self.chat_id, "text": part, **extra})


async def handle_update(db: Session, platform: str, update: dict[str, Any]) -> _Reply | None:
    """Process one update and send the replies. Returns what was sent."""
    if _seen(db, platform, update.get("update_id")):
        return None
    db.commit()
    cfg = load_settings(db).get(platform)
    if not cfg:
        return None
    if "callback_query" in update:
        reply = await _on_callback(db, platform, update["callback_query"])
    elif "message" in update:
        reply = await _on_message(db, platform, cfg, update["message"])
    else:
        return None
    if reply is None:
        return None
    for msg in reply.messages:
        method = msg.pop("method")
        try:
            await api(platform, cfg["token"], method, **msg)
        except Exception:  # noqa: BLE001
            log.warning("could not send %s to %s", method, platform, exc_info=True)
    if reply.callback:
        try:
            await api(platform, cfg["token"], "answerCallbackQuery", **reply.callback)
        except Exception:  # noqa: BLE001
            log.warning("could not answer a callback on %s", platform, exc_info=True)
    return reply


async def _on_message(db: Session, platform: str, cfg: dict, message: dict) -> _Reply | None:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return None
    reply = _Reply(chat_id)
    lang = "fa" if (message.get("from") or {}).get("language_code") == "fa" else "en"
    if chat.get("type") not in (None, "private"):
        reply.say(_t(lang, "groups"))
        return reply
    text = (message.get("text") or "").strip()
    who = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username")

    if text.startswith("/start"):
        code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if not code:
            link = _link_for_chat(db, platform, chat_id)
            reply.say(_t(lang, "hello") if link is None else _t(_lang_of(db, link), "new"))
            return reply
        link = _redeem(db, platform, chat_id, who, code)
        db.commit()
        if link is None:
            reply.say(_t(lang, "bad_code"))
            return reply
        su, user, company, why = _session_user(db, link)
        lang = _lang_of(db, link)
        reply.say(_t(lang, "linked", company=(company.name if company else "—"), user=(user.username if user else "—")))
        return reply

    link = _link_for_chat(db, platform, chat_id)
    if link is None:
        reply.say(_t(lang, "hello"))
        return reply
    lang = _lang_of(db, link)
    if text in ("/stop", "/unlink"):
        from app.db.tenant import tenant_bypass
        with tenant_bypass():
            db.delete(link)
            db.commit()
        reply.say(_t(lang, "unlinked"))
        return reply
    if text == "/new":
        link.session_id = None
        db.commit()
        reply.say(_t(lang, "new"))
        return reply

    if not text and (message.get("voice") or message.get("audio")):
        media = message.get("voice") or message.get("audio")
        text = await _transcribe(db, platform, cfg, link, media)
        if not text:
            reply.say(_t(lang, "voice_failed"))
            return reply
        reply.say(_t(lang, "heard", text=text))
    if not text:
        reply.say(_t(lang, "only_text"))
        return reply
    await _run_turn(db, link, text, lang, reply)
    return reply


def _lang_of(db: Session, link: MessengerLink) -> str:
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    with tenant_bypass():
        user = db.get(User, link.user_id)
    lang = ((user.preferred_language if user else None) or "en").lower()
    return lang if lang in TEXTS else "en"


class _as_user:
    """Run under the link's user: their company scope and actor (for the AI
    budget and the audit trail)."""

    def __init__(self, su):
        self.su = su

    def __enter__(self):
        from app.core.request_context import set_current_user
        from app.db.tenant import use_company
        self._scope = use_company(self.su.company_id) if self.su.company_id else None
        if self._scope:
            self._scope.__enter__()
        set_current_user(self.su)
        return self

    def __exit__(self, *exc):
        from app.core.request_context import clear_current_user
        clear_current_user()
        if self._scope:
            self._scope.__exit__(*exc)
        return False


async def _transcribe(db: Session, platform: str, cfg: dict, link: MessengerLink, media: dict) -> str:
    from app.services.speech import SpeechError, transcribe
    su, *_rest, why = _session_user(db, link)
    if su is None:
        return ""
    try:
        with _as_user(su):
            from app.services.ai_usage import guard_ai_request
            guard_ai_request(db)
            data = await download(platform, cfg["token"], media.get("file_id", ""))
            return (await transcribe(data))["text"]
    except (SpeechError, RuntimeError, httpx.HTTPError):
        return ""
    except Exception:  # noqa: BLE001 — a budget or limit: say nothing was understood
        log.warning("voice note on %s failed", platform, exc_info=True)
        return ""


async def _run_turn(db: Session, link: MessengerLink, text: str, lang: str, reply: _Reply) -> None:
    from app.core.permissions import user_can_access
    from app.services.ai_accountant.orchestrator import run_chat_turn
    su, user, company, why = _session_user(db, link)
    if why:
        reply.say(_t(lang, why))
        return
    if not user_can_access(su, "POST", "/ai-accountant/chat"):
        reply.say(_t(lang, "not_allowed", company=(company.name if company else "—")))
        return
    try:
        with _as_user(su):
            from app.services.ai_usage import guard_ai_request
            guard_ai_request(db)
            started = time.perf_counter()
            result = await run_chat_turn(
                db, user_id=su.user_id, username=su.username, user_message=text,
                session_id=str(link.session_id) if link.session_id else None, lang=lang,
                mode="personal" if su.role == "personal" else "default",
            )
            link.session_id = uuid.UUID(str(result.session_id)) if result.session_id else link.session_id
            link.last_seen_at = datetime.now(timezone.utc)
            db.commit()
            # the owner's review queue takes bot turns too (roadmap §5.5)
            from app.services import ai_review
            ai_review.maybe_sample(db, result, user_id=su.user_id, username=su.username, message=text, lang=lang,
                                   channel=link.platform, latency_ms=int((time.perf_counter() - started) * 1000),
                                   personal=su.role == "personal")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        from app.services.ai_usage import AIBudgetExceeded, AIRateLimited
        key = "busy" if isinstance(exc, (AIBudgetExceeded, AIRateLimited)) else "failed"
        if key == "failed":
            log.warning("bot turn failed", exc_info=True)
        reply.say(_t(lang, key))
        return
    if result.text:
        reply.say(_plain(result.text))
    for p in result.proposals or []:
        token = str(p.get("confirmation_token") or "")
        if not token:
            continue
        reply.say(_plain(p.get("summary") or p.get("tool_name") or "…"), reply_markup={"inline_keyboard": [[
            {"text": _t(lang, "confirm"), "callback_data": f"ok:{token}"},
            {"text": _t(lang, "cancel"), "callback_data": f"no:{token}"},
        ]]})


async def _on_callback(db: Session, platform: str, cq: dict) -> _Reply | None:
    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant.execute_service import (
        ApprovalRequired, PermissionDenied, ProposalCancelled, ProposalExpired, ProposalNotFound, execute_proposal)
    msg = cq.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    reply = _Reply(chat_id)
    reply.callback = {"callback_query_id": cq.get("id")}
    link = _link_for_chat(db, platform, chat_id) if chat_id else None
    action, _, token = (cq.get("data") or "").partition(":")
    lang = _lang_of(db, link) if link else "en"
    if link is None or action not in ("ok", "no") or not token:
        reply.callback["text"] = _t(lang, "unlinked" if link is None else "failed")
        return reply
    su, user, company, why = _session_user(db, link)
    if why:
        reply.callback["text"] = _t(lang, why)
        return reply
    with _as_user(su):
        row = db.execute(select(AIProposal).where(AIProposal.confirmation_token == _uuid(token))).scalars().first()
        if row is None or str(row.user_id) != su.user_id:
            reply.callback["text"] = _t(lang, "not_yours")
            return reply
        if action == "no":
            if row.status == "pending":
                row.status = "cancelled"
                db.commit()
            outcome = _t(lang, "cancelled")
        else:
            try:
                execute_proposal(db, confirmation_token=token, actor_user_id=su.user_id, actor_username=su.username)
                outcome = _t(lang, "done")
            except ApprovalRequired:
                db.commit()
                outcome = _t(lang, "awaiting_approval")
            except ProposalExpired:
                outcome = _t(lang, "expired")
            except (ProposalCancelled, ProposalNotFound):
                outcome = _t(lang, "cancelled")
            except PermissionDenied:
                outcome = _t(lang, "not_yours")
            except Exception:  # noqa: BLE001
                db.rollback()
                log.warning("bot confirm failed", exc_info=True)
                outcome = _t(lang, "failed")
    reply.callback["text"] = outcome
    if msg.get("message_id"):
        # take the buttons off the card and say what happened on it
        reply.messages.append({"method": "editMessageReplyMarkup", "chat_id": chat_id,
                               "message_id": msg["message_id"], "reply_markup": {"inline_keyboard": []}})
    reply.say(outcome)
    return reply


def _uuid(v: str):
    try:
        return uuid.UUID(v)
    except (TypeError, ValueError):
        return None

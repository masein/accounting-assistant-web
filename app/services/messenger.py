"""Messenger bots (roadmap 2026-09 §5.7, part 2).

Telegram, and Bale (بله) — which speaks the Telegram Bot API at its own host
and is reachable where Telegram is filtered. One adapter, two base URLs.

Flow
----
* The platform admin saves a bot token (Settings → Messenger bots): ``getMe``
  checks it, ``setWebhook`` points the bot at
  ``{APP_PUBLIC_URL}/bots/<platform>/webhook/<secret>`` with the same secret
  as ``secret_token`` (both are checked on every call).
* A chat gets in one of three ways (scenarios CU1–CU5):
  - the app's account menu → Connect: a one-time code, valid 10 minutes, as
    ``https://t.me/<bot>?start=<code>``; the bot's ``/start <code>`` links the
    private chat to the login;
  - ``/login`` in the chat: username, password — its message deleted as soon
    as it is read — and the two-factor code when it's on, checked by the same
    code as the web's and the phone's sign-in (limits, suspended companies, the
    first password's lock);
  - ``/signup``: a new person's own books, as the sign-up page makes them, when
    the server allows sign-up (ALLOW_SELF_SIGNUP).
  So someone who only uses Telegram or Bale needs nothing else. A chat's
  sign-in ends with the user's other sessions: a password change, "sign out
  everywhere" or a suspended company (``token_version``), or ``/stop``.
* A message from a linked chat is one turn of the same assistant as the web
  chat — the very same handler (``app.api.ai_accountant.chat``), so the quick
  answers, a bank statement's steps and a receipt's reading are the same —
  run as that user: their company, role (the chat permission is checked every
  time), language and AI budget. A voice note is transcribed first; a photo or
  a file (PDF, CSV, Excel) goes in as the web's attachments do, its caption as
  the message. Each card comes back with Confirm / Cancel buttons; nothing is
  written until Confirm, exactly as on the web. A file the reply has — an
  invoice's or a payslip's PDF, the journal's Excel sheet, a month's close pack —
  is sent as a document (scenarios CU7, CU8).
* Groups only get instructions. A re-delivered update is ignored
  (``messenger_updates``) once it was handled to the end; one the server
  stopped half way through is handled again. The webhook answers at once and
  does the work in the background: an AI turn can take longer than a platform
  waits.

Relay mode
----------
A server that can't reach the platform (Telegram is filtered where production
runs) keeps the bot through a *relay*: a small program on a machine that can
(ops/telegram-relay). The platform admin turns relay mode on and gets a relay
key once; the server keeps only its hash, and no bot token at all. The relay
polls the platform (getUpdates), hands each update to
``POST /bots/<platform>/relay/update`` with the key — and a voice note's, a
photo's or a file's bytes, which the server can't fetch — and makes the calls
the answer lists (sendMessage, sendDocument with the document's bytes,
deleteMessage, answerCallbackQuery, …). Everything else — signing in, the
assistant, Confirm / Cancel — is the same code as above.
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

from app.models.messenger import MessengerLink, MessengerOutbox, MessengerSignIn, MessengerUpdate
from app.core.money import json_dumps

log = logging.getLogger("app.messenger")

PLATFORMS: dict[str, dict[str, str]] = {
    "telegram": {"api": "https://api.telegram.org", "link": "https://t.me/{bot}?start={code}", "name": "Telegram"},
    "bale": {"api": "https://tapi.bale.ai", "link": "https://ble.ir/{bot}?start={code}", "name": "Bale"},
}
SETTINGS_KEY = "messenger_bots"
CODE_TTL = timedelta(minutes=10)
SIGN_IN_TTL = timedelta(minutes=10)
MAX_TEXT = 3900                      # Telegram allows 4096 per message
MAX_VOICE_BYTES = 10 * 1024 * 1024
# a photo or a file sent in, and a document sent out: the app's attachment limit (store_attachment), which is
# also what the Bot API lets a bot download (getFile)
MAX_FILE_BYTES = 20 * 1024 * 1024
# an update the server began and never finished (it stopped half way): handled again on the next try after this
STALE_UPDATE = timedelta(minutes=10)
_TRANSPORT = None                    # tests put an httpx.MockTransport here

TEXTS = {
    "en": {
        "hello": "Hi! I'm your accounting assistant. /login signs in to your account; /signup makes a new one. "
                 "(You can also link this chat from the app: account menu → Telegram / Bale.)",
        "hello_no_signup": "Hi! I'm your accounting assistant. /login signs in to your account. (You can also link "
                           "this chat from the app: account menu → Telegram / Bale.)",
        "login_button": "🔑 Sign in", "signup_button": "✨ New account",
        "linked": "Signed in to {company} as {user}. Tell me what you spent or earned — for example "
                  "\"paid 20 for lunch from the card\" — or send a voice note, a photo of a receipt or a file. /new "
                  "starts a new conversation, /support writes to the support desk, /stop signs this chat out.",
        "bad_code": "That link has expired or was already used — make a new one in the app, or /login here.",
        "unlinked": "This chat is signed out. /login to sign in again.",
        "signed_out": "This chat was signed out (a new password, or signed out everywhere). /login to sign in again.",
        "new": "New conversation started.",
        "not_allowed": "Your role in {company} can't use the assistant.",
        "suspended": "This account is suspended.",
        "groups": "I only work in a private chat.",
        "only_text": "Send text, a voice note, a photo or a file (PDF, CSV, Excel).",
        "voice_failed": "I couldn't make out that voice note — try again, or type it.",
        "heard": "🎙 {text}",
        "confirm": "✅ Confirm", "cancel": "✖ Cancel",
        "done": "✅ Done.", "cancelled": "Cancelled — nothing was saved.",
        "awaiting_approval": "⏳ Sent for approval — it is recorded once someone who can approve confirms it.",
        "expired": "This card has expired — ask again.", "not_yours": "This card isn't yours.",
        "failed": "Something went wrong — nothing was saved. Try again.",
        "busy": "The assistant is busy or over today's limit — try again later.",
        "ask_username": "Your username?",
        "ask_password": "Your password? I'll delete your message as soon as I've read it.",
        "ask_code": "Two-step sign-in is on: the 6-digit code from your authenticator app (or a recovery code).",
        "bad_login": "That username or password isn't right. Your username?",
        "bad_2fa": "That code isn't right — try again, or /cancel.",
        "too_many": "Too many tries — wait a few minutes, then /login again.",
        "timed_out": "That took too long — /login to start again.",
        "verify_first": "Confirm your email address first — the link is in your inbox — then /login here.",
        "first_password": "This account still has its first password. Change it on the web first, then sign in here.",
        "already_in": "This chat is signed in as {user}. /stop signs it out first.",
        "stopped": "Stopped. /login or /signup when you're ready.",
        "signup_off": "New accounts can't be made here on this server — ask whoever runs it for one, then /login.",
        "choose_username": "Choose a username (at least 3 characters, no spaces).",
        "bad_username": "A username has 3 to 64 characters and no spaces — choose another.",
        "username_taken": "That username is taken — choose another.",
        "ask_email": "Your email address? You'll get a link to confirm it.",
        "bad_email": "That doesn't look like an email address — try again.",
        "choose_password": "Choose a password (at least 8 characters). I'll delete your message as soon as I've "
                           "read it.",
        "weak_password": "{reason} Choose another password.",
        "created_verify": "Your account is made. Confirm your email address — the link went to {email} — then "
                          "/login here.",
        "file_too_large": "That file is over 20 MB — send a smaller one.",
        "file_failed": "I couldn't fetch that file — send it again.",
        "unavailable": "The assistant isn't available right now — try again in a little while.",
        "file_caption": "📎 {name}",
        "approval_note": "⚠ Above the approval limit: confirming sends it to someone who can approve it.",
        "support_on": "You're writing to support now: say what went wrong — a screenshot helps. The reply comes "
                      "here. /done goes back to the assistant.",
        "support_sent": "Sent to support — the reply comes here.",
        "support_off": "Back to the assistant.",
        "support_reply": "💬 Support: {text}",
    },
    "fa": {
        "hello": "سلام! من دستیار حسابداری شما هستم. با /login وارد حسابتان شوید؛ با /signup حساب تازه بسازید. "
                 "(می‌توانید این گفتگو را از برنامه هم وصل کنید: منوی حساب ← «تلگرام / بله».)",
        "hello_no_signup": "سلام! من دستیار حسابداری شما هستم. با /login وارد حسابتان شوید. (می‌توانید این گفتگو را "
                           "از برنامه هم وصل کنید: منوی حساب ← «تلگرام / بله».)",
        "login_button": "🔑 ورود", "signup_button": "✨ حساب تازه",
        "linked": "به {company} با کاربر {user} وارد شدید. بگویید چه خرج یا دریافتی داشتید — مثلاً «۵۰ هزار تومن نون "
                  "از کارت» — یا پیام صوتی، عکس رسید یا فایل بفرستید. /new گفتگوی جدید، /support نوشتن به پشتیبانی، /stop خروج این گفتگو.",
        "bad_code": "این لینک منقضی شده یا قبلاً استفاده شده است — در برنامه لینک جدیدی بسازید، یا همین‌جا /login.",
        "unlinked": "این گفتگو از حساب خارج است. برای ورود دوباره /login.",
        "signed_out": "این گفتگو از حساب خارج شد (رمز تازه، یا خروج از همه‌جا). برای ورود دوباره /login.",
        "new": "گفتگوی جدید شروع شد.",
        "not_allowed": "نقش شما در {company} اجازهٔ استفاده از دستیار را ندارد.",
        "suspended": "این حساب معلق است.",
        "groups": "فقط در گفتگوی خصوصی کار می‌کنم.",
        "only_text": "متن، پیام صوتی، عکس یا فایل (PDF، CSV، اکسل) بفرستید.",
        "voice_failed": "پیام صوتی را متوجه نشدم — دوباره بفرستید یا بنویسید.",
        "heard": "🎙 {text}",
        "confirm": "✅ تأیید", "cancel": "✖ انصراف",
        "done": "✅ ثبت شد.", "cancelled": "لغو شد — چیزی ثبت نشد.",
        "awaiting_approval": "⏳ برای تأیید فرستاده شد — پس از تأیید یک نفر دیگر ثبت می‌شود.",
        "expired": "این کارت منقضی شده — دوباره بپرسید.", "not_yours": "این کارت مال شما نیست.",
        "failed": "مشکلی پیش آمد — چیزی ثبت نشد. دوباره امتحان کنید.",
        "busy": "دستیار مشغول است یا سهمیهٔ امروز تمام شده — بعداً امتحان کنید.",
        "ask_username": "نام کاربری‌تان؟",
        "ask_password": "رمز عبورتان؟ پیامتان را همین که خواندم پاک می‌کنم.",
        "ask_code": "ورود دومرحله‌ای روشن است: کد ۶ رقمی برنامهٔ احراز هویت (یا یکی از کدهای بازیابی).",
        "bad_login": "نام کاربری یا رمز درست نیست. نام کاربری‌تان؟",
        "bad_2fa": "این کد درست نیست — دوباره بفرستید، یا /cancel.",
        "too_many": "تلاش‌ها زیاد شد — چند دقیقه صبر کنید، بعد دوباره /login.",
        "timed_out": "طول کشید — برای شروع دوباره /login.",
        "verify_first": "اول نشانی ایمیلتان را تأیید کنید — لینکش در صندوق ایمیلتان است — بعد همین‌جا /login.",
        "first_password": "این حساب هنوز رمز اولیه‌اش را دارد. اول در وب رمز را عوض کنید، بعد همین‌جا وارد شوید.",
        "already_in": "این گفتگو با کاربر {user} وارد است. برای خروج اول /stop.",
        "stopped": "متوقف شد. هر وقت آماده بودید /login یا /signup.",
        "signup_off": "روی این سرور نمی‌شود این‌جا حساب تازه ساخت — از گردانندهٔ آن حساب بخواهید، بعد /login.",
        "choose_username": "یک نام کاربری انتخاب کنید (دست‌کم ۳ نویسه، بدون فاصله).",
        "bad_username": "نام کاربری ۳ تا ۶۴ نویسه و بدون فاصله است — نام دیگری انتخاب کنید.",
        "username_taken": "این نام کاربری گرفته شده — نام دیگری انتخاب کنید.",
        "ask_email": "نشانی ایمیلتان؟ لینکی برای تأییدش می‌گیرید.",
        "bad_email": "این نشانی ایمیل به نظر درست نیست — دوباره بفرستید.",
        "choose_password": "یک رمز عبور انتخاب کنید (دست‌کم ۸ نویسه). پیامتان را همین که خواندم پاک می‌کنم.",
        "weak_password": "{reason} رمز دیگری انتخاب کنید.",
        "created_verify": "حسابتان ساخته شد. نشانی ایمیلتان را تأیید کنید — لینک به {email} رفت — بعد همین‌جا /login.",
        "file_too_large": "این فایل بیش از ۲۰ مگابایت است — فایل کوچک‌تری بفرستید.",
        "file_failed": "نتوانستم فایل را بگیرم — دوباره بفرستید.",
        "unavailable": "دستیار الان در دسترس نیست — کمی بعد دوباره امتحان کنید.",
        "file_caption": "📎 {name}",
        "approval_note": "⚠ بالاتر از سقف تأیید: با تأیید، برای کسی فرستاده می‌شود که اجازهٔ تأییدش را دارد.",
        "support_on": "حالا به پشتیبانی می‌نویسید: بگویید چه مشکلی پیش آمد — تصویر صفحه کمک می‌کند. پاسخ همین‌جا "
                      "می‌آید. /done برمی‌گردد به دستیار.",
        "support_sent": "برای پشتیبانی فرستاده شد — پاسخ همین‌جا می‌آید.",
        "support_off": "برگشتید به دستیار.",
        "support_reply": "💬 پشتیبانی: {text}",
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
        if platform not in PLATFORMS or not isinstance(cfg, dict):
            continue
        if cfg.get("mode") == "relay" and cfg.get("relay_key_hash"):
            out[platform] = dict(cfg)                       # no token: the relay holds it
        elif cfg.get("token"):
            out[platform] = {**cfg, "token": decrypt_secret(cfg["token"])}
    return out


def _save(db: Session, bots: dict[str, dict[str, Any]]) -> None:
    import json
    from app.core.secrets import encrypt_secret
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    value = json_dumps({p: ({**c, "token": encrypt_secret(c["token"])} if c.get("token") else dict(c))
                        for p, c in bots.items()})
    with tenant_bypass():
        row = _row(db)
        if row is None:
            db.add(AppSetting(key=SETTINGS_KEY, value=value, company_id=None))
        else:
            row.value = value
        db.flush()


def public_status(db: Session) -> dict[str, Any]:
    bots = load_settings(db)
    out = {}
    for p, meta in PLATFORMS.items():
        cfg = bots.get(p) or {}
        out[p] = {"name": meta["name"], "connected": p in bots, "username": cfg.get("username"),
                  "mode": cfg.get("mode", "webhook") if cfg else None,
                  "relay_seen_at": cfg.get("relay_seen_at")}
    return out


# --- the Bot API ----------------------------------------------------------------------------------------------

async def api(platform: str, token: str, method: str, _file: dict | None = None, **params) -> Any:
    """One Bot API call. ``_file`` ({field, name, mime, data}) sends a document's bytes along (multipart), as
    sendDocument needs."""
    url = f"{PLATFORMS[platform]['api']}/bot{token}/{method}"
    timeout = httpx.Timeout(60.0 if _file else 20.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, transport=_TRANSPORT) as client:
        if _file:
            import json
            form = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                    for k, v in params.items() if v is not None}
            r = await client.post(url, data=form,
                                  files={_file["field"]: (_file["name"], _file["data"], _file["mime"])})
        else:
            r = await client.post(url, json={k: v for k, v in params.items() if v is not None})
    body = r.json() if r.content else {}
    if not body.get("ok"):
        raise RuntimeError(f"{platform} {method}: {body.get('description') or r.status_code}")
    return body.get("result")


async def download(platform: str, token: str, file_id: str, limit: int = MAX_VOICE_BYTES) -> bytes:
    info = await api(platform, token, "getFile", file_id=file_id)
    if int(info.get("file_size") or 0) > limit:
        raise RuntimeError("file too large")
    url = f"{PLATFORMS[platform]['api']}/file/bot{token}/{info['file_path']}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0), transport=_TRANSPORT) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content[: limit + 1]


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
    if cfg and cfg.get("token"):
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


# --- relay mode -----------------------------------------------------------------------------------------------

def _key_hash(key: str) -> str:
    import hashlib
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()


def enable_relay(db: Session, platform: str) -> str:
    """Relay mode for ``platform``: a fresh relay key, returned once; only its hash is kept. Replaces a webhook
    setup (and any earlier relay key). Raises ValueError for an unknown platform."""
    if platform not in PLATFORMS:
        raise ValueError("Unknown platform.")
    key = secrets.token_urlsafe(32)
    bots = load_settings(db)
    # no username until the relay checks in with its bot's own (it may be another bot than a webhook's)
    bots[platform] = {"mode": "relay", "relay_key_hash": _key_hash(key), "username": "",
                      "connected_at": datetime.now(timezone.utc).isoformat(), "relay_seen_at": None}
    _save(db, bots)
    return key


def relay_config(db: Session, platform: str, key: str | None) -> dict[str, Any] | None:
    """The relay setup when ``key`` is its relay key, else None (a wrong key, or not in relay mode)."""
    cfg = load_settings(db).get(platform) if platform in PLATFORMS else None
    if not cfg or cfg.get("mode") != "relay" or not key:
        return None
    return cfg if secrets.compare_digest(_key_hash(key), cfg.get("relay_key_hash") or "") else None


def relay_hello(db: Session, platform: str, username: str) -> dict[str, Any]:
    """The relay checking in (on start, and every few minutes): the bot's username — for the links the app
    shows — and when the relay was last seen."""
    bots = load_settings(db)
    cfg = bots[platform]
    username = (username or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", username):
        raise ValueError("That is not a bot username.")
    cfg.update(username=username, relay_seen_at=datetime.now(timezone.utc).isoformat())
    _save(db, bots)
    return public_status(db)[platform]


def relay_calls(reply: "_Reply | None") -> list[dict[str, Any]]:
    """The Bot API calls a reply stands for, for the relay to make in order. A document goes with its bytes
    (``files``: {field: {name, mime, b64}}), which the relay sends as the platform's upload."""
    import base64
    if reply is None:
        return []
    calls = []
    for m in reply.messages:
        call: dict[str, Any] = {"method": m["method"],
                                "params": {k: v for k, v in m.items() if k not in ("method", "_file")}}
        f = m.get("_file")
        if f:
            call["files"] = {f["field"]: {"name": f["name"], "mime": f["mime"],
                                          "b64": base64.b64encode(f["data"]).decode("ascii")}}
        calls.append(call)
    if reply.callback:
        calls.append({"method": "answerCallbackQuery", "params": dict(reply.callback)})
    return calls


# --- linking --------------------------------------------------------------------------------------------------

def start_link(db: Session, *, user_id: str, platform: str) -> dict[str, Any]:
    """A pending link with a fresh code; the old pending ones of this user go."""
    bots = load_settings(db)
    if platform not in bots:
        raise ValueError("That messenger is not connected on this server.")
    if not bots[platform].get("username"):
        raise ValueError("The bot's relay hasn't checked in yet — try again in a few minutes.")
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


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _redeem(db: Session, platform: str, chat_id: str, chat_name: str | None, code: str) -> MessengerLink | None:
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    now = datetime.now(timezone.utc)
    with tenant_bypass():
        link = db.execute(select(MessengerLink).where(MessengerLink.code == code, MessengerLink.platform == platform,
                                                      MessengerLink.status == "pending")).scalars().first()
        if link is None:
            return None
        exp = _utc(link.code_expires_at)
        if exp is None or exp < now:
            db.delete(link)
            db.flush()
            return None
        # a chat belongs to one login: an older link of this chat goes, and a sign-in it had begun
        db.execute(delete(MessengerLink).where(MessengerLink.platform == platform, MessengerLink.chat_id == chat_id,
                                               MessengerLink.id != link.id))
        db.execute(delete(MessengerSignIn).where(MessengerSignIn.platform == platform,
                                                 MessengerSignIn.chat_id == chat_id))
        user = db.get(User, link.user_id)
        link.status, link.chat_id, link.chat_name = "active", chat_id, (chat_name or "")[:128]
        link.code = link.code_expires_at = None
        link.last_seen_at = now
        link.token_version, link.via = int(user.token_version or 0) if user else 0, "code"
        db.flush()
        return link


def _open_link(db: Session, platform: str, chat_id: str, chat_name: str | None, user, *, via: str) -> MessengerLink:
    """The chat signed in as ``user`` (a password, a code or a new account typed in the chat): any earlier
    login of this chat goes, as with a link from the app, and the sign-in conversation with it."""
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        db.execute(delete(MessengerLink).where(MessengerLink.platform == platform, MessengerLink.chat_id == chat_id))
        db.execute(delete(MessengerSignIn).where(MessengerSignIn.platform == platform,
                                                 MessengerSignIn.chat_id == chat_id))
        link = MessengerLink(platform=platform, user_id=user.id, company_id=user.company_id, status="active",
                             chat_id=chat_id, chat_name=(chat_name or "")[:128], token_version=int(user.token_version or 0),
                             via=via, last_seen_at=datetime.now(timezone.utc))
        db.add(link)
        db.flush()
    return link


# --- handling one update ---------------------------------------------------------------------------------------

def _begin_update(db: Session, platform: str, update_id: int | None) -> bool:
    """True when this update is to be handled now: never seen, or begun more than STALE_UPDATE ago and never
    finished (the server stopped half way — handled again rather than lost, scenario CU9). False for one
    handled already, or being handled right now (a platform re-sending while a slow turn runs)."""
    if update_id is None:
        return True
    from sqlalchemy.exc import IntegrityError
    now = datetime.now(timezone.utc)
    row = db.execute(select(MessengerUpdate).where(MessengerUpdate.platform == platform,
                                                   MessengerUpdate.update_id == int(update_id))).scalars().first()
    if row is None:
        try:
            with db.begin_nested():
                db.add(MessengerUpdate(platform=platform, update_id=int(update_id), received_at=now))
                db.flush()
        except IntegrityError:
            return False                                      # another worker took it this instant
        return True
    if row.done_at is not None or (_utc(row.received_at) or now) > now - STALE_UPDATE:
        return False
    row.received_at = now
    db.flush()
    return True


def _end_update(db: Session, platform: str, update_id: int | None, *, done: bool) -> None:
    """Handled to the end (``done``), or failed — then forgotten, so the platform's or the relay's next try
    is handled instead of ignored."""
    if update_id is None:
        return
    row = db.execute(select(MessengerUpdate).where(MessengerUpdate.platform == platform,
                                                   MessengerUpdate.update_id == int(update_id))).scalars().first()
    if row is None:
        return
    if done:
        row.done_at = datetime.now(timezone.utc)
    else:
        db.delete(row)
    db.commit()


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
    if int(link.token_version or 0) != int(user.token_version or 0):
        # a new password, "sign out everywhere" or a suspension since the chat got in: as for a browser (CU6)
        return None, user, company, "signed_out"
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

    def forget(self, message_id: Any) -> None:
        """Take a message out of the chat: a password or a code typed in it."""
        if message_id:
            self.messages.append({"method": "deleteMessage", "chat_id": self.chat_id, "message_id": message_id})

    def document(self, name: str, mime: str, data: bytes, caption: str | None = None) -> None:
        self.messages.append({"method": "sendDocument", "chat_id": self.chat_id, "caption": caption,
                              "_file": {"field": "document", "name": name, "mime": mime, "data": data}})


async def handle_update(db: Session, platform: str, update: dict[str, Any], *,
                        files: dict[str, bytes] | None = None, deliver: bool = True) -> _Reply | None:
    """Process one update and send the replies. Returns what was sent. Through a relay (``deliver=False``)
    nothing is sent here — the relay makes the calls (``relay_calls``) — and a voice note's, a photo's or a
    file's bytes come in ``files`` ({file_id: bytes}): the server can't fetch them from the platform."""
    update_id = update.get("update_id")
    if not _begin_update(db, platform, update_id):
        return None
    db.commit()
    try:
        cfg = load_settings(db).get(platform)
        if not cfg:
            _end_update(db, platform, update_id, done=True)
            return None
        if files is not None:
            cfg = {**cfg, "_files": files}
        if "callback_query" in update:
            reply = await _on_callback(db, platform, update["callback_query"])
        elif "message" in update:
            reply = await _on_message(db, platform, cfg, update["message"])
        else:
            reply = None
    except BaseException:
        db.rollback()
        try:
            _end_update(db, platform, update_id, done=False)
        except Exception:  # noqa: BLE001 — the update's own error is the one to report
            log.warning("could not forget a failed update", exc_info=True)
        raise
    _end_update(db, platform, update_id, done=True)
    if deliver and cfg.get("token"):
        try:                                           # a support reply that couldn't go before goes now
            await flush_outbox(db, platform)
        except Exception:  # noqa: BLE001
            db.rollback()
    if reply is None or not deliver:
        return reply
    for msg in reply.messages:
        method, params = msg["method"], {k: v for k, v in msg.items() if k != "method"}
        try:
            await api(platform, cfg["token"], method, **params)
        except Exception:  # noqa: BLE001
            log.warning("could not send %s to %s", method, platform, exc_info=True)
    if reply.callback:
        try:
            await api(platform, cfg["token"], "answerCallbackQuery", **reply.callback)
        except Exception:  # noqa: BLE001
            log.warning("could not answer a callback on %s", platform, exc_info=True)
    return reply


def _command(text: str) -> str:
    """``/login@my_bot extra`` → ``/login``; '' when the text isn't a command."""
    if not text.startswith("/"):
        return ""
    return text.split(maxsplit=1)[0].split("@", 1)[0].lower()


def _chat_lang(message: dict) -> str:
    """The language of a chat that isn't signed in: its Telegram app's."""
    return "fa" if (message.get("from") or {}).get("language_code") == "fa" else "en"


def _app_lang(message: dict) -> str | None:
    """The app language a new account starts in: the Telegram app's, when the app has it."""
    code = ((message.get("from") or {}).get("language_code") or "").lower()[:2]
    return code if code in ("en", "fa", "ar", "es") else None


def _hello(db: Session, reply: _Reply, lang: str) -> None:
    from app.core.config import settings
    open_signup = bool(settings.allow_self_signup)
    buttons = [{"text": _t(lang, "login_button"), "callback_data": "auth:login"}]
    if open_signup:
        buttons.append({"text": _t(lang, "signup_button"), "callback_data": "auth:signup"})
    reply.say(_t(lang, "hello" if open_signup else "hello_no_signup"),
              reply_markup={"inline_keyboard": [buttons]})


async def _on_message(db: Session, platform: str, cfg: dict, message: dict) -> _Reply | None:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return None
    reply = _Reply(chat_id)
    lang = _chat_lang(message)
    if chat.get("type") not in (None, "private"):
        reply.say(_t(lang, "groups"))
        return reply
    text = (message.get("text") or "").strip()
    cmd = _command(text)
    who = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username")

    if cmd == "/start":
        code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if not code:
            link = _link_for_chat(db, platform, chat_id)
            if link is None:
                _hello(db, reply, lang)
            else:                                             # says a new conversation, and starts one
                link.session_id = None
                db.commit()
                reply.say(_t(_lang_of(db, link), "new"))
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
        await _signed_out_chat(db, platform, chat_id, who, message, text, cmd, lang, reply)
        return reply
    lang = _lang_of(db, link)
    if cmd in ("/stop", "/unlink", "/logout"):
        _forget_link(db, link)
        reply.say(_t(lang, "unlinked"))
        return reply
    if cmd == "/new":
        link.session_id, link.mode = None, None
        db.commit()
        reply.say(_t(lang, "new"))
        return reply
    if cmd == "/done":                                  # back from the support desk to the assistant (DA9)
        link.mode = None
        db.commit()
        reply.say(_t(lang, "support_off"))
        return reply
    if cmd in ("/login", "/signin", "/signup", "/register"):
        reply.say(_t(lang, "already_in", user=_username_of(db, link)))
        return reply

    su, user, company, why = _session_user(db, link)
    if why in ("signed_out", "unlinked"):
        _forget_link(db, link)                                # the chat is out: /login works again
        reply.say(_t(lang, why))
        return reply
    if cmd == "/support":                               # the support desk, from the chat (DA9)
        link.mode = "support"
        db.commit()
        rest = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if not rest:
            reply.say(_t(lang, "support_on"))
            return reply
        text, cmd = rest, ""

    attachment_ids: list[str] = []
    media = _document_of(message)
    if media is not None:
        if why:
            reply.say(_t(lang, why))
            return reply
        attachment = await _take_file(db, platform, cfg, su, media, lang, reply)
        if attachment is None:
            return reply
        attachment_ids.append(attachment)
        text = (message.get("caption") or "").strip()
    elif not text and (message.get("voice") or message.get("audio")):
        media = message.get("voice") or message.get("audio")
        text = await _transcribe(db, platform, cfg, link, media)
        if not text:
            reply.say(_t(lang, "voice_failed"))
            return reply
        reply.say(_t(lang, "heard", text=text))
    if not text and not attachment_ids:
        reply.say(_t(lang, "only_text"))
        return reply
    if link.mode == "support":
        if why:
            reply.say(_t(lang, why))
            return reply
        _to_support(db, platform, link, su, text, attachment_ids, lang, reply)
        return reply
    await _run_turn(db, platform, link, text, lang, reply, attachment_ids=attachment_ids)
    return reply


def _to_support(db: Session, platform: str, link: MessengerLink, su, text: str, attachment_ids: list[str],
                lang: str, reply: _Reply) -> None:
    """A message for the support desk rather than the assistant: the person's support conversation, with this
    chat to answer in (app/services/support_desk.py)."""
    from app.services import support_desk as S
    try:
        with _as_user(su):
            S.post(db, user_id=su.user_id, text=text, attachment_ids=attachment_ids,
                   context={"page": "bot", "platform": platform, "language": lang}, channel=platform,
                   chat=(platform, str(link.chat_id)))
            db.commit()
    except (S.SupportLimit, S.SupportError) as e:
        db.rollback()
        from app.core.messages import localize_detail
        reply.say(localize_detail(str(e), lang))
        return
    reply.say(_t(lang, "support_sent"))


# --- the outbox: the support desk's replies, outside any update (DA9) --------------------------------------------

def queue_support_reply(db: Session, platform: str, chat_id: str, text: str, user_id: str) -> None:
    """The desk's reply for a chat: sent now by a bot on a webhook, fetched by the relay otherwise."""
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    if platform not in PLATFORMS or not chat_id:
        return
    with tenant_bypass():
        user = db.get(User, uuid.UUID(str(user_id)))
    lang = ((user.preferred_language if user else None) or "en").lower()
    lang = lang if lang in TEXTS else "en"
    for part in _chunks(_t(lang, "support_reply", text=text)):
        db.add(MessengerOutbox(platform=platform, chat_id=str(chat_id), text=part))
    db.commit()
    cfg = load_settings(db).get(platform) or {}
    if cfg.get("token") and cfg.get("mode") != "relay":
        try:
            import asyncio
            asyncio.get_running_loop()
        except RuntimeError:                              # no loop here (a sync route): send it now
            import asyncio
            asyncio.run(flush_outbox(db, platform))


def _waiting(db: Session, platform: str, limit: int = 20) -> list[MessengerOutbox]:
    return db.execute(select(MessengerOutbox).where(MessengerOutbox.platform == platform,
                                                    MessengerOutbox.sent_at.is_(None))
                      .order_by(MessengerOutbox.created_at).limit(limit)).scalars().all()


async def flush_outbox(db: Session, platform: str) -> int:
    """A bot on a webhook: send what waits (oldest first); one that fails waits for the next try."""
    cfg = load_settings(db).get(platform) or {}
    if not cfg.get("token"):
        return 0
    sent = 0
    for row in _waiting(db, platform):
        try:
            await api(platform, cfg["token"], "sendMessage", chat_id=row.chat_id, text=row.text)
        except Exception:  # noqa: BLE001 — kept for the next try
            log.warning("could not send a support reply on %s", platform, exc_info=True)
            break
        row.sent_at = datetime.now(timezone.utc)
        sent += 1
    db.commit()
    return sent


def relay_outbox(db: Session, platform: str) -> list[dict[str, Any]]:
    """What the relay sends for us (it fetches every few seconds): the waiting messages, marked sent."""
    rows = _waiting(db, platform)
    now = datetime.now(timezone.utc)
    for row in rows:
        row.sent_at = now
    db.commit()
    return [{"method": "sendMessage", "params": {"chat_id": r.chat_id, "text": r.text}} for r in rows]


def _forget_link(db: Session, link: MessengerLink) -> None:
    from app.db.tenant import tenant_bypass
    with tenant_bypass():
        db.delete(link)
        db.commit()


def _username_of(db: Session, link: MessengerLink) -> str:
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    with tenant_bypass():
        user = db.get(User, link.user_id)
    return user.username if user else "—"


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


# --- signing in, and a new account, in the chat (scenarios CU1–CU5) ----------------------------------------------

def _bot_request(platform: str, chat_id: str):
    """What the sign-in code reads of a request: the "client" is the chat (limits per chat, the chat in the
    audit rows), never a forwarded address."""
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": f"/bots/{platform}", "headers": [],
                    "query_string": b"", "client": (f"{platform}:{chat_id}"[:64], 0)})


def _sign_in_of(db: Session, platform: str, chat_id: str) -> MessengerSignIn | None:
    row = db.execute(select(MessengerSignIn).where(MessengerSignIn.platform == platform,
                                                   MessengerSignIn.chat_id == chat_id)).scalars().first()
    if row is not None and (_utc(row.expires_at) or datetime.now(timezone.utc)) < datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        return None
    return row


def _begin_sign_in(db: Session, platform: str, chat_id: str, purpose: str, lang: str) -> MessengerSignIn:
    db.execute(delete(MessengerSignIn).where(MessengerSignIn.platform == platform, MessengerSignIn.chat_id == chat_id))
    row = MessengerSignIn(platform=platform, chat_id=chat_id, purpose=purpose, step="username", lang=lang,
                          expires_at=datetime.now(timezone.utc) + SIGN_IN_TTL)
    db.add(row)
    db.commit()
    return row


def _end_sign_in(db: Session, row: MessengerSignIn) -> None:
    db.delete(row)
    db.commit()


def _detail(exc, lang: str) -> str:
    from app.core.messages import localize_detail
    detail = getattr(exc, "detail", None)
    return localize_detail(detail, lang) if isinstance(detail, str) else str(detail or "")


async def _signed_out_chat(db: Session, platform: str, chat_id: str, who: str | None, message: dict, text: str,
                           cmd: str, lang: str, reply: _Reply) -> None:
    """A chat no login is in: /login, /signup, a sign-in's next answer, or the greeting."""
    from app.core.config import settings
    if cmd in ("/cancel", "/stop"):
        row = _sign_in_of(db, platform, chat_id)
        if row is not None:
            _end_sign_in(db, row)
        reply.say(_t(lang, "stopped"))
        return
    if cmd in ("/login", "/signin"):
        _begin_sign_in(db, platform, chat_id, "login", lang)
        reply.say(_t(lang, "ask_username"))
        return
    if cmd in ("/signup", "/register"):
        if not settings.allow_self_signup:
            reply.say(_t(lang, "signup_off"))
            return
        _begin_sign_in(db, platform, chat_id, "signup", lang)
        reply.say(_t(lang, "choose_username"))
        return
    row = _sign_in_of(db, platform, chat_id) if text and not cmd else None
    if row is None:
        _hello(db, reply, lang)
        return
    if row.purpose == "signup":
        await _signup_step(db, platform, chat_id, who, message, row, text, reply)
    else:
        await _login_step(db, platform, chat_id, who, message, row, text, reply)


async def _login_step(db: Session, platform: str, chat_id: str, who: str | None, message: dict,
                      row: MessengerSignIn, text: str, reply: _Reply) -> None:
    from fastapi import HTTPException

    from app.api.auth import password_step, second_factor_step
    lang = row.lang if row.lang in TEXTS else "en"
    # every answer leaves the chat whatever comes of it: a password typed where the username was asked again
    # (after a wrong one) must not stay on the screen either
    reply.forget(message.get("message_id"))
    if row.step == "username":
        row.username, row.step = text[:64], "password"
        db.commit()
        reply.say(_t(lang, "ask_password"))
        return
    request = _bot_request(platform, chat_id)
    if row.step == "password":
        try:
            user, company, must_change, challenge = password_step(row.username or "", text, request, db)
        except HTTPException as e:
            db.rollback()
            if e.status_code == 401:
                row.username, row.step = None, "username"
                db.commit()
                reply.say(_t(lang, "bad_login"))
            else:
                _end_sign_in(db, row)
                reply.say(_t(lang, "too_many") if e.status_code == 429 else _refusal(e, lang))
            return
        if must_change:
            _end_sign_in(db, row)
            reply.say(_t(lang, "first_password"))
            return
        if challenge:
            row.challenge, row.step = challenge, "code"
            db.commit()
            reply.say(_t(lang, "ask_code"))
            return
    elif row.step == "code":
        try:
            user, company, must_change, _method, _left = second_factor_step(
                row.challenge or "", text, request, db, via=PLATFORMS[platform]["name"])
        except HTTPException as e:
            db.rollback()
            code = (e.headers or {}).get("X-Error-Code")
            if e.status_code == 401 and code != "signin_timed_out":
                reply.say(_t(lang, "bad_2fa"))
                return
            _end_sign_in(db, row)
            reply.say(_t(lang, "timed_out") if e.status_code == 401 else
                      _t(lang, "too_many") if e.status_code == 429 else _refusal(e, lang))
            return
        if must_change:
            _end_sign_in(db, row)
            reply.say(_t(lang, "first_password"))
            return
    else:                                                     # a step this version doesn't know: start again
        _end_sign_in(db, row)
        reply.say(_t(lang, "timed_out"))
        return
    _signed_in(db, platform, chat_id, who, user, company, via="password", reply=reply)


def _refusal(exc, lang: str) -> str:
    """A refused sign-in in the reader's words: a suspended company, an address still to confirm."""
    detail = str(getattr(exc, "detail", "") or "")
    if "suspended" in detail:
        return _t(lang, "suspended")
    if "email" in detail.lower():
        return _t(lang, "verify_first")
    return _detail(exc, lang)


def _signed_in(db: Session, platform: str, chat_id: str, who: str | None, user, company, *, via: str,
               reply: _Reply) -> None:
    from app.core.audit import audit_log
    link = _open_link(db, platform, chat_id, who, user, via=via)
    audit_log(db, action="login", entity_type="user", entity_id=str(user.id), user_id=str(user.id),
              username=user.username, detail=f"{PLATFORMS[platform]['name']} chat ({via})",
              ip_address=f"{platform}:{chat_id}"[:64])
    db.commit()
    lang = _lang_of(db, link)
    reply.say(_t(lang, "linked", company=(company.name if company else "—"), user=user.username))


_USERNAME = re.compile(r"\S{3,64}")


async def _signup_step(db: Session, platform: str, chat_id: str, who: str | None, message: dict,
                       row: MessengerSignIn, text: str, reply: _Reply) -> None:
    from fastapi import HTTPException

    from app.api.auth import _signup_limiter, create_personal_account, valid_email
    from app.core.config import settings
    from app.db.tenant import tenant_bypass
    from app.models.user import User
    from app.services.email_verification import verification_required
    lang = row.lang if row.lang in TEXTS else "en"
    if not settings.allow_self_signup:                        # turned off since the chat began
        _end_sign_in(db, row)
        reply.say(_t(lang, "signup_off"))
        return
    if row.step == "username":
        name = text.strip()
        if not _USERNAME.fullmatch(name):
            reply.say(_t(lang, "bad_username"))
            return
        with tenant_bypass():
            taken = db.execute(select(User.id).where(User.username == name)).first() is not None
        if taken:
            reply.say(_t(lang, "username_taken"))
            return
        row.username = name
        row.step = "email" if verification_required() else "password"
        db.commit()
        reply.say(_t(lang, "ask_email" if row.step == "email" else "choose_password"))
        return
    if row.step == "email":
        if not valid_email(text):
            reply.say(_t(lang, "bad_email"))
            return
        row.email, row.step = text.strip()[:254], "password"
        db.commit()
        reply.say(_t(lang, "choose_password"))
        return
    reply.forget(message.get("message_id"))                 # the password leaves the chat whatever comes of it
    if row.step != "password":
        _end_sign_in(db, row)
        reply.say(_t(lang, "timed_out"))
        return
    key = f"{platform}:{chat_id}"[:64]
    if not _signup_limiter.is_allowed(db, key):
        _end_sign_in(db, row)
        reply.say(_t(lang, "too_many"))
        return
    try:
        company, user, pending = create_personal_account(
            db, username=row.username or "", password=text, email=row.email, display_name=who,
            language=_app_lang(message) or lang, ip=key, via=PLATFORMS[platform]["name"])
    except HTTPException as e:
        db.rollback()
        detail = str(e.detail or "")
        if "exists" in detail.lower() or "taken" in detail.lower():
            row.username, row.step = None, "username"
            db.commit()
            reply.say(_t(lang, "username_taken"))
        else:                                                 # a weak password: choose another
            reply.say(_t(lang, "weak_password", reason=_detail(e, lang)))
        return
    if pending:
        _end_sign_in(db, row)
        reply.say(_t(lang, "created_verify", email=user.email or row.email or ""))
        return
    _signed_in(db, platform, chat_id, who, user, company, via="signup", reply=reply)


# --- files in -------------------------------------------------------------------------------------------------

def _document_of(message: dict) -> dict | None:
    """A photo or a file sent in: {file_id, name, mime, size} — the photo's largest size the app takes."""
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        fits = [p for p in photos if int(p.get("file_size") or 0) <= MAX_FILE_BYTES] or photos[:1]
        best = max(fits, key=lambda p: int(p.get("file_size") or 0) or int(p.get("width") or 0))
        return {"file_id": best.get("file_id", ""), "name": "photo.jpg", "mime": "image/jpeg",
                "size": int(best.get("file_size") or 0)}
    doc = message.get("document")
    if isinstance(doc, dict) and doc.get("file_id"):
        return {"file_id": doc["file_id"], "name": doc.get("file_name") or "file", "mime": doc.get("mime_type") or "",
                "size": int(doc.get("file_size") or 0)}
    return None


async def _take_file(db: Session, platform: str, cfg: dict, su, media: dict, lang: str, reply: _Reply) -> str | None:
    """The file as one of the web's attachments (its type checked by its bytes, 20 MB at most); its id, or
    None when the reply already says why not."""
    from fastapi import HTTPException

    from app.api.transactions import attachment_type, store_attachment
    if media["size"] > MAX_FILE_BYTES:
        reply.say(_t(lang, "file_too_large"))
        return None
    try:
        if cfg.get("mode") == "relay":
            sent = cfg.get("_files") or {}
            # the relay picks the photo's size as _document_of does; one file sent is that one either way
            data = sent.get(media["file_id"]) or (next(iter(sent.values())) if len(sent) == 1 else None)
        else:
            data = await download(platform, cfg["token"], media["file_id"], limit=MAX_FILE_BYTES)
    except (RuntimeError, httpx.HTTPError):
        log.warning("could not fetch a file on %s", platform, exc_info=True)
        data = None
    if not data:
        reply.say(_t(lang, "file_failed"))
        return None
    if len(data) > MAX_FILE_BYTES:
        reply.say(_t(lang, "file_too_large"))
        return None
    try:
        with _as_user(su):
            row = store_attachment(db, data, media["name"], attachment_type(media["mime"], media["name"]))
            db.commit()
            return str(row.id)
    except HTTPException as e:                                # a type the app doesn't take, or bytes that lie
        db.rollback()
        reply.say(_detail(e, lang))
        return None


async def _transcribe(db: Session, platform: str, cfg: dict, link: MessengerLink, media: dict) -> str:
    from app.services.speech import SpeechError, transcribe
    su, *_rest, why = _session_user(db, link)
    if su is None:
        return ""
    try:
        with _as_user(su):
            from app.services.ai_usage import guard_ai_request
            guard_ai_request(db)
            if cfg.get("mode") == "relay":
                data = (cfg.get("_files") or {}).get(media.get("file_id", ""))
                if not data or len(data) > MAX_VOICE_BYTES:
                    return ""                                 # the relay didn't send it (too large, or failed)
            else:
                data = await download(platform, cfg["token"], media.get("file_id", ""))
            return (await transcribe(data))["text"]
    except (SpeechError, RuntimeError, httpx.HTTPError):
        return ""
    except Exception:  # noqa: BLE001 — a budget or limit: say nothing was understood
        log.warning("voice note on %s failed", platform, exc_info=True)
        return ""


# --- one turn of the assistant ----------------------------------------------------------------------------------

async def _run_turn(db: Session, platform: str, link: MessengerLink, text: str, lang: str, reply: _Reply, *,
                    attachment_ids: list[str] | None = None) -> None:
    """The web chat's own handler, as the phone's chat runs it: the quick answers, a statement's steps, a
    receipt's reading and the never-mind and bare-"confirm" guards are the same in the bot (scenario CU7)."""
    from fastapi import HTTPException

    from app.api.ai_accountant import ChatPayload, chat_channel
    from app.api.ai_accountant import chat as web_chat
    from app.core.permissions import user_can_access
    from app.services.ai_accountant import blocks as B
    from app.services.ai_usage import AIBudgetExceeded, AIRateLimited
    su, user, company, why = _session_user(db, link)
    if why:
        reply.say(_t(lang, why))
        return
    if not user_can_access(su, "POST", "/ai-accountant/chat"):
        reply.say(_t(lang, "not_allowed", company=(company.name if company else "—")))
        return
    try:
        with _as_user(su), chat_channel(platform):
            resp = await web_chat(ChatPayload(message=text, session_id=str(link.session_id) if link.session_id else None,
                                              attachment_ids=list(attachment_ids or [])), db=db, user=su)
            link.session_id = uuid.UUID(str(resp.session_id)) if resp.session_id else link.session_id
            link.last_seen_at = datetime.now(timezone.utc)
            db.commit()
            from app.services.ai_accountant.orchestrator import turn_language
            reply_lang = turn_language(db, text, resp.session_id, lang)
            blocks = B.build_blocks(db, text=resp.text, proposals=resp.proposals, tool_calls=resp.tool_calls,
                                    intake=resp.intake, lang=reply_lang)
            say_lang = reply_lang if reply_lang in TEXTS else lang
            _render(db, su, blocks, say_lang, reply)
    except HTTPException as e:
        db.rollback()
        if (e.headers or {}).get("X-Error-Code") == "ai_unavailable":
            reply.say(_t(lang, "unavailable"))
        else:
            reply.say(_detail(e, lang) or _t(lang, "failed"))
    except (AIBudgetExceeded, AIRateLimited):
        db.rollback()
        reply.say(_t(lang, "busy"))
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("bot turn failed", exc_info=True)
        reply.say(_t(lang, "failed"))


def _amount(value, lang: str) -> str:
    from app.core.money import grouped, grouped_fa
    return grouped_fa(value) if lang == "fa" else grouped(value)


def _card_text(block: dict, lang: str) -> str:
    """A card as the phone draws it — what, how much, when, each line with its account's name — in plain text,
    from the card itself rather than the tool's first summary (a line it later moved to a named place would
    read wrong, scenario CU7)."""
    lines = block.get("lines") or []
    if not lines:
        text = _plain(block.get("summary") or block.get("title") or "…")
    else:
        head = [f"📝 {block.get('title') or ''}".rstrip()]
        amount = block.get("amount") or {}
        when = ((block.get("date") or {}).get("display")) or ""
        figure = f"{_amount(amount.get('value'), lang)} {amount.get('currency') or ''}".strip() if amount else ""
        if figure or when:
            head.append(" · ".join(x for x in (figure, when) if x))
        dr, cr = ("بدهکار", "بستانکار") if lang == "fa" else ("Dr", "Cr")
        for ln in lines:
            if ln.get("debit"):
                head.append(f"  {dr} {ln.get('name')}: {_amount(ln['debit'], lang)}")
            if ln.get("credit"):
                head.append(f"  {cr} {ln.get('name')}: {_amount(ln['credit'], lang)}")
        for e in block.get("new_entities") or []:
            if isinstance(e, dict) and e.get("name"):
                head.append(f"  + {e['name']}")
        text = "\n".join(head)
    if block.get("needs_approval"):
        text += "\n" + _t(lang, "approval_note")
    return text


def _render(db: Session, su, blocks: list[dict], lang: str, reply: _Reply) -> None:
    """The reply's blocks as messages: the words (or, with none, what each card reads as), each card with its
    buttons, each file as a document."""
    has_text = any(b.get("type") == "text" and (b.get("text") or "").strip() for b in blocks)
    for b in blocks:
        kind = b.get("type")
        if kind == "text":
            words = _plain(b.get("text") or "")
            choices = [str(c) for c in (b.get("choices") or [])]
            if choices:                                       # what the web and the phone show as buttons
                words += "\n" + "\n".join(f"• {c}" for c in choices)
            if words:
                reply.say(words)
        elif kind not in ("proposal", "file") and not has_text and (b.get("fallback_text") or "").strip():
            reply.say(_plain(b["fallback_text"]))
    for b in blocks:
        if b.get("type") == "proposal" and b.get("token"):
            reply.say(_card_text(b, lang), reply_markup={"inline_keyboard": [[
                {"text": _t(lang, "confirm"), "callback_data": f"ok:{b['token']}"},
                {"text": _t(lang, "cancel"), "callback_data": f"no:{b['token']}"},
            ]]})
    for b in blocks:
        if b.get("type") == "file":
            _send_file(db, su, b, lang, reply)


def _send_file(db: Session, su, block: dict, lang: str, reply: _Reply) -> None:
    """A file block as a document in the chat, when the user may open it (the phone's rule: its path's
    permission) and it fits a bot's message."""
    from app.core.permissions import user_can_access
    from app.services.ai_accountant.files_out import FileOutError, file_bytes
    path = str(block.get("path") or "").split("?", 1)[0]
    if path and not user_can_access(su, "GET", path):
        return
    try:
        with _as_user(su):
            name, mime, data = file_bytes(db, block)
    except FileOutError as e:
        reply.say(str(e))
        return
    except Exception:  # noqa: BLE001 — the words already went; say the file didn't
        log.warning("could not make %s", block.get("id"), exc_info=True)
        reply.say(_t(lang, "failed"))
        return
    if len(data) > MAX_FILE_BYTES:
        reply.say(_t(lang, "file_too_large"))
        return
    reply.document(name, mime, data, caption=_t(lang, "file_caption", name=name))


async def _on_callback(db: Session, platform: str, cq: dict) -> _Reply | None:
    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant.execute_service import (
        ApprovalRequired, PermissionDenied, ProposalCancelled, ProposalExpired, ProposalNotFound, cancel_proposal,
        execute_proposal)
    msg = cq.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    reply = _Reply(chat_id)
    reply.callback = {"callback_query_id": cq.get("id")}
    action, _, token = (cq.get("data") or "").partition(":")
    link = _link_for_chat(db, platform, chat_id) if chat_id else None
    if action == "auth" and chat_id:                          # the greeting's Sign in / New account buttons
        lang = _chat_lang(cq)
        if link is not None:
            reply.say(_t(_lang_of(db, link), "already_in", user=_username_of(db, link)))
        else:
            await _signed_out_chat(db, platform, chat_id, None, {}, "", "/login" if token == "login" else "/signup",
                                   lang, reply)
        return reply
    lang = _lang_of(db, link) if link else "en"
    if link is None or action not in ("ok", "no") or not token:
        reply.callback["text"] = _t(lang, "unlinked" if link is None else "failed")
        return reply
    su, user, company, why = _session_user(db, link)
    if why:
        if why == "signed_out":
            _forget_link(db, link)
        reply.callback["text"] = _t(lang, why)
        return reply
    file_block = None
    with _as_user(su):
        row = db.execute(select(AIProposal).where(AIProposal.confirmation_token == _uuid(token))).scalars().first()
        if row is None or str(row.user_id) != su.user_id:
            reply.callback["text"] = _t(lang, "not_yours")
            return reply
        if action == "no":
            try:                                              # withdraws an approval it asked for too
                cancel_proposal(db, confirmation_token=token, actor_user_id=su.user_id)
                outcome = _t(lang, "cancelled")
            except ProposalCancelled:
                outcome = _t(lang, "done")                    # it was confirmed already
            except (ProposalNotFound, PermissionDenied):
                outcome = _t(lang, "not_yours")
        else:
            try:
                result = execute_proposal(db, confirmation_token=token, actor_user_id=su.user_id,
                                          actor_username=su.username, ip_address=f"{platform}:{chat_id}"[:64])
                outcome = _t(lang, "done")
                from app.services.ai_accountant.blocks import made_document
                file_block = made_document(db, result.audit_log_id)
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
    if file_block:                                            # the invoice the card made, as its PDF (CU8)
        _send_file(db, su, file_block, lang, reply)
    return reply


def _uuid(v: str):
    try:
        return uuid.UUID(v)
    except (TypeError, ValueError):
        return None

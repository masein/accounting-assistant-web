"""Bank statements by e-mail (roadmap 2026-09 §4.1, part 2).

Many banks e-mail a statement each month (or on request) as a CSV, Excel file
or PDF. The owner saves the IMAP mailbox those e-mails arrive in and the
senders they come from. Every 30 minutes (or on "Check now") the server:

* connects over TLS only, and only to a public address — the one it checked,
  so a name that changes in between can't point it inside the network
  (app/core/public_address.py);
* opens the folder **read-only**: nothing in the mailbox is marked, moved or
  deleted;
* looks at the last days' messages, and only at those from an allowed sender
  (an address, or ``@domain``);
* files each attached statement through the same import as an upload
  (``import_statement_bytes``: duplicate check, parsing, OCR for a PDF), and
  marks it ``origin="email"``; a PDF the bank locked is opened with the
  saved PDF password (app/services/pdf_unlock.py), or logged
  ``needs_password``;
* records every message it read in ``statement_mail_messages``, so a message
  is never imported twice.

Nothing is posted: the statements wait on the Bank statements page to be
reviewed and approved like any other. The password is stored encrypted
(app/core/secrets.py) and never returned. A forged sender can at most add a
statement waiting for review.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import json
import logging
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

log = logging.getLogger("app.statement_mailbox")

SETTINGS_KEY = "statement_mailbox"
STATUS_KEY = "statement_mailbox_status"
DEFAULTS: dict[str, Any] = {"enabled": False, "host": "", "port": 993, "username": "", "folder": "INBOX",
                            "senders": []}
FIRST_LOOK_DAYS = 14          # how far back the first check reads
OVERLAP_DAYS = 2              # later checks re-read a little (the table makes it harmless)
MAX_MESSAGES = 25             # newest messages from allowed senders per check
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_SENDERS = 20
TIMEOUT = 20
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^[^\s\"\\]{1,254}$")          # it goes into LOGIN unquoted
# the match goes into SEARCH FROM "…": no quotes, backslashes, brackets or spaces in it
_SENDER_RE = re.compile(r"^(?:[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+)?@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$")

# The ways a check can fail, as codes the page translates.
ERROR_CODES = ("resolve", "private", "connect", "timeout", "tls", "auth", "folder", "protocol")


class MailboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- settings -------------------------------------------------------------------------------------------------

def _row(db: Session, key: str) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == key)).scalars().first()


def _read(db: Session, key: str) -> dict[str, Any]:
    row = _row(db, key)
    try:
        return json.loads(row.value) if row and row.value else {}
    except ValueError:
        return {}


def _write(db: Session, key: str, value: dict[str, Any]) -> None:
    row = _row(db, key)
    text = json.dumps(value, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=key, value=text))
    else:
        row.value = text
    db.flush()


def load_settings(db: Session, *, reveal: bool = False) -> dict[str, Any]:
    """The saved mailbox. The password is only decrypted with ``reveal`` (for
    the connection); otherwise the answer just says whether there is one."""
    from app.core.secrets import decrypt_secret
    raw = _read(db, SETTINGS_KEY)
    out = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
    out["has_password"] = bool(raw.get("password"))
    out["has_pdf_password"] = bool(raw.get("pdf_password"))
    if reveal:
        out["password"] = decrypt_secret(raw.get("password") or "") if raw.get("password") else ""
        out["pdf_password"] = decrypt_secret(raw.get("pdf_password") or "") if raw.get("pdf_password") else ""
    return out


def parse_senders(lines: list[dict[str, Any]] | str) -> list[dict[str, str]]:
    """Sender rules from the form: a list of {match, bank_name}, or text with one
    ``address-or-@domain, bank name`` per line."""
    if isinstance(lines, str):
        items = []
        for line in lines.splitlines():
            line = line.strip()
            if not line:
                continue
            match, _, bank = line.replace("،", ",").partition(",")        # the Persian comma too
            items.append({"match": match, "bank_name": bank})
        lines = items
    out: list[dict[str, str]] = []
    for item in lines or []:
        match = str(item.get("match") or "").strip().lower()
        bank = str(item.get("bank_name") or "").strip()[:60]
        if not _SENDER_RE.match(match):
            raise ValueError(f"'{match or '—'}' isn't an e-mail address or @domain.")
        if match not in {s["match"] for s in out}:
            out.append({"match": match, "bank_name": bank})
    if len(out) > MAX_SENDERS:
        raise ValueError(f"At most {MAX_SENDERS} senders.")
    return out


def save_settings(db: Session, data: dict[str, Any], *, resolver=None) -> dict[str, Any]:
    """Validate and store the mailbox. A blank password keeps the saved one.
    The host must resolve to a public address now, and is checked again on
    every connection."""
    from app.core.public_address import NotPublic, public_addresses
    from app.core.secrets import encrypt_secret
    current = _read(db, SETTINGS_KEY)
    host = str(data.get("host") or "").strip().lower()
    username = str(data.get("username") or "").strip()
    folder = str(data.get("folder") or "INBOX").strip() or "INBOX"
    try:
        port = 993 if data.get("port") in (None, "") else int(data["port"])
    except (TypeError, ValueError):
        port = 0
    enabled = bool(data.get("enabled"))
    senders = parse_senders(data.get("senders") or [])
    if host and not _HOST_RE.match(host):
        raise ValueError("The server name isn't valid.")
    if not 1 <= port <= 65535:
        raise ValueError("The port is 1–65535 (IMAP over TLS is usually 993).")
    if username and not _USER_RE.match(username):
        raise ValueError("The username can't contain spaces or quotes.")
    if len(folder) > 100 or any(c in folder for c in "\r\n\"\\"):
        raise ValueError("The folder name isn't valid.")
    password = data.get("password")
    stored = current.get("password")
    if password:
        if len(password) > 256 or any(c in password for c in "\r\n"):
            raise ValueError("The password isn't valid.")
        stored = encrypt_secret(password)
    if host != current.get("host") or username != current.get("username"):
        # a password belongs to its account: a new server or user needs it again
        stored = encrypt_secret(password) if password else None
    # the password banks lock their PDF statements with: kept when left blank,
    # independent of the mail account (it belongs to the statements)
    pdf_stored = current.get("pdf_password")
    pdf_password = data.get("pdf_password")
    if data.get("clear_pdf_password"):
        pdf_stored = None
    elif pdf_password:
        from app.services.pdf_unlock import MAX_PASSWORD
        if len(pdf_password) > MAX_PASSWORD or any(c in pdf_password for c in "\r\n"):
            raise ValueError("The PDF password isn't valid.")
        pdf_stored = encrypt_secret(pdf_password)
    if enabled and not (host and username and stored and senders):
        raise ValueError("To check automatically, fill in the server, username, password and at least one sender.")
    if host:
        try:
            public_addresses(host, port, resolver=resolver or _resolve)
        except NotPublic as exc:
            raise ValueError(str(exc)) from exc
    _write(db, SETTINGS_KEY, {"enabled": enabled, "host": host, "port": port, "username": username,
                              "password": stored, "folder": folder, "senders": senders,
                              "pdf_password": pdf_stored})
    if pdf_password and not data.get("clear_pdf_password"):
        # a new PDF password: the locked statements already seen get another go —
        # forget them, and have the next check look back the full first window
        from sqlalchemy import delete

        from app.models.statement_mail import StatementMailMessage
        db.execute(delete(StatementMailMessage).where(StatementMailMessage.status == "needs_password"))
        _save_status(db, {**load_status(db), "rescan": True})
    return load_settings(db)


def load_status(db: Session) -> dict[str, Any]:
    return _read(db, STATUS_KEY)


def _save_status(db: Session, status: dict[str, Any]) -> None:
    _write(db, STATUS_KEY, status)


# --- the connection ---------------------------------------------------------------------------------------------

class _PinnedIMAP(imaplib.IMAP4_SSL):
    """IMAP over TLS to the address that was checked, with the certificate
    still verified against the server's name."""

    def __init__(self, host: str, port: int, *, address: str, ssl_context: ssl.SSLContext, timeout: float) -> None:
        self._address = address                  # set before __init__ opens the connection
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self._address, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def _resolve(host: str, port: int) -> list[str]:
    from app.core.public_address import resolve
    return resolve(host, port)


def _connect(host: str, port: int):
    """An IMAP client connected to a vetted public address (tests replace this)."""
    from app.core.public_address import NotPublic, public_addresses
    try:
        address = public_addresses(host, port, resolver=_resolve)[0]
    except NotPublic as exc:
        raise MailboxError(exc.code, str(exc)) from exc
    try:
        return _PinnedIMAP(host, port, address=address, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
    except ssl.SSLCertVerificationError as exc:
        raise MailboxError("tls", f"the certificate isn't valid for {host}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise MailboxError("timeout", f"no answer from {host}") from exc
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailboxError("connect", f"couldn't connect to {host}:{port}") from exc


def _quote(folder: str) -> str:
    return '"' + folder + '"'                    # the name was checked for quotes and backslashes


def _open(cfg: dict[str, Any]):
    conn = _connect(cfg["host"], int(cfg["port"]))
    try:
        try:
            conn.login(cfg["username"], cfg["password"])
        except imaplib.IMAP4.error as exc:
            raise MailboxError("auth", "the server refused the username or password") from exc
        typ, data = conn.select(_quote(cfg["folder"]), readonly=True)
        if typ != "OK":
            raise MailboxError("folder", f"there's no folder called {cfg['folder']}")
        try:
            count = int((data or [b"0"])[0] or 0)
        except (TypeError, ValueError):
            count = 0
        return conn, count
    except Exception:
        _close(conn)
        raise


def _close(conn) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001 — closing is best effort
        pass


def test_connection(db: Session, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Log in and open the folder with what's in the form — nothing is read,
    nothing is saved. A blank password uses the saved one, but only for the
    saved server and username."""
    saved = load_settings(db, reveal=True)
    form = data or {}
    host = str(form.get("host") or saved["host"]).strip().lower()
    username = str(form.get("username") or saved["username"]).strip()
    folder = str(form.get("folder") or saved["folder"] or "INBOX").strip()
    try:
        port = int(form.get("port") or saved["port"] or 993)
    except (TypeError, ValueError):
        port = 0
    same_account = (host, username) == (saved["host"], saved["username"])
    password = form.get("password") or (saved["password"] if same_account else "")
    if not (host and username and password):
        return {"ok": False, "error_code": "auth", "error": "fill in the server, username and password"}
    if not _HOST_RE.match(host) or not _USER_RE.match(username) or not 1 <= port <= 65535 \
            or any(c in folder for c in "\r\n\"\\") or any(c in password for c in "\r\n"):
        return {"ok": False, "error_code": "protocol", "error": "the server, port, username or folder isn't valid"}
    cfg = {"host": host, "port": port, "username": username, "password": password, "folder": folder}
    try:
        conn, count = _open(cfg)
    except MailboxError as exc:
        return {"ok": False, "error_code": exc.code, "error": str(exc)}
    _close(conn)
    return {"ok": True, "messages": count, "folder": cfg["folder"]}


# --- reading messages -----------------------------------------------------------------------------------------

@dataclass
class _Header:
    uid: bytes
    size: int
    sender: str
    display: str
    subject: str
    received_at: datetime | None
    key: str


def sender_rule(address: str, senders: list[dict[str, str]]) -> dict[str, str] | None:
    """The rule an address falls under: the exact address, or its domain
    (``@bank.ir`` also covers ``mail.bank.ir``)."""
    address = (address or "").strip().lower()
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1]
    for rule in senders:
        m = rule["match"]
        if m == address:
            return rule
        if m.startswith("@") and (domain == m[1:] or domain.endswith("." + m[1:])):
            return rule
    return None


def _since(status: dict[str, Any], today: date) -> date:
    last = status.get("checked_at")
    if last and not status.get("rescan"):
        try:
            return datetime.fromisoformat(last).date() - timedelta(days=OVERLAP_DAYS)
        except ValueError:
            pass
    return today - timedelta(days=FIRST_LOOK_DAYS)


def _imap_date(d: date) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def search_criteria(since: date, senders: list[dict[str, str]]) -> list[str]:
    """SEARCH keys: since the date, and FROM any sender — filtered on the
    server, so a busy inbox isn't read message by message. ``FROM`` matches a
    substring, so ``@bank.ir`` is searched as ``bank.ir`` (it also covers
    mail.bank.ir); ``sender_rule`` then decides exactly."""
    keys = [f'FROM "{r["match"].lstrip("@")}"' for r in senders]

    def either(ks: list[str]) -> str:
        return ks[0] if len(ks) == 1 else f"OR {ks[0]} {either(ks[1:])}"

    return ["SINCE", _imap_date(since)] + ([either(keys)] if keys else [])


def _parts(data) -> tuple[bytes, bytes]:
    """(the response line, the literal) from an imaplib FETCH answer."""
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2:
            return item[0] or b"", item[1] or b""
    raise MailboxError("protocol", "the server gave an unexpected answer")


def _header(conn, uid: bytes, host: str, folder: str) -> _Header:
    typ, data = conn.uid("FETCH", uid, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
    if typ != "OK":
        raise MailboxError("protocol", "the server gave an unexpected answer")
    line, raw = _parts(data)
    m = re.search(rb"RFC822\.SIZE (\d+)", line)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    display, sender = parseaddr(str(msg.get("From", "")))
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
    except (TypeError, ValueError, IndexError):
        received = None
    if received is not None and received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    message_id = str(msg.get("Message-ID") or "").strip()
    key_src = message_id or f"{host}|{folder}|{uid.decode(errors='replace')}|{msg.get('Date')}|{msg.get('Subject')}"
    return _Header(uid=uid, size=int(m.group(1)) if m else 0, sender=sender.strip().lower(),
                   display=display.strip(), subject=str(msg.get("Subject") or "")[:300], received_at=received,
                   key=hashlib.sha256(key_src.encode("utf-8", "replace")).hexdigest())


def attachments(raw: bytes) -> tuple[list[tuple[str, str, bytes]], int]:
    """(statement files, how many were too large) in a message."""
    from app.api.brain import MAX_STATEMENT_UPLOAD_BYTES
    from app.services.statement_import import SUPPORTED_STATEMENT_EXTENSIONS
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    files, too_large = [], 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if not name:
            continue
        name = Path(str(name).replace("\\", "/")).name[:200] or "statement"
        if Path(name).suffix.lower() not in SUPPORTED_STATEMENT_EXTENSIONS:
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        if len(payload) > MAX_STATEMENT_UPLOAD_BYTES:
            too_large += 1
            continue
        files.append((name, part.get_content_type(), payload))
    return files[:MAX_ATTACHMENTS], too_large


def _import(db: Session, name: str, ctype: str, content: bytes, bank: str,
            pdf_password: str | None = None) -> tuple[str, str | None, str | None]:
    """File one attachment: (outcome, statement id, detail)."""
    import asyncio

    from fastapi import HTTPException

    from app.models.bank_statement import BankStatement
    from app.services.statement_import import import_statement_bytes
    try:
        res = asyncio.run(import_statement_bytes(db, content=content, filename=name, content_type=ctype,
                                                 bank_name=bank, pdf_password=pdf_password))
    except HTTPException as exc:
        db.rollback()
        return "failed", None, str(exc.detail)[:300]
    except Exception:  # noqa: BLE001 — one unreadable file must not stop the check
        db.rollback()
        log.exception("statement mailbox: importing %s failed", name)
        return "failed", None, "the file couldn't be read"
    if res.status == "duplicate":
        return "duplicate", str(res.duplicate_of) if res.duplicate_of else None, None
    if res.status == "needs_password":
        return "needs_password", None, "; ".join(res.errors or [])[:300] or None
    if res.status == "needs_mapping" or not res.id:
        return "needs_mapping", None, "; ".join(res.errors or [])[:300] or None
    stmt = db.get(BankStatement, res.id)
    if stmt is not None:
        stmt.origin = "email"
        db.commit()
    return "imported", str(res.id), None


def _record(db: Session, h: _Header, status: str, statement_ids: list[str], detail: str | None) -> None:
    from app.models.statement_mail import StatementMailMessage
    db.add(StatementMailMessage(message_key=h.key, sender=h.sender[:254], subject=h.subject or None,
                                received_at=h.received_at, status=status,
                                statement_ids=json.dumps(statement_ids) if statement_ids else None,
                                detail=(detail or None) and detail[:500]))
    db.commit()


_RANK = {"imported": 0, "needs_password": 1, "needs_mapping": 2, "failed": 3, "too_large": 4, "duplicate": 5}


def check_mailbox(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Read the mailbox once. Returns (and saves) the status: when, whether it
    worked, how many new messages from the banks, how many statements filed."""
    from app.models.statement_mail import StatementMailMessage
    now = now or datetime.now(timezone.utc)
    cfg = load_settings(db, reveal=True)
    status = load_status(db)
    if not (cfg["host"] and cfg["username"] and cfg["has_password"] and cfg["senders"]):
        raise MailboxError("auth", "the mailbox isn't set up")
    result = {"checked_at": now.isoformat(), "ok": True, "found": 0, "imported": 0,
              "error_code": None, "error": None, "failures": 0}
    try:
        if not cfg["password"]:                          # saved, but no longer decrypts (AUTH_SECRET changed)
            raise MailboxError("auth", "the saved password can't be read any more — enter it again")
        conn, _count = _open(cfg)
        try:
            typ, data = conn.uid("SEARCH", None, *search_criteria(_since(status, now.date()), cfg["senders"]))
            if typ != "OK":
                raise MailboxError("protocol", "the server gave an unexpected answer")
            uids = (data[0] or b"").split() if data else []
            wanted: list[_Header] = []
            for uid in list(reversed(uids))[:MAX_MESSAGES * 4]:      # newest first, a bounded look
                if len(wanted) >= MAX_MESSAGES:
                    break
                h = _header(conn, uid, cfg["host"], cfg["folder"])
                rule = sender_rule(h.sender, cfg["senders"])
                if rule is None:
                    continue
                seen = db.execute(select(StatementMailMessage.id)
                                  .where(StatementMailMessage.message_key == h.key)).first()
                if seen is None:
                    wanted.append(h)
                    h.display = rule["bank_name"] or h.display or h.sender.rsplit("@", 1)[-1]
            for h in reversed(wanted):                      # file them oldest first
                result["found"] += 1
                if h.size > MAX_MESSAGE_BYTES:
                    _record(db, h, "too_large", [], None)
                    continue
                typ, data = conn.uid("FETCH", h.uid, "(BODY.PEEK[])")
                if typ != "OK":
                    raise MailboxError("protocol", "the server gave an unexpected answer")
                _line, raw = _parts(data)
                files, too_large = attachments(raw)
                if not files:
                    _record(db, h, "too_large" if too_large else "no_attachment", [], None)
                    continue
                outcomes = [_import(db, name, ctype, content, h.display, cfg.get("pdf_password") or None)
                            for name, ctype, content in files]
                ids = [sid for o, sid, _d in outcomes if o == "imported" and sid]
                result["imported"] += len(ids)
                best = min((o for o, _s, _d in outcomes), key=lambda o: _RANK.get(o, 9))
                details = "; ".join(d for _o, _s, d in outcomes if d)
                _record(db, h, best, ids or [s for _o, s, _d in outcomes if s], details or None)
        finally:
            _close(conn)
    except MailboxError as exc:
        db.rollback()
        result.update(ok=False, error_code=exc.code, error=str(exc)[:300],
                      failures=int(status.get("failures") or 0) + 1)
    except (imaplib.IMAP4.error, OSError) as exc:           # the connection dropped, or an answer timed out
        db.rollback()
        timeout = isinstance(exc, (socket.timeout, TimeoutError))
        result.update(ok=False, error_code="timeout" if timeout else "protocol",
                      error=f"no answer from {cfg['host']}" if timeout else "the server gave an unexpected answer",
                      failures=int(status.get("failures") or 0) + 1)
    _save_status(db, result)
    db.commit()
    return result


def is_enabled(db: Session) -> bool:
    return bool(_read(db, SETTINGS_KEY).get("enabled"))


def recent_messages(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    from app.models.statement_mail import StatementMailMessage
    rows = db.execute(select(StatementMailMessage).order_by(StatementMailMessage.created_at.desc(),
                                                            StatementMailMessage.id.desc())
                      .limit(limit)).scalars().all()
    return [{"id": str(r.id), "sender": r.sender, "subject": r.subject, "status": r.status,
             "received_at": r.received_at.isoformat() if r.received_at else None,
             "checked_at": r.created_at.isoformat() if r.created_at else None,
             "statement_ids": json.loads(r.statement_ids) if r.statement_ids else [], "detail": r.detail}
            for r in rows]

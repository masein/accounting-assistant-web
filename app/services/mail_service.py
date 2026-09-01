"""Outgoing mail.

One place that knows how to talk to SMTP, so the rest of the app can ask for
an email without caring about ports, TLS modes or what happens when the mail
server is unreachable.

Design points that matter in this deployment:

* **Off unless configured.** No ``SMTP_HOST`` means mail is disabled and every
  send is a no-op returning False. That is also what keeps the test suite off
  the network, and it means an air-gapped install simply doesn't send mail
  rather than erroring everywhere.
* **Never raises at the caller.** A signup must not fail because the mail
  server is down, and a digest run must not abort halfway. Callers get a bool
  and decide; failures are logged.
* **Both TLS modes.** DirectAdmin offers 587/STARTTLS and 465/implicit SSL and
  installs differ, so both are supported rather than assuming one.
* Credentials are never logged, including in error paths.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings

logger = logging.getLogger(__name__)


class MailNotConfigured(RuntimeError):
    """Raised only by paths that need to report the reason to an operator."""


def mail_configured() -> bool:
    """True when there is enough config to attempt a send."""
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def sender_address() -> str:
    return (settings.smtp_from or settings.smtp_user or "").strip()


def build_message(
    *, to: str | list[str], subject: str, text: str, html: str | None = None
) -> EmailMessage:
    """Compose the message. Separated from sending so it can be asserted on
    without a server."""
    recipients = [to] if isinstance(to, str) else list(to)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, sender_address()))
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)
    if html:
        # Clients that can render HTML take the alternative; the rest — and
        # anything indexing the mail — still get readable text.
        msg.add_alternative(html, subtype="html")
    return msg


def _connect() -> smtplib.SMTP:
    if settings.smtp_use_ssl:
        context = ssl.create_default_context()
        return smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port,
            timeout=settings.smtp_timeout, context=context,
        )
    server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout)
    if settings.smtp_starttls:
        server.starttls(context=ssl.create_default_context())
    return server


def send_email(
    *, to: str | list[str], subject: str, text: str, html: str | None = None
) -> bool:
    """Send one message. Returns whether it went out; never raises."""
    if not mail_configured():
        logger.debug("mail not configured — skipping send of %r", subject)
        return False
    recipients = [to] if isinstance(to, str) else list(to)
    if not any((r or "").strip() for r in recipients):
        logger.warning("mail send skipped: no recipient for %r", subject)
        return False

    msg = build_message(to=recipients, subject=subject, text=text, html=html)
    try:
        with _connect() as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("mail sent: %r to %d recipient(s)", subject, len(recipients))
        return True
    except Exception as e:  # noqa: BLE001 - a mail failure must not break callers
        # str(e) can carry the server's rejection text but never our password.
        logger.warning("mail send failed for %r: %s: %s", subject, type(e).__name__, e)
        return False


def send_test_email(to: str) -> tuple[bool, str]:
    """Operator-facing check of the SMTP settings.

    Returns (ok, message) with the reason on failure, because "it didn't work"
    is useless when configuring a mail host.
    """
    if not mail_configured():
        return False, "SMTP is not configured (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD)."
    if not (to or "").strip():
        return False, "No recipient address given."

    subject = "Accounting Assistant — test email"
    text = (
        "This is a test message from Accounting Assistant.\n\n"
        f"If you received it, outgoing mail is working: host {settings.smtp_host} "
        f"port {settings.smtp_port}, sending as {sender_address()}."
    )
    try:
        msg = build_message(to=to, subject=subject, text=text)
        with _connect() as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True, f"Test email sent to {to}."
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP rejected the username or password."
    except smtplib.SMTPRecipientsRefused:
        return False, f"The server refused the recipient {to}."
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"

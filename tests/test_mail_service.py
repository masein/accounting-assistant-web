"""Outgoing mail.

The properties worth pinning are the failure ones: mail is off unless
configured, a send never raises at the caller, and no code path can leak the
SMTP password. Nothing here opens a socket — the SMTP client is stubbed.
"""
from __future__ import annotations

import smtplib

import pytest

from app.core.config import settings
from app.services import mail_service as mail


@pytest.fixture
def smtp_configured():
    original = {k: getattr(settings, k) for k in
                ("smtp_host", "smtp_port", "smtp_user", "smtp_password",
                 "smtp_from", "smtp_from_name", "smtp_use_ssl", "smtp_starttls")}
    settings.smtp_host = "mail.netixsystem.com"
    settings.smtp_port = 587
    settings.smtp_user = "assistant@netixsystem.com"
    settings.smtp_password = "s3cr3t-not-logged"
    settings.smtp_from = None
    settings.smtp_from_name = "Accounting Assistant"
    settings.smtp_use_ssl = False
    settings.smtp_starttls = True
    yield
    for k, v in original.items():
        setattr(settings, k, v)


class _FakeSMTP:
    """Records what would have been sent."""
    instances: list["_FakeSMTP"] = []

    def __init__(self, host=None, port=None, timeout=None, context=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in_as = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in_as = user

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeSMTP.instances.clear()
    yield
    _FakeSMTP.instances.clear()


@pytest.fixture
def fake_smtp(monkeypatch):
    monkeypatch.setattr(mail.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


# ---------------------------------------------------------------------------
# Off unless configured
# ---------------------------------------------------------------------------
def test_mail_is_off_by_default():
    """Which is also what keeps the suite off the network."""
    assert mail.mail_configured() is False
    assert mail.send_email(to="a@b.com", subject="x", text="y") is False


def test_partial_config_is_not_configured(smtp_configured):
    settings.smtp_password = None
    assert mail.mail_configured() is False


def test_nothing_is_sent_when_unconfigured(fake_smtp):
    mail.send_email(to="a@b.com", subject="x", text="y")
    assert fake_smtp.instances == []


# ---------------------------------------------------------------------------
# Composing
# ---------------------------------------------------------------------------
def test_the_message_carries_subject_sender_and_recipient(smtp_configured):
    msg = mail.build_message(to="user@example.com", subject="Hello", text="Body")
    assert msg["Subject"] == "Hello"
    assert msg["To"] == "user@example.com"
    assert "assistant@netixsystem.com" in msg["From"]
    assert "Accounting Assistant" in msg["From"]


def test_smtp_from_overrides_the_login_address(smtp_configured):
    settings.smtp_from = "noreply@netixsystem.com"
    assert "noreply@netixsystem.com" in mail.build_message(
        to="u@e.com", subject="s", text="t")["From"]


def test_multiple_recipients_are_joined(smtp_configured):
    msg = mail.build_message(to=["a@e.com", "b@e.com"], subject="s", text="t")
    assert "a@e.com" in msg["To"] and "b@e.com" in msg["To"]


def test_html_is_added_as_an_alternative_keeping_the_text_part(smtp_configured):
    """Clients that can't render HTML must still get something readable."""
    msg = mail.build_message(to="u@e.com", subject="s", text="plain words",
                             html="<p>rich words</p>")
    assert msg.is_multipart()
    types = {p.get_content_type() for p in msg.walk() if p.get_content_maintype() == "text"}
    assert {"text/plain", "text/html"} <= types
    assert "plain words" in msg.get_body(("plain",)).get_content()


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def test_a_configured_send_logs_in_and_delivers(smtp_configured, fake_smtp):
    assert mail.send_email(to="user@example.com", subject="Hi", text="There") is True
    server = fake_smtp.instances[0]
    assert server.host == "mail.netixsystem.com" and server.port == 587
    assert server.started_tls is True
    assert server.logged_in_as == "assistant@netixsystem.com"
    assert len(server.sent) == 1


def test_implicit_ssl_skips_starttls(smtp_configured, fake_smtp):
    """Port 465 negotiates TLS on connect; calling STARTTLS there is an error."""
    settings.smtp_use_ssl = True
    settings.smtp_port = 465
    mail.send_email(to="u@e.com", subject="s", text="t")
    assert fake_smtp.instances[0].started_tls is False


def test_starttls_can_be_disabled(smtp_configured, fake_smtp):
    settings.smtp_starttls = False
    mail.send_email(to="u@e.com", subject="s", text="t")
    assert fake_smtp.instances[0].started_tls is False


def test_an_empty_recipient_is_refused(smtp_configured, fake_smtp):
    assert mail.send_email(to="   ", subject="s", text="t") is False
    assert fake_smtp.instances == []


# ---------------------------------------------------------------------------
# Failure must not reach the caller
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("boom", [
    smtplib.SMTPAuthenticationError(535, b"bad credentials"),
    smtplib.SMTPServerDisconnected("connection lost"),
    OSError("network unreachable"),
    TimeoutError("timed out"),
])
def test_send_returns_false_instead_of_raising(smtp_configured, monkeypatch, boom):
    """A signup must not fail because the mail server is down."""
    def _explode(*a, **kw):
        raise boom

    monkeypatch.setattr(mail, "_connect", _explode)
    assert mail.send_email(to="u@e.com", subject="s", text="t") is False


def test_a_failure_never_logs_the_password(smtp_configured, monkeypatch, caplog):
    def _explode(*a, **kw):
        raise smtplib.SMTPAuthenticationError(535, b"authentication failed")

    monkeypatch.setattr(mail, "_connect", _explode)
    with caplog.at_level("DEBUG"):
        mail.send_email(to="u@e.com", subject="s", text="t")
    assert "s3cr3t-not-logged" not in caplog.text


# ---------------------------------------------------------------------------
# Operator test-send
# ---------------------------------------------------------------------------
def test_test_email_explains_when_unconfigured():
    ok, message = mail.send_test_email("me@example.com")
    assert ok is False
    assert "not configured" in message.lower()


def test_test_email_reports_a_bad_password_specifically(smtp_configured, monkeypatch):
    """'It didn't work' is useless when configuring a mail host."""
    def _explode(*a, **kw):
        raise smtplib.SMTPAuthenticationError(535, b"nope")

    monkeypatch.setattr(mail, "_connect", _explode)
    ok, message = mail.send_test_email("me@example.com")
    assert ok is False
    assert "username or password" in message.lower()


def test_test_email_succeeds_when_the_server_accepts(smtp_configured, fake_smtp):
    ok, message = mail.send_test_email("me@example.com")
    assert ok is True
    assert "me@example.com" in message


def test_the_endpoint_is_owner_only(client):
    from app.core.permissions import Role, user_can_access
    from app.core.auth import SessionUser
    import uuid as _uuid

    for role in (Role.CFO, Role.ACCOUNTANT, Role.PERSONAL, Role.VIEWER):
        u = SessionUser(user_id=str(_uuid.uuid4()), username=role, is_admin=False,
                        company_id="c1", is_superadmin=False, role=role)
        assert user_can_access(u, "POST", "/admin/test-email") is False

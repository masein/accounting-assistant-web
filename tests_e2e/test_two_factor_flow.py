"""Two-factor sign-in end to end in a real browser (roadmap 2026-09 §1.13):
set it up from the account menu, sign out, sign in with password + code,
then turn it off with a recovery code. Runs as a throwaway accountant the
owner creates, so the shared e2e owner is never left with 2FA on."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time

import pytest

from tests_e2e.conftest import ARTIFACTS, BASE_URL, PageWatch


def _totp(secret: str, step: int) -> str:
    """Independent RFC 6238 implementation — the browser test must not trust
    the server's own maths."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return str((struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


def _step() -> int:
    return int(time.time() // 30)


def _csrf_headers(ctx) -> dict:
    # the app shell sets the double-submit cookie on load (app/main.py)
    token = next((c["value"] for c in ctx.cookies() if c["name"] == "aa_csrf"), "")
    return {"X-CSRF-Token": token}


@pytest.fixture()
def staff_user(browser, logged_in_state):
    """An accountant the owner creates for this test and deletes after it."""
    ctx = browser.new_context(storage_state=logged_in_state)
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    username = f"e2e_tfa_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(18) + "#9a"
    r = page.request.post(f"{BASE_URL}/admin/users", headers=_csrf_headers(ctx),
                          data={"username": username, "password": password, "role": "accountant"})
    assert r.status == 201, r.text()
    uid = r.json()["id"]
    yield username, password
    page.request.delete(f"{BASE_URL}/admin/users/{uid}", headers=_csrf_headers(ctx))
    ctx.close()


def _shot(page, name):
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, f"{name}.png"), full_page=True)


def _password_step(page, username, password):
    page.goto(f"{BASE_URL}/login")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#submit-btn")


def test_two_factor_setup_sign_in_and_turn_off(browser, staff_user):
    username, password = staff_user
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    watch = PageWatch(page)
    try:
        # 1. Password-only sign-in, then set up from the account menu.
        _password_step(page, username, password)
        page.wait_for_url(lambda url: "/login" not in url, timeout=15_000)
        page.wait_for_load_state("networkidle")
        page.click("#user-menu-btn")
        page.click("#topbar-security")
        page.wait_for_selector("#tfa-off", state="visible")
        page.fill("#tfa-setup-password", password)
        page.click("#tfa-start")
        page.wait_for_selector("#tfa-scan", state="visible")
        assert page.get_attribute("#tfa-qr", "src").startswith("data:image/svg+xml")
        secret = page.inner_text("#tfa-secret").replace(" ", "").strip()
        assert len(secret) == 32
        page.fill("#tfa-enable-code", _totp(secret, _step()))
        page.click("#tfa-confirm")
        page.wait_for_selector("#tfa-codes", state="visible")
        codes = [c.strip() for c in page.inner_text("#tfa-codes-list").split("\n") if c.strip()]
        assert len(codes) == 10
        page.click("#tfa-codes-done")
        page.wait_for_selector("#tfa-on", state="visible")

        # 2. Sign out; the password alone now leads to the code step.
        page.click("#security-modal-close")
        page.click("#user-menu-btn")
        page.click("#topbar-logout")
        page.wait_for_url(lambda url: "/login" in url, timeout=15_000)
        _password_step(page, username, password)
        page.wait_for_selector("#tfa-form", state="visible")
        assert "/login" in page.url
        # The set-up used this step's code; the next step's is inside the drift window.
        page.fill("#tfa-code", _totp(secret, _step() + 1))
        page.click("#tfa-btn")
        page.wait_for_url(lambda url: "/login" not in url, timeout=15_000)
        page.wait_for_load_state("networkidle")

        # 3. Turn it off with the password and a recovery code.
        page.click("#user-menu-btn")
        page.click("#topbar-security")
        page.wait_for_selector("#tfa-on", state="visible")
        assert "10" in page.inner_text("#tfa-on-left")
        page.fill("#tfa-on-code", codes[0])
        page.fill("#tfa-off-password", password)
        page.click("#tfa-disable")
        page.wait_for_selector("#ui-confirm-modal", state="visible")
        page.click("#ui-confirm-ok")
        page.wait_for_selector("#tfa-off", state="visible")
        assert watch.problems() == [], watch.problems()
    except Exception:
        _shot(page, "two-factor-flow")
        raise
    finally:
        ctx.close()

    # 4. And the password alone signs in again.
    ctx = browser.new_context()
    page = ctx.new_page()
    _password_step(page, username, password)
    page.wait_for_url(lambda url: "/login" not in url, timeout=15_000)
    ctx.close()

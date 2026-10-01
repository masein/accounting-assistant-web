"""An error from the server reads in the page's language: on the sign-in page
in Persian, a wrong password said "Invalid username or password"."""
from __future__ import annotations

import os
import uuid

from tests_e2e.conftest import ARTIFACTS, BASE_URL


def test_a_sign_in_error_reads_in_persian(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(f"{BASE_URL}/login")
        page.click('.lang-pill[data-lang="fa"]')
        page.fill("#username", f"nobody-{uuid.uuid4().hex[:6]}")      # no such user: no lockout for anyone real
        page.fill("#password", "not-the-password")
        with page.expect_response(lambda r: r.url.endswith("/auth/login")) as res:
            page.click("#submit-btn")
        assert res.value.status == 401
        assert res.value.request.headers.get("x-ui-language") == "fa"
        page.wait_for_selector("#error-box", state="visible")
        assert page.inner_text("#error-box").strip() == "نام کاربری یا رمز عبور نادرست است"
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "error-language.png"), full_page=True)
        raise
    finally:
        ctx.close()

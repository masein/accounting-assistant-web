"""The app asks in its own dialogs, in the user's language: petty cash's
deposit asked through the browser's prompt() (its OK / Cancel in the
browser's language, the page frozen behind it), a deposit typed in Persian
digits — "۵۰۰٬۰۰۰" — was read as nothing and silently dropped, and petty
cash wrote every amount in rials whatever the company's currency."""
from __future__ import annotations

from tests_e2e.conftest import switch_language

API = r"""async ([method, path, body]) => { const r = await fetch(path, { method,
  headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return [r.status, await r.json().catch(() => null)]; }"""


def test_a_petty_cash_deposit_asks_in_the_app_and_takes_persian_digits(flow_page):
    page, watch = flow_page("e2e_petty")
    native = []
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    try:
        switch_language(page, "fa")
        status, body = page.evaluate(API, ["POST", "/petty-cash/accounts", {"username": "e2e_employee", "holder_name": "تنخواه آزمون"}])
        if status != 201:     # one float per holder: a rerun finds it
            accounts = page.evaluate(API, ["GET", "/petty-cash/accounts", None])[1]
            body = next(a for a in accounts if a["holder_name"] == "تنخواه آزمون")
        before = page.evaluate(API, ["GET", f"/petty-cash/accounts/{body['id']}", None])[1]["balance"]

        page.click('.nav-btn[data-page="petty-cash"]')
        page.wait_for_selector(f'.petty-deposit[data-id="{body["id"]}"]')
        page.click(f'.petty-deposit[data-id="{body["id"]}"]')
        modal = page.locator("#ui-prompt-modal")
        assert modal.is_visible()
        assert page.inner_text("#ui-prompt-title") == page.evaluate("() => t('pettyDepositBtn')")
        # in the company's own currency, not a rial written into the words
        ccy = page.evaluate("() => currencySymbol(baseCurrencyCode())")
        assert ccy in page.inner_text("#ui-prompt-label")
        page.fill("#ui-prompt-input", "۵۰۰٬۰۰۰")
        page.click("#ui-prompt-ok")
        assert page.input_value("#ui-prompt-input") == "1110"      # then the bank, prefilled
        with page.expect_response(lambda r: r.url.endswith(f"/petty-cash/accounts/{body['id']}/deposit")) as res:
            page.click("#ui-prompt-ok")
        assert res.value.status in (200, 201), res.value.text()
        after = page.evaluate(API, ["GET", f"/petty-cash/accounts/{body['id']}", None])[1]["balance"]
        assert after - before == 500_000
        page.wait_for_selector(f'.petty-deposit[data-id="{body["id"]}"]')
        row = page.locator(f'#petty-admin-tbody tr:has(.petty-deposit[data-id="{body["id"]}"])')
        assert page.evaluate("(n) => formatMoney(n, baseCurrencyCode())", after) in row.inner_text()
        assert native == []
        assert watch.problems() == [], watch.problems()
    finally:
        try:
            switch_language(page, "en")
        except Exception:
            pass

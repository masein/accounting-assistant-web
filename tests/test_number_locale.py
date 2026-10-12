"""Numbers read the same everywhere (scenarios I12, I13).

47 places formatted amounts with a bare ``toLocaleString()``, which follows the
browser's locale, not the app's language: Persian digits beside Latin ones on
a Persian browser, «74.250.000» with dots on a Spanish one (#298). Since the
owner's call on 2026-10-12 a figure follows the app's language instead: Persian
digits and «٬» groups on a Persian page, Latin digits and comma groups on every
other (#338). Display code goes through ``formatNum``/``localDigits``, or gives
``toLocaleString`` the app's own ``numLocale()``. A date may still follow the
reader's own locale."""
from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _lines():
    for p in sorted(JS.glob("*.js")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            yield p.name, i, line


def test_no_number_follows_the_browser_locale():
    hits = [f"{name}:{i}" for name, i, line in _lines()
            if ".toLocaleString()" in line and "when.toLocaleString()" not in line]
    assert hits == [], f"use formatNum, or toLocaleString(numLocale()): {hits}"


def test_no_figure_is_pinned_to_latin_digits_on_a_persian_page():
    # formatRate's two calls are wrapped in localDigits on the line before
    hits = [f"{name}:{i}" for name, i, line in _lines()
            if "toLocaleString('en-US'" in line and not (name == "03-ui.js" and "maximum" in line)]
    assert hits == [], f"use formatNum, or toLocaleString(numLocale()): {hits}"


def test_the_helpers_are_there_and_exports_turn_digits_back():
    core = (JS / "01-core.js").read_text(encoding="utf-8")
    assert "function localDigits(" in core and "function latinDigits(" in core and "function numLocale(" in core
    assert "function localCode(" in core
    assert "?<=" not in core                       # no lookbehind: older Safari can't parse the script
    products = (JS / "13-companies-products.js").read_text(encoding="utf-8")
    assert "latinDigits(c)" in products            # a CSV is read by other programs: Latin digits


def test_account_codes_are_shown_through_local_code():
    # a code is shown in the page's digits; the value a form or option holds stays Latin
    shown = re.compile(r">\$\{escapeHtml\((a\.code|r\.account_code|n\.code)\)\}|escapeHtml\(n\.code\) \+ '</td>|account_code \+ ' ' \+")
    hits = [f"{name}:{i}" for name, i, line in _lines() if shown.search(line)]
    assert hits == [], f"wrap the shown code in localCode(): {hits}"

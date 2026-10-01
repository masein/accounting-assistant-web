"""Habits the browser suite keeps, learnt the hard way (#252, #254):

* page.wait_for_function polls by evaluating its string inside the page, and
  the page's CSP (script-src 'self', no 'unsafe-eval') refuses that as soon as
  the first check is false — use conftest.wait_until.
* a language switch is waited for with conftest.switch_language: the
  "networkidle" load state is already reached, so waiting on it returns at once."""
from __future__ import annotations

import re
from pathlib import Path

E2E = Path(__file__).resolve().parents[1] / "tests_e2e"


def test_no_test_polls_by_eval():
    offenders = [p.name for p in E2E.glob("*.py") if "wait_for_function(" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_a_language_switch_is_waited_for():
    # the raw switch followed by a networkidle wait is the race switch_language replaced
    raw = re.compile(r"getElementById\('topbar-language'\)[^\n]*\n[^\n]*dispatchEvent[^\n]*\n\s*\w+\.wait_for_load_state\(\"networkidle\"\)")
    offenders = [p.name for p in E2E.glob("test_*.py") if raw.search(p.read_text(encoding="utf-8"))]
    assert offenders == []

"""Numbers read the same on every browser (scenario I12).

47 places formatted amounts with a bare ``toLocaleString()``, which follows the
browser's locale, not the app's language: Persian digits beside Latin ones on
a Persian browser, «74.250.000» with dots on a Spanish one. They are pinned to
``en-US`` — Latin digits and comma groups, as ``formatNum`` writes everywhere
else. A date may still follow the reader's own locale."""
from __future__ import annotations

from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def test_no_number_follows_the_browser_locale():
    hits = [f"{p.name}:{i}" for p in sorted(JS.glob("*.js")) for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if ".toLocaleString()" in line and "when.toLocaleString()" not in line]
    assert hits == [], f"pin these to 'en-US' (or use formatNum): {hits}"

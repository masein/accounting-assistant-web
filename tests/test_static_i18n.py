"""No English UI text is written into index.html outside the translation
system — including the parts no browser test opens: the Excel journal import,
a statement's row table, the product chart and detail panels, the companies
form, the confirm dialogs.

Every text node with Latin words must sit in (or under) an element carrying
data-i18n, unless a script fills that element before anyone reads it (listed
below, by id, with the reason) or the text is a name that stays as written:
currency codes, the languages' own names, other products' brand names."""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"

# ids whose text a script replaces with t()/tf() before it is shown
SCRIPT_FILLED = {
    "page-title",          # updatePageTitle() names the open page
    "bal-debit", "bal-credit", "bal-diff",   # updateVoucherBalanceBar() at load and on every edit
    "ui-language-label",   # applyLanguage(): t('languageLabel')
    "equity-amount-hint",  # loadEquity(): tf('equityAmountHint', {currency}) — the company's own
}
# the languages in their own names; currency codes (with a symbol); other
# products' names, written in both scripts
KEEP = re.compile(r"^(English|Español|[A-Z]{3}( \([^A-Za-z]{1,3}\))?|Xero|QuickBooks|MetisAI|[A-Z][a-z]+ · [؀-ۿ ]+)$")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP = {"script", "style", "code", "pre", "textarea", "title", "template", "svg"}


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or not self.stack or not re.search(r"[A-Za-z]{3,}", text) or KEEP.match(text):
            return
        if any(tag in SKIP for tag, _ in self.stack):
            return
        if any("data-i18n" in attrs for _, attrs in self.stack):     # translated here or by an ancestor
            return
        if self.stack[-1][1].get("id") in SCRIPT_FILLED:
            return
        self.found.append(f"line {self.getpos()[0]} <{self.stack[-1][0]}>: {text[:60]}")


def _english_text() -> list[str]:
    scan = _Scan()
    scan.feed(INDEX.read_text(encoding="utf-8"))
    return scan.found


def test_index_html_has_no_untranslated_english():
    assert _english_text() == []


def test_the_scan_sees_untranslated_text(monkeypatch, tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<div><h3 data-i18n="x">Title</h3><label>Excel File</label><span id="page-title">Dashboard</span>'
                    '<option>IRR (Rial)</option><option>GBP (£)</option><option>English</option></div>', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "INDEX", page)
    assert [f.split(": ", 1)[1] for f in _english_text()] == ["Excel File", "IRR (Rial)"]


def test_every_key_the_code_asks_for_exists():
    """t('key') returns the key itself when it is missing, so a typo or a key
    never added shows up as "aiThinking" on screen (the bank-statement upload
    said exactly that) — and `t('key') || 'fallback'` never falls back."""
    from tests.i18n_source import JS, pack_keys

    used: set[str] = set()
    for path in JS.glob("*.js"):
        # a whole key: the call closes (or takes params) right after it; t('adj_' + kind) is a prefix
        used |= set(re.findall(r"\bt[fr]?\('([A-Za-z_][A-Za-z0-9_]*)'\s*[,)]", path.read_text(encoding="utf-8")))
    # data-i18n names a key too — in index.html and in the HTML the scripts build
    # (the shareholder ledger asked for colDate/colDescription, which never existed)
    attr = re.compile(r'data-i18n(?:-placeholder|-title|-aria-label)?=\\?"([A-Za-z_][A-Za-z0-9_]*)\\?"')
    for text in [INDEX.read_text(encoding="utf-8")] + [p.read_text(encoding="utf-8") for p in JS.glob("*.js")]:
        used |= set(attr.findall(text))
    assert used, "the scan found no keys"
    missing = {lang: sorted(used - keys) for lang, keys in pack_keys().items() if used - keys}
    assert missing == {}, missing

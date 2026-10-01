"""File fields speak the user's language (dressFileInputs, js/03-ui.js): Chrome
writes "Choose Files" / "No file chosen" in the browser's language on a bare
<input type=file>. A visible one is dressed by the observer 03-ui.js keeps; a
hidden one (display:none) must be opened by a button of the app's own, whose
label is translated. tests_e2e/test_file_picker_language.py runs it."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPTS = "\n".join(p.read_text(encoding="utf-8") for p in sorted((STATIC / "js").glob("*.js")))


def _file_inputs(html: str) -> list[tuple[str, bool]]:
    out = []
    for tag in re.findall(r"<input\b[^>]*\btype=\"file\"[^>]*>", html):
        ident = re.search(r'\bid="([^"]+)"', tag)
        hidden = bool(re.search(r'style="[^"]*display:\s*none', tag))
        out.append((ident.group(1) if ident else "", hidden))
    return out


def test_the_observer_dresses_file_fields():
    ui = (STATIC / "js" / "03-ui.js").read_text(encoding="utf-8")
    run = re.search(r"const run = \(\) => \{(.*?)\};", ui)
    assert run and "dressFileInputs()" in run.group(1)
    # a script clearing the field repaints it, and a language switch repaints all
    assert "set(v) { _fileInputValue.set.call(this, v); paintFilePick(this); }" in ui
    assert "if (input.dataset.filePick) { paintFilePick(input); return; }" in ui


def test_a_hidden_file_field_has_a_button_of_its_own():
    hidden = [ident for ident, is_hidden in _file_inputs(INDEX) if is_hidden]
    assert hidden, "the scan found no hidden file inputs"
    orphans = []
    for ident in hidden:
        names = re.findall(r"const (\w+) = document\.getElementById\('" + re.escape(ident) + r"'\)", SCRIPTS)
        if not any(re.search(r"addEventListener\('click', \(\) => " + n + r"\.click\(\)\)", SCRIPTS) for n in names):
            orphans.append(ident)
    assert orphans == []


def test_the_scan_reads_file_inputs():
    html = '<input type="file" id="a"><input id="b" type="file" style="display:none;"><input type="text" id="c">'
    assert _file_inputs(html) == [("a", False), ("b", True)]

"""The scripts say nothing to the user in hard-coded English.

A Persian user used to meet English after nearly every save — "Invoice
created.", "Entity added.", "Voucher saved. Ledger updated.", "Connection
error: …" — because ~100 messages were written straight into showAlert(),
textContent and `data.detail || '…'` fallbacks. They now go through t()/tf(),
and this scan keeps new ones from creeping back.

Also: a translation must use the same {placeholders} as the English, or the
user sees a raw "{count}" (the Persian data-quality checklist did)."""
from __future__ import annotations

import re
import sys

from tests.i18n_source import JS, pack_values

# Where English reaches the screen: a message call, a fallback, an element's
# text, a thrown error's message, or the start of a concatenated sentence.
SHAPES = {
    "message": re.compile(r"\b(?:showAlert|alert|confirm|prompt)\(\s*(['\"`])([A-Z][a-z][^'\"`]*?)\1"),
    "fallback": re.compile(r"\|\|\s*\(?\s*(['\"`])([A-Z][a-z][^'\"`]{2,}?)\1"),
    "text": re.compile(r"\.(?:textContent|innerText|placeholder|title)\s*=\s*(['\"`])([A-Z][a-z][^'\"`]{3,}?)\1"),
    "error": re.compile(r"new Error\(\s*(['\"`])([A-Z][a-z][^'\"`]{3,}?)\1"),
    "sentence": re.compile(r"(['\"])([A-Z][a-z]+(?: [a-z]+)+[:.]? ?)\1\s*\+"),
    # a labelled value in a template literal: `Date: ${date}\n` (the voucher
    # confirmation was built this way, all of it English)
    "label": re.compile(r"(`)[^`]*?(?:(?<![\w$-])|(?<=\\n))([A-Z][a-z]+(?: [a-z]+)*:) \$\{"),
}
# `t('key') || 'English'` never falls back (t returns the key), so it isn't shown.
_AFTER_T = re.compile(r"\bt[fr]?\([^()]*\)\s*\|\|\s*\(?\s*$")

# Values that are data, not wording.
ALLOWED = {
    # the bank name the server stores when the user leaves it blank (its own default too)
    ("10-forms-fx-bank.js", "Unknown"),
}


def _english_messages(files=None) -> list[str]:
    found = []
    for path in files or sorted(JS.glob("*.js")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for shape, pattern in SHAPES.items():
                for m in pattern.finditer(line):
                    if shape == "fallback" and _AFTER_T.search(line[: m.end(1) - 1]):
                        continue
                    if (path.name, m.group(2)) in ALLOWED:
                        continue
                    found.append(f"{path.name}:{n} [{shape}] {m.group(2)!r}")
    return found


def test_the_scripts_write_no_english_messages():
    assert _english_messages() == []


# a lowercase word as the fallback of a message or an element's text ("failed",
# "error", "none" — the petty-cash and stock pages said "failed" in English)
_LOWER_FALLBACK = re.compile(r"""(?:\|\||\?[^:;]*:)\s*(['"])([a-z][a-z]+(?: [a-z]+)*)\1\s*[,)]""")
_SHOWN = re.compile(r"showAlert\(|\.textContent\s*=|new Error\(")


def _lower_fallbacks(files=None) -> list[str]:
    found = []
    for path in files or sorted(JS.glob("*.js")):
        if path.name == "02-i18n.js":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SHOWN.search(line):
                found += [f"{path.name}:{n} {m.group(2)!r}" for m in _LOWER_FALLBACK.finditer(line) if len(m.group(2)) > 3]
    return found


def test_no_lowercase_english_fallback():
    assert _lower_fallbacks() == []


def test_the_lowercase_scan_sees_them(tmp_path):
    js = tmp_path / "99-sample.js"
    js.write_text("showAlert(d.detail || 'failed', true);\n"
                  "el.textContent = ok ? t('done') : (typeof d.detail === 'string' ? d.detail : 'error');\n"
                  "showAlert(d.detail || t('msgFailed'), true);\n"
                  "el.style.display = ok ? 'none' : 'block';\n", encoding="utf-8")
    assert [f.split(" ", 1)[1] for f in _lower_fallbacks([js])] == ["'failed'", "'error'"]


# The browser's own dialogs label their buttons in the browser's language and
# stop the page; the app has uiConfirm / uiPrompt / showAlert (js/03-ui.js).
_NATIVE_DIALOG = re.compile(r"(?<![\w.$])(?:window\.)?(alert|confirm|prompt)\(")


def test_no_native_dialog():
    found = [f"{p.name}:{n} {m.group(1)}()" for p in sorted(JS.glob("*.js"))
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
             if not line.lstrip().startswith("//")
             for m in _NATIVE_DIALOG.finditer(line)]
    assert found == []


def test_the_scan_sees_each_shape(tmp_path):
    js = tmp_path / "99-sample.js"
    js.write_text(
        "showAlert('Invoice created.');\n"
        "showAlert(data.detail || 'Error creating invoice.', true);\n"
        "el.textContent = 'Scanning invoice...';\n"
        "throw new Error('Authentication required');\n"
        "showAlert('Connection error: ' + err.message, true);\n"
        "msg.textContent = `Total: ${n}`;\n"
        "let summary = `Date: ${date}\\n`; summary += `\\nDebit entries: ${n}`;\n"
        "if (!r.ok) throw new Error(data.detail || ('Failed upload: ' + f.name));\n"
        # not shown, or not English: none of these may be reported
        "showAlert(t('msgInvoiceCreated'));\n"
        "showAlert(data.detail || t('msgInvoiceCreateError'), true);\n"
        "const label = t('reportWord') || 'Report';\n"
        "el.textContent = 'IRR';\n"
        "showAlert(tf('msgVoucherUnbalanced', { debit: a, credit: b }), true);\n",
        encoding="utf-8",
    )
    got = [f.split(" ", 1)[1] for f in _english_messages([js])]
    assert got == [
        "[message] 'Invoice created.'",
        "[fallback] 'Error creating invoice.'",
        "[text] 'Scanning invoice...'",
        "[error] 'Authentication required'",
        "[message] 'Connection error: '",
        "[sentence] 'Connection error: '",
        "[text] 'Total: ${n}'",
        "[label] 'Total:'",
        "[label] 'Date:'",
        "[label] 'Debit entries:'",
        "[fallback] 'Failed upload: '",
        "[sentence] 'Failed upload: '",
    ]


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text))


def test_every_translation_fills_the_same_placeholders():
    values = pack_values()
    assert len(values["en"]) > 2000, "the pack reader found too few keys"
    wrong = [
        f"{lang}.{key}: {sorted(_placeholders(text))} vs en {sorted(_placeholders(values['en'][key]))}"
        for lang in ("fa", "es", "ar")
        for key, text in values[lang].items()
        if key in values["en"] and _placeholders(text) != _placeholders(values["en"][key])
    ]
    assert wrong == []


def _message_keys() -> set[str]:
    """Keys whose text is a message: passed to showAlert()/new Error(), put in
    an element's text, or offered as a `|| …` fallback."""
    keys: set[str] = set()
    key = r"\bt[fr]?\('([A-Za-z_][A-Za-z0-9_]*)'"
    for path in JS.glob("*.js"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"showAlert\(|new Error\(|\.textContent\s*=|\|\|\s*t[fr]?\(", line):
                keys |= set(re.findall(key, line))
    return keys


def test_the_messages_are_translated():
    """Each message reads as Persian in fa and Arabic in ar, and not as the
    English in es (bar the few that Spanish spells the same)."""
    values = pack_values()
    keys = sorted(_message_keys() & set(values["en"]))
    assert len(keys) > 300, "the scan found too few message keys"
    assert {"msgInvoiceCreated", "msgConnectionError", "msgVoucherSaved", "msgUploadFileFailed"} <= set(keys)
    assert [k for k in keys if not re.search(r"[\u0600-\u06FF]", values["fa"][k])] == []
    assert [k for k in keys if not re.search(r"[\u0600-\u06FF]", values["ar"][k])] == []
    # Persian uses its own letters, not the Arabic ي/ك
    assert [k for k in keys if re.search(r"[يك]", values["fa"][k])] == []
    same_in_spanish = {"errorWithMessage", "msgTotalAmount", "timeNoProject"}
    assert [k for k in keys if values["es"][k] == values["en"][k] and k not in same_in_spanish] == []


def test_a_persian_check_reads_its_numbers():
    """The data-quality checklist passes done/total; the Persian used to ask for {count}."""
    fa = pack_values()["fa"]
    for key in ("checkRefsDetail", "checkEntitiesDetail", "checkAttachmentsDetail", "checkLineDescriptionsDetail"):
        assert {"done", "total"} <= _placeholders(fa[key]), key
    for key in ("alertOverdueArMessage", "alertOverdueApMessage"):
        assert _placeholders(fa[key]) == {"amount"}, key


# ---------------------------------------------------------------------------
# HTML the scripts build: table headers, buttons, labels, empty states
# ---------------------------------------------------------------------------

# Words that stay as written: file formats, protocols, currency codes, and the
# sample values shown in the rate-feed form (a URL, a JSON path, a unit code).
AS_WRITTEN = re.compile(r"^(CSV|PDF|XLSX|Excel|JSON|HTTPS?|IBAN|API|SMS|IMAP|GBP|IRR|USD|EUR|Navasan|GOLDG"
                        r"|123456:ABC…|https://…\?api_key=…|data\.gold18\.price)$")
_HTML_TEXT = re.compile(r">([^<>]*?[A-Za-z]{2,}[^<>]*?)<")
_HTML_ATTR = re.compile(r"\b(placeholder|title|aria-label|alt)=\\?\"([^\"\x00]*[A-Za-z]{2,}[^\"\x00]*)\\?\"")
_SET_ATTR = re.compile(r"setAttribute\(\s*'(aria-label|title|placeholder|alt)'\s*,\s*(['\"])([^'\"]*[A-Za-z]{2,}[^'\"]*)\2")
_CODE = re.compile(r"\w\(|=>|&&|\|\||[{}=]|;\s*\w|\w\.\w+_")


def _literal_parts(line: str, nested: list[str]) -> str:
    """The line with every ${ … } replaced by a marker; a template literal
    written inside one of those expressions is collected into `nested`."""
    out, i, depth = [], 0, 0
    while i < len(line):
        if line.startswith("${", i):
            if not depth:
                out.append("\x00")
            depth, i = depth + 1, i + 2
            continue
        if depth:
            if line[i] == "`":
                j = line.find("`", i + 1)
                while j != -1 and line.count("${", i, j) > line.count("}", i, j):
                    j = line.find("`", j + 1)
                j = len(line) if j == -1 else j
                nested.append(_literal_parts(line[i + 1:j], nested))
                i = j + 1
                continue
            depth += {"{": 1, "}": -1}.get(line[i], 0)
            i += 1
            continue
        out.append(line[i])
        i += 1
    return "".join(out)


def _english_in_html(files=None) -> list[str]:
    found = []
    for path in files or sorted(JS.glob("*.js")):
        if path.name == "02-i18n.js":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("//"):
                continue
            nested: list[str] = []
            parts = [_literal_parts(line, nested)] + nested
            for part in parts:
                for m in _HTML_TEXT.finditer(part):
                    text = " ".join(m.group(1).replace("\x00", " ").split())
                    if text.startswith(("'", '"', "+", ")")) or AS_WRITTEN.match(text) or _CODE.search(re.sub(r"&#?\w+;", "", text)):
                        continue
                    if re.search(r"[A-Za-z]{2,}", text):
                        found.append(f"{path.name}:{n} {text!r}")
                for m in _HTML_ATTR.finditer(part):
                    value = m.group(2).strip()
                    if not (AS_WRITTEN.match(value) or "' +" in value or "+ '" in value):
                        found.append(f"{path.name}:{n} {m.group(1)}={value!r}")
            for m in _SET_ATTR.finditer(line):
                found.append(f"{path.name}:{n} {m.group(1)}={m.group(3)!r}")
    return found


def test_the_html_the_scripts_build_has_no_english():
    assert _english_in_html() == []


def test_the_html_scan_sees_each_shape(tmp_path):
    js = tmp_path / "99-sample.js"
    js.write_text(
        "tr.innerHTML = `<td>${escapeHtml(e.name)}</td><td><button>Edit</button></td>`;\n"
        "tbody.innerHTML = '<tr><td colspan=\"5\" class=\"empty-state\">No entities yet.</td></tr>';\n"
        "          <th>Item</th><th class=\"num\">On Hand</th>\n"
        "  <input type=\"text\" placeholder=\"Search movements...\">\n"
        "  <td>${ok ? `<button class=\"x\">Approve</button>` : '✓'}</td>\n"
        "close.setAttribute('aria-label', 'Close');\n"
        "<a href=\"#\">Next &#8594;</a>\n"
        "<button data-delta=\"-1\">&#8592; Prev</button>\n"
        # translated, or written as is on purpose: none of these may be reported
        "tr.innerHTML = `<td>${escapeHtml(t('btnEdit'))}</td><th>${t('labelDate')}</th>`;\n"
        "body.innerHTML = '<p>' + escapeHtml(t('noDataYet')) + '</p>';\n"
        "<button data-format=\"csv\">CSV</button><button>PDF</button>\n"
        "<span title=\"' + escapeHtml(t('aiRevToolFailed')) + '\">!</span>\n"
        "// <b>Old comment</b>\n",
        encoding="utf-8",
    )
    got = [f.split(" ", 1)[1] for f in _english_in_html([js])]
    assert got == ["'Edit'", "'No entities yet.'", "'Item'", "'On Hand'", "placeholder='Search movements...'",
                   "'Approve'", "aria-label='Close'", "'Next &#8594;'", "'&#8592; Prev'"]

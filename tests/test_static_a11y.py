"""Keyboard and screen-reader basics of the single page (review 2026-11-28,
scenarios CZ1–CZ5 in docs/qa/SCENARIOS.md): a skip link, one Escape/Tab
handler for every modal, column headers that say they are, and a chat that
announces a reply once instead of reading the whole list and a "thinking"
caption every 2.2 seconds."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = STATIC / "js"


def _js(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_skip_link_is_first_and_focuses_main_without_touching_the_hash():
    body = HTML.split("<body>", 1)[1].lstrip()
    assert body.startswith('<a class="skip-link" href="#main-content" data-action="skip-to-content" data-i18n="skipToContent">')
    assert '<main id="main-content" role="main" tabindex="-1">' in HTML
    core = _js("01-core.js")
    action = core[core.index("registerAction('skip-to-content'"):]
    action = action[:action.index("});")]
    assert "e.preventDefault();" in action and "main.focus()" in action   # #main-content is no page name
    from tests.i18n_source import pack_values
    values = pack_values()
    assert {lang: bool(values[lang].get("skipToContent")) for lang in values} == {"en": True, "fa": True, "es": True, "ar": True}
    assert ".skip-link:focus" in (STATIC / "css" / "app.css").read_text(encoding="utf-8")


def test_every_modal_gets_escape_tab_and_focus_return_from_one_handler():
    dialogs = re.findall(r'<div id="([a-z-]+)"[^>]*role="dialog" aria-modal="true"', HTML)
    assert len(dialogs) >= 12 and "chart-drilldown-modal" in dialogs and "ui-confirm-modal" in dialogs
    ui = _js("03-ui.js")
    handler = ui[ui.index("(function dialogKeyboard() {"):]
    handler = handler[:handler.index("    })();")]
    assert "document.querySelectorAll('[role=\"dialog\"][aria-modal=\"true\"]')" in handler
    assert "dlg.__dialogCancel()" in handler            # a waiting uiConfirm/uiPrompt answers "no"
    assert "e.defaultPrevented" in handler               # uiPrompt's box and the date picker handle their own Escape
    assert "(e.shiftKey ? last : first).focus()" in handler
    assert "back.focus()" in handler                     # focus returns to the opener
    # no script sets the modal focus up a second time
    assert sum(src.count("dialogKeyboard") for src in (_js(p.name) for p in JS.glob("*.js"))) == 1


def test_header_cells_in_the_page_are_column_headers():
    heads = re.findall(r"<thead\b.*?</thead>", HTML, re.S)
    assert heads
    cells = [c for h in heads for c in re.findall(r"<th\b[^>]*>", h)]
    assert cells and all('scope="col"' in c for c in cells), [c for c in cells if 'scope="col"' not in c][:5]
    # the tables the scripts build the same way
    assert "<th scope=\"col\">${escapeHtml(localizeReportFieldName(h))}</th>" in _js("05-reports-manager.js")
    assert "tag === 'th' ? ' scope=\"col\"' : ''" in _js("15-ai-chat.js")       # the chat's Markdown tables


def test_chat_announces_a_new_reply_once_not_the_whole_list():
    assert '<div id="ai-acct-messages" class="chat-messages ai-messages"></div>' in HTML
    assert '<div id="ai-acct-announce" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>' in HTML
    chat = _js("15-ai-chat.js")
    typing = chat[chat.index("function showTypingIndicator()"):chat.index("function hideTypingIndicator(")]
    assert "wrap.setAttribute('aria-hidden', 'true')" in typing
    assert "setAttribute('aria-label'" not in typing and "'role', 'status'" not in typing
    assert typing.count("announce(phrases[0]);") == 1                         # said once, not every 2.2 s
    bubble = chat[chat.index("function appendBubble("):chat.index("function appendReadingCard(")]
    assert "if (kind !== 'user' && opts.animate !== false) announce(" in bubble   # history replays stay quiet


def test_shared_file_names_use_the_languages_list_separator():
    chat = _js("15-ai-chat.js")
    assert "const names = joinList(files.map(f => f.name));" in chat
    assert "join('، ')" not in chat

"""AI chat page redesign (2026-09-24): direction-neutral bubbles (a Persian
answer inside an English UI laid out LTR — colon and bullets on the wrong
side), structured markup instead of inline styles, localized chips, empty
state, textarea composer."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "js" / "15-ai-chat.js").read_text(encoding="utf-8")
CSS = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
I18N = (STATIC / "js" / "02-i18n.js").read_text(encoding="utf-8")


def _chat_block() -> str:
    return HTML.split('class="card ai-page" data-page="ai-accountant"', 1)[1].split("VOUCHER PAGE", 1)[0]


def test_bubbles_are_direction_neutral():
    # every bubble decides its own direction from its text, and the CSS keeps
    # each markdown block (list, paragraph, table) neutral too
    assert "bubble.setAttribute('dir', 'auto')" in JS
    assert ".msg {" in CSS and "unicode-bidi: plaintext" in CSS.split(".msg {", 1)[1].split("}", 1)[0]
    assert ".md > * { unicode-bidi: plaintext; text-align: start; }" in CSS
    # own vs assistant side follows the document direction via flex alignment,
    # never a hard-coded left/right
    assert ".msg-row.user { align-items: flex-end; }" in CSS
    assert "text-align:right" not in JS and "text-align:left" not in JS


def test_markup_uses_classes_and_keeps_the_ids_the_scripts_need():
    block = _chat_block()
    for needle in ('id="ai-acct-messages"', 'id="ai-acct-input"', 'id="ai-acct-send"', 'id="ai-acct-session-list"',
                   'id="ai-acct-quick-actions"', 'id="ai-acct-attachments"', 'id="ai-acct-title"',
                   'id="ai-acct-scroll-bottom"', 'class="ai-acct-layout"', '<textarea id="ai-acct-input"'):
        assert needle in block, needle
    # no inline style soup left on the structural elements
    assert 'id="ai-acct-messages" class="chat-messages ai-messages"' in block
    assert 'style="display:none; position:absolute' not in block


def test_chips_send_their_localized_label_in_every_mode():
    block = HTML.split('id="ai-acct-quick-actions"', 1)[1].split("</div>", 1)[0]
    tags = re.findall(r'<button[^>]*class="chip (?:chip-business|chip-personal)"[^>]*>', block, re.S)
    assert len(tags) >= 12
    for tag in tags:
        assert "data-msg=" not in tag, tag  # English pinned messages made the assistant answer in English
        assert "data-i18n=" in tag


def test_markdown_renderer_handles_tables_code_and_paragraphs():
    assert "flushTable" in JS and "<table><thead><tr>" in JS
    assert "<pre><code>" in JS
    assert "out.push('<p>' + para.join('<br>') + '</p>')" in JS
    assert "class=\"num\"" in JS  # numeric cells right-aligned


def test_empty_state_composer_and_strings():
    assert "function renderEmptyState()" in JS and "aiChatExample1" in JS
    assert "function autosizeInput()" in JS
    assert "[data-i18n-aria-label]" in I18N
    for key in ("aiChatHint:", "aiChatYou:", "aiChatAssistant:", "aiChatEmptyTitle:", "aiChatEmptyBody:",
                "aiChatExample1:", "aiChatExample2:", "aiChatExample3:", "aiChatScrollBottom:"):
        assert I18N.count(key) == 4, key


def test_mobile_rules_still_present():
    assert ".ai-acct-layout { flex-direction: column; }" in CSS
    assert "#ai-acct-sidebar { width: 100% !important" in CSS

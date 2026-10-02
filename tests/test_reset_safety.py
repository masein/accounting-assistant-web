"""Wiping the books is a Settings action that asks for the company's name typed
back. It also sat under the parties list, one confirm away, for every role
(deep test B3/G0, 2026-10-02)."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
JS = {p.name: p.read_text(encoding="utf-8") for p in (STATIC / "js").glob("*.js")}


def test_the_parties_page_has_no_reset():
    entities = re.search(r'<div class="card" data-page="entities">(.*?)\n    </div>\n', INDEX, re.S).group(1)
    assert "reset" not in entities.lower()
    assert "reset-db-btn" not in INDEX
    assert not any("reset-db-btn" in src for src in JS.values())


def test_every_reset_asks_for_the_name():
    settings = JS["10-forms-fx-bank.js"]
    calls = re.findall(r"/admin/reset-db", settings)
    assert len(calls) == 2                         # the demo loads and the empty wipe
    assert settings.count("uiConfirmTyped({") == 2
    assert "uiConfirm({ message: confirmMsg" not in settings
    ui = JS["03-ui.js"]
    assert "async function uiConfirmTyped(opts)" in ui
    assert "showAlert(t('typeToConfirmMismatch'), true); return false;" in ui

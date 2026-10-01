"""One file per UI language (roadmap 2026-09 §2.6): English stays in
js/02-i18n.js (the fallback), Persian, Spanish and Arabic are fetched only by
someone using them — js/00-lang.js writes the saved language's pack before the
app scripts, applyLanguage() fetches another on a switch. 02-i18n.js was
588 KB with all four."""
from __future__ import annotations

import re

from tests.i18n_source import JS, PACKS, pack_keys

CORE = (JS / "02-i18n.js").read_text(encoding="utf-8")
LOADER = (JS / "00-lang.js").read_text(encoding="utf-8")
INDEX = (JS.parent / "index.html").read_text(encoding="utf-8")


def test_english_alone_is_in_the_core_file():
    assert re.findall(r"^      (en|fa|es|ar): \{", CORE, re.M) == ["en", "en"]
    assert len(CORE.encode("utf-8")) < 200_000, "02-i18n.js grew back — new languages go in js/i18n/"


def test_each_pack_registers_itself_with_every_key():
    keys = pack_keys()
    for lang in ("fa", "es", "ar"):
        text = PACKS[lang].read_text(encoding="utf-8")
        assert f"window.I18N_PACKS.{lang} = Object.assign({{" in text
        assert keys[lang] == keys["en"] and len(keys[lang]) > 2000


def test_the_saved_language_loads_before_the_app_scripts():
    scripts = re.findall(r'<script src="/static/js/([^"]+)"', INDEX)
    assert scripts[0] == "00-lang.js" and scripts[1] == "01-core.js"
    tag = re.search(r'<script src="/static/js/00-lang.js"([^>]*)>', INDEX).group(1)
    for lang in ("fa", "es", "ar"):
        assert f'data-pack-{lang}="/static/js/i18n/{lang}.js"' in tag
    # a parser-inserted script, so it runs before 02-i18n.js; only a pack URL
    assert r"""document.write('<script src="' + url + '"><\/script>')""" in LOADER
    assert r"/^\/static\/js\/i18n\/[a-z]{2}\.js(\?v=[0-9a-f]+)?$/" in LOADER
    assert "localStorage.getItem('aa_ui_language')" in LOADER


def test_the_pack_urls_are_versioned_like_every_script():
    from app.main import render_versioned_html
    html = render_versioned_html("index.html")
    for lang in ("fa", "es", "ar"):
        assert re.search(rf'data-pack-{lang}="/static/js/i18n/{lang}\.js\?v=[0-9a-f]{{8}}"', html), lang


def test_the_service_worker_version_covers_the_packs(monkeypatch):
    import app.main as main
    before = main.app_build_version()
    real = main._asset_version
    monkeypatch.setattr(main, "_asset_version",
                        lambda rel: "changed0" if rel.endswith("i18n/fa.js") else real(rel))
    assert main.app_build_version() != before


def test_a_switch_fetches_a_missing_pack_first():
    apply = CORE[CORE.index("function applyLanguage("):]
    head = apply[:apply.index("currentLanguage = normalized;")]
    assert "if (!I18N[normalized])" in head and "loadLanguagePack(normalized).then(" in head
    loader = CORE[CORE.index("function loadLanguagePack("):]
    assert "script[data-pack-' + lang + ']" in loader and "s.onerror" in loader
    assert "Object.keys(window.I18N_PACKS || {}).forEach" in CORE        # the preloaded pack is picked up


def test_the_top_bar_switch_sticks_and_redraws():
    forms = (JS / "10-forms-fx-bank.js").read_text(encoding="utf-8")
    assert "tbLang.addEventListener('change', () => switchLanguage(tbLang.value))" in forms
    switch = CORE[CORE.index("async function switchLanguage("):]
    switch = switch[:switch.index("\n    }\n")]
    assert "await loadLanguagePack(lang)" in switch
    assert "'/auth/preferences'" in switch and "method: 'PATCH'" in switch      # the account's preference
    assert "loadPageData(page)" in switch                                       # script-drawn content redrawn


def test_no_key_is_written_twice_in_a_pack():
    """In an object literal the later of two same-named keys wins, silently:
    the invoice-reminder setting's "Automatic reminders" retitled the
    notifications panel's "Reminders", and a "Failed" added for one alert
    replaced the messenger's "That did not work — try again." everywhere."""
    from collections import Counter

    from tests.i18n_source import PACKS

    repeated = {}
    for lang, path in PACKS.items():
        text = path.read_text(encoding="utf-8")
        blocks = [m.group(1) for m in re.finditer(r"^      en: \{\n(.*?)^      \},?$", text, re.M | re.S)] if lang == "en" else [text]
        for block in blocks:
            counts = Counter(re.findall(r"^        ([A-Za-z_][A-Za-z0-9_]*)\s*:", block, re.M))
            if dup := sorted(k for k, n in counts.items() if n > 1):
                repeated.setdefault(lang, []).extend(dup)
    assert repeated == {}

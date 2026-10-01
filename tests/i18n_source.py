"""The UI strings as tests read them (roadmap 2026-09 §2.6): English in
js/02-i18n.js, every other language in its own js/i18n/<lang>.js pack.

``i18n_text()`` is all four in en, fa, es, ar order — so a check that a key
appears four times, or reads a function from 02-i18n.js, works as before."""
from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
PACKS = {"en": JS / "02-i18n.js", "fa": JS / "i18n" / "fa.js", "es": JS / "i18n" / "es.js", "ar": JS / "i18n" / "ar.js"}
_KEY = re.compile(r"^        ([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[\"']", re.M)


def i18n_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in PACKS.values())


def pack_keys() -> dict[str, set[str]]:
    """Keys per language. English: the keys inside 02-i18n.js's ``en: {`` blocks;
    the others: every key line of their pack file."""
    out: dict[str, set[str]] = {}
    en = PACKS["en"].read_text(encoding="utf-8")
    keys: set[str] = set()
    for m in re.finditer(r"^      en: \{\n(.*?)^      \},?$", en, re.M | re.S):
        keys |= set(_KEY.findall(m.group(1)))
    out["en"] = keys
    for lang in ("fa", "es", "ar"):
        out[lang] = set(_KEY.findall(PACKS[lang].read_text(encoding="utf-8")))
    return out


_VALUE = re.compile(r"""^        ([A-Za-z_][A-Za-z0-9_]*)\s*:\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')\s*,?\s*$""", re.M)


def pack_values() -> dict[str, dict[str, str]]:
    """Each language's key → text (only the one-line string values)."""
    import json

    def read(text: str) -> dict[str, str]:
        out = {}
        for key, raw in _VALUE.findall(text):
            if raw[0] == "'":
                raw = '"' + raw[1:-1].replace("\\'", "'").replace('"', '\\"') + '"'
            out[key] = json.loads(raw)
        return out

    en = PACKS["en"].read_text(encoding="utf-8")
    blocks = "\n".join(m.group(1) for m in re.finditer(r"^      en: \{\n(.*?)^      \},?$", en, re.M | re.S))
    values = {"en": read(blocks)}
    for lang in ("fa", "es", "ar"):
        values[lang] = read(PACKS[lang].read_text(encoding="utf-8"))
    return values

"""The Persian keeps to one glossary (docs/FA_GLOSSARY.md): the words it rules
out stay out of what users read on the web, on the phone and in the server's
messages (Persian copy review, 2026-10-31, scenarios BD1–BD8)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.core.messages import EXACT, PATTERNS
from tests.i18n_source import pack_values

ROOT = Path(__file__).resolve().parents[1]

# ruled out → what the glossary uses instead
RULED_OUT = {
    "گذرواژه": "رمز عبور",
    "نمودار حساب": "کدینگ حساب‌ها",
    "صورت جریان وجوه نقد": "صورت جریان‌های نقدی",
    "نگاشت": "معادل‌سازی",
    "مهاجرت": "انتقال اطلاعات",
    "موجودیت": "اشخاص / طرف حساب",
    "بارگیری": "بارگذاری / دانلود",
    "پیوند": "لینک",
    "گفت‌وگو": "گفتگو",
    "یادداشت بستانکار": "برگ بستانکار",
    "نرخ سوخت": "نرخ مصرف نقدینگی",
    "دادهٔ آزمایشی": "دادهٔ نمایشی",
    "صف تأیید": "کارتابل تأیید",
    "چت هوش مصنوعی": "گفتگوی هوشمند",
    "ي": "ی (Persian yeh)",
    "ك": "ک (Persian kaf)",
}
# «می شود» → «می‌شود»: the verb prefix takes a half-space
_LOOSE_PREFIX = re.compile(r"(?<![؀-ۿ‌])(ن?می) (?=[؀-ۿ])")


def _android() -> dict[str, str]:
    root = ET.parse(ROOT / "android/app/src/main/res/values-fa/strings.xml").getroot()
    out = {}
    for el in root:
        if el.tag == "string":
            out[el.get("name")] = "".join(el.itertext())
        elif el.tag == "plurals":
            for item in el:
                out[f"{el.get('name')}/{item.get('quantity')}"] = "".join(item.itertext())
    return out


def _server() -> dict[str, str]:
    out = {en: langs["fa"] for en, langs in EXACT.items() if "fa" in langs}
    out.update({p.regex.pattern: p.text["fa"] for p in PATTERNS if "fa" in p.text})
    return out


SOURCES = {
    "web (app/static/js/i18n/fa.js)": lambda: pack_values()["fa"],
    "phone (values-fa/strings.xml)": _android,
    "server (app/core/messages.py)": _server,
}


@pytest.mark.parametrize("where", SOURCES)
def test_the_persian_uses_the_glossarys_words(where):
    texts = SOURCES[where]()
    assert len(texts) > 100, where
    found = [f"{key}: «{word}» → {use}" for key, text in texts.items()
             for word, use in RULED_OUT.items() if word in text]
    found += [f"{key}: «{m.group(0)}» → «{m.group(1)}‌»" for key, text in texts.items()
              for m in _LOOSE_PREFIX.finditer(text)]
    assert not found, f"{where}: words docs/FA_GLOSSARY.md rules out:\n" + "\n".join(found)


def test_the_scan_sees_a_ruled_out_word():
    text = "این پیوند منقضی شده است و دیگر کار نمی کند"
    assert [w for w in RULED_OUT if w in text] == ["پیوند"]
    assert [m.group(1) for m in _LOOSE_PREFIX.finditer(text)] == ["نمی"]
    assert not _LOOSE_PREFIX.search("کار نمی‌کند")

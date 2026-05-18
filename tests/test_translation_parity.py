"""Sanity check that the four language packs in app/static/index.html
have parity — every key in the English pack must also exist in fa, es,
and ar (and vice-versa). Guards against regressions when new strings
are added but only translated in one pack.

The English pack is the source of truth: keys missing from another
language are flagged as errors, but keys present in a non-en pack but
absent from en are also flagged (they indicate stale translations).
"""
from __future__ import annotations

import re
from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def _parse_lang_packs() -> dict[str, set[str]]:
    html = INDEX_HTML.read_text()
    # Match every `      LANG: {` opening at column 6; balance braces until
    # the matching closer at column 6 so we capture the full body.
    lang_pat = re.compile(r"^      (en|fa|es|ar): \{", re.M)
    # Match `        key: '...'` OR `        key: "..."`
    key_re = re.compile(
        r"^        ([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[\"']",
        re.M,
    )

    keys_by_lang: dict[str, set[str]] = {"en": set(), "fa": set(), "es": set(), "ar": set()}
    for m in lang_pat.finditer(html):
        lang = m.group(1)
        start = m.end()
        depth, i, in_str = 1, start, None
        while i < len(html) and depth > 0:
            c = html[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
            else:
                if c in ("'", '"'):
                    in_str = c
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        body = html[start:i]
        for km in key_re.finditer(body):
            keys_by_lang[lang].add(km.group(1))
    return keys_by_lang


def test_all_four_language_packs_have_the_same_keys() -> None:
    keys = _parse_lang_packs()
    en = keys["en"]
    assert en, "English language pack should not be empty"
    failures = []
    for lang in ("fa", "es", "ar"):
        missing = sorted(en - keys[lang])
        extra = sorted(keys[lang] - en)
        if missing:
            failures.append(
                f"{lang}: missing {len(missing)} keys present in en — first 10: {missing[:10]}"
            )
        if extra:
            failures.append(
                f"{lang}: has {len(extra)} keys NOT in en (likely stale) — first 10: {extra[:10]}"
            )
    if failures:
        raise AssertionError(
            f"Language-pack parity failures:\n  " + "\n  ".join(failures)
        )


def test_each_language_pack_has_at_least_400_keys() -> None:
    """Smoke check that none of the packs has shrunk dramatically."""
    keys = _parse_lang_packs()
    for lang, key_set in keys.items():
        assert len(key_set) >= 400, (
            f"Language pack {lang!r} has only {len(key_set)} keys — expected ≥ 400"
        )

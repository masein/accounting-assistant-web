"""Sanity check that the four language packs (English in app/static/js/02-i18n.js,
the others in app/static/js/i18n/<lang>.js)
have parity — every key in the English pack must also exist in fa, es,
and ar (and vice-versa). Guards against regressions when new strings
are added but only translated in one pack.

The English pack is the source of truth: keys missing from another
language are flagged as errors, but keys present in a non-en pack but
absent from en are also flagged (they indicate stale translations).
"""
from __future__ import annotations


def _parse_lang_packs() -> dict[str, set[str]]:
    # English in js/02-i18n.js, the others in js/i18n/<lang>.js (roadmap §2.6)
    from tests.i18n_source import pack_keys
    return pack_keys()


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

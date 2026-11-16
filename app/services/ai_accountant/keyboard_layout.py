"""English typed with the Persian keyboard layout (chat review 2026-11-10, item 25, scenario CI3).

From production: «یثغ ذشممشدزث» was answered "I didn't understand" — it is "dey ballance" typed with the Persian
layout on: each key gave the Persian letter in its place. A message all in Persian letters that, read back through
the layout, is English words gets that reading handed to the model beside it. Persian that happens to read as a
stray English word or two isn't one: most of its words must be English, and one of them three letters or more.
"""
from __future__ import annotations

import re

# The standard Persian layout (ISIRI 9147), and the phone's: the letter each QWERTY key gives
_KEYS = {
    "q": "ض", "w": "ص", "e": "ث", "r": "ق", "t": "ف", "y": "غ", "u": "ع", "i": "ه", "o": "خ", "p": "ح",
    "[": "ج", "]": "چ", "a": "ش", "s": "س", "d": "ی", "f": "ب", "g": "ل", "h": "ا", "j": "ت", "k": "ن",
    "l": "م", ";": "ک", "'": "گ", "z": "ظ", "x": "ط", "c": "ز", "v": "ر", "b": "ذ", "n": "د", "m": "پ",
    ",": "و",
}
_BACK = {fa: en for en, fa in _KEYS.items()}
_BACK.update({"ي": "d", "ك": ";"})              # the Arabic forms a phone may send

# Words someone asks their books in English — and the banks' names as they're written in English
_WORDS = frozenset("""
a about account accounts add all am an and any are as at back bal balance balances bank banks bill bills book books
budget buy bought by can cancel card cards cash category check cost costs day days debt debts delete did do does
done down due each edit entry entries every expense expenses fee fees food for from get give go got have help her
his how i in income invoice invoices is it last left list loan me money month months more much my net new next no
not now of on one or our out owe owed owes paid pay payment payments please profit receipt receipts record rent
report salary sales save saving savings see sell send sent show sold spend spending spent statement sum summary
tax taxes taxi that the their them this to today total transfer transfers tx undo up us wallet wallets was we
week weeks what when where which who why with year years yes yesterday you your
dey mellat melli saderat tejarat sepah pasargad saman ayandeh parsian keshavarzi maskan refah sina shahr
eghtesad novin resalat karafarin sarmayeh tosee mehr iran zamin gardeshgari khavarmianeh hekmat kosar
""".split())

_PERSIAN_WORD = re.compile(r"^[؀-ۿ]+$")


def _near(word: str) -> bool:
    """A word of the list, or one letter more or less than one of five letters or more («ballance»)."""
    if word in _WORDS:
        return True
    if len(word) < 5:
        return False
    for i in range(len(word)):
        if word[:i] + word[i + 1:] in _WORDS:                       # a letter too many
            return True
    return any(len(w) == len(word) + 1 and any(w[:i] + w[i + 1:] == word for i in range(len(w)))
               for w in _WORDS if len(w) >= 5)


def as_english(message: str | None) -> str | None:
    """The English a message typed with the Persian layout says, or None when it isn't one."""
    text = (message or "").strip()
    if not text or len(text) > 120:
        return None
    tokens = text.split()
    if not tokens or not all(_PERSIAN_WORD.match(t) and all(ch in _BACK for ch in t) for t in tokens):
        return None
    latin = ["".join(_BACK[ch] for ch in t) for t in tokens]
    words = [w for w in latin if len(w) >= 2]
    hits = [w for w in words if w.isalpha() and _near(w)]
    if not hits or not any(len(w) >= 3 for w in hits) or len(hits) * 10 < len(words) * 6:
        return None
    return " ".join(latin)


def note(message: str | None) -> str | None:
    """What the model is told beside such a message."""
    english = as_english(message)
    if not english:
        return None
    return (f"(This message reads as English typed with the Persian keyboard layout on: \"{english}\". "
            "Answer what it says.)")

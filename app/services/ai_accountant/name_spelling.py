"""Names in a reply as the books spell them (chat review 2026-11-10, item 25, scenario CI2).

From production: a balance answer named a bank «آینه …» for «آینده …», and wrote a branch with one of its letters
moved. The tools gave the names right; the model copied them wrong. A word of the reply one slip from a word of a
name a tool returned this turn — a letter dropped, a letter too many inside the word, or one letter moved — while
the word beside it is that name's next (or previous) word exactly, is the name's word. Nothing else is touched: a
different letter («ملی» against «ملت») is another name, a letter added at the end is Persian's own suffix
(«پولم»), and a pair of words some other name has is that name.
"""
from __future__ import annotations

import re
from typing import Any

_ARABIC = re.compile(r"[؀-ۿ]")
_WORD = re.compile(r"[؀-ۿ‌]+")


def _names(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _names(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _names(v, out)
    elif isinstance(obj, str) and 3 <= len(obj) <= 80 and _ARABIC.search(obj):
        head = obj.split(" — ")[0].strip()                  # «بانک … — bank account»
        if 2 <= len(head.split()) <= 8:
            out.add(head)


def _slip(said: str, name: str) -> bool:
    """``said`` is ``name`` with one letter dropped, one too many inside it, or one moved."""
    if said == name or min(len(said), len(name)) < 3:
        return False
    if len(said) == len(name) - 1:                          # a letter dropped
        return any(name[:i] + name[i + 1:] == said for i in range(len(name)))
    if len(said) == len(name) + 1:                          # a letter too many — not at the end (a suffix)
        return any(said[:i] + said[i + 1:] == name for i in range(len(said) - 1))
    if len(said) == len(name) and sorted(said) == sorted(name):      # a letter moved
        for i in range(len(name)):
            rest = name[:i] + name[i + 1:]
            if any(rest[:j] + name[i] + rest[j:] == said for j in range(len(name))):
                return True
    return False


def as_given(text: str, tool_call_log: list[dict] | None) -> str:
    """``text`` with the names the tools gave spelled as they gave them."""
    if not text or not _ARABIC.search(text):
        return text
    names: set[str] = set()
    for entry in tool_call_log or []:
        _names(entry.get("result"), names)
    if not names:
        return text
    pairs: set[tuple[str, str]] = set()
    for n in names:
        w = n.split()
        pairs.update(zip(w, w[1:]))
    words = list(_WORD.finditer(text))
    fixes: dict[int, tuple[int, str]] = {}
    for k, m in enumerate(words):
        said = m.group(0)
        for a, b in pairs:
            prev = words[k - 1].group(0) if k else None
            nxt = words[k + 1].group(0) if k + 1 < len(words) else None
            if nxt == b and _slip(said, a) and (said, b) not in pairs:
                fixes[m.start()] = (m.end(), a)
                break
            if prev == a and _slip(said, b) and (a, said) not in pairs:
                fixes[m.start()] = (m.end(), b)
                break
    if not fixes:
        return text
    out, at = [], 0
    for start in sorted(fixes):
        end, word = fixes[start]
        out.append(text[at:start] + word)
        at = end
    return "".join(out) + text[at:]

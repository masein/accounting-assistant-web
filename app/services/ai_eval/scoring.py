"""Check one chat turn against a scenario's expectations.

``expect`` keys (all optional):

* ``tools_any`` / ``tools_all`` / ``tools_none`` — tool names called
* ``trajectory`` — tools that must appear in this order (others may come between)
* ``proposals`` (exact) / ``proposals_min`` / ``proposals_max`` — cards made
* ``card`` — the first card of ``card.tool`` (or the first card): ``total``
  (a transaction's debits, an invoice's lines, else ``amount``),
  ``debit_prefix`` / ``credit_prefix`` (some line's account code starts with
  one of them, on that side), ``fields`` (tool input equal), ``fields_contain``
  (tool input contains, case-insensitive), ``bank_statement_row`` (the card
  settles a statement row), ``date`` (``today``, ``today-N``, or ISO)
* ``reply_lang`` — ``fa`` or ``en``
* ``reply_contains_any`` — one of these substrings (case-insensitive,
  digits normalised)
* ``reply_numbers_any`` — one of these figures appears in the reply, in any
  digits and with any grouping
* ``max_tool_errors`` — tool calls that failed (unknown tool, bad input, tool
  error); replay allows none
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PERSIAN = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")
# a figure with optional grouping: 2,070,000,000 · 2070000000 · ۲٬۰۷۰٬۰۰۰٬۰۰۰ · 2 070 000 000
_NUMBER = re.compile(r"\d{1,3}(?:[,٬،  ]\d{3})+|\d+")


def normalise(text: str | None) -> str:
    return (text or "").translate(_FA_DIGITS)


def numbers_in(text: str | None) -> set[int]:
    out = set()
    for m in _NUMBER.finditer(normalise(text)):
        out.add(int(re.sub(r"\D", "", m.group(0))))
    return out


def reply_language(text: str | None) -> str:
    """``fa`` when Persian letters outnumber Latin ones (figures and codes aside)."""
    t = text or ""
    return "fa" if len(_PERSIAN.findall(t)) > len(_LATIN.findall(t)) else "en"


def _is_subsequence(want: list[str], got: list[str]) -> bool:
    it = iter(got)
    return all(any(g == w for g in it) for w in want)


def resolve_date(spec: str, today: date) -> date:
    if spec == "today":
        return today
    m = re.fullmatch(r"today([+-])(\d+)", spec)
    if m:
        n = int(m.group(2))
        return today + timedelta(days=n if m.group(1) == "+" else -n)
    return date.fromisoformat(spec)


def card_total(tool: str, inp: dict[str, Any]) -> int:
    if tool == "propose_create_transaction":
        return sum(int(ln.get("debit") or 0) for ln in inp.get("lines") or [])
    if tool == "propose_create_invoice":
        return sum(int(round(float(ln.get("quantity", 1) or 1) * int(ln.get("unit_price") or 0)))
                   for ln in inp.get("lines") or [])
    for key in ("amount", "total_amount"):
        if inp.get(key) is not None:
            return int(inp[key])
    return 0


def _check_card(spec: dict, cards: list[dict], today: date) -> list[str]:
    tool = spec.get("tool")
    pick = [c for c in cards if not tool or c["tool"] == tool]
    if not pick:
        return [f"no {tool or 'card'} card"]
    card = pick[0]
    inp, problems = card["input"], []
    if "total" in spec and card_total(card["tool"], inp) != int(spec["total"]):
        problems.append(f"card total {card_total(card['tool'], inp)} want {spec['total']}")
    lines = inp.get("lines") or []
    for side in ("debit", "credit"):
        prefixes = spec.get(f"{side}_prefix")
        if prefixes and not any(str(ln.get("account_code", "")).startswith(tuple(prefixes))
                                and int(ln.get(side) or 0) > 0 for ln in lines):
            problems.append(f"no {side} line on {prefixes}: {[(ln.get('account_code'), ln.get('debit'), ln.get('credit')) for ln in lines]}")
    for key, want in (spec.get("fields") or {}).items():
        if inp.get(key) != want:
            problems.append(f"card {key}={inp.get(key)!r} want {want!r}")
    for key, want in (spec.get("fields_contain") or {}).items():
        if str(want).lower() not in normalise(str(inp.get(key) or "")).lower():
            problems.append(f"card {key}={inp.get(key)!r} lacks {want!r}")
    if spec.get("bank_statement_row") and not inp.get("bank_statement_row_id"):
        problems.append("card doesn't settle a statement row")
    if "date" in spec:
        want = resolve_date(spec["date"], today).isoformat()
        got = str(inp.get("date") or inp.get("issue_date") or "")[:10]
        if got != want:
            problems.append(f"card date {got or None} want {want}")
    return problems


def score(expect: dict, *, tools: list[str], tool_errors: int, cards: list[dict], reply: str,
          today: date) -> list[str]:
    """Problems with one turn; empty means it passed. ``cards`` are
    ``{"tool": name, "input": tool input}`` in the order they were made."""
    problems: list[str] = []
    if expect.get("tools_any") and not set(expect["tools_any"]) & set(tools):
        problems.append(f"none of {expect['tools_any']} called (called {tools})")
    missing = [t for t in expect.get("tools_all") or [] if t not in tools]
    if missing:
        problems.append(f"{missing} not called (called {tools})")
    banned = [t for t in expect.get("tools_none") or [] if t in tools]
    if banned:
        problems.append(f"called {banned}, which it must not")
    if expect.get("trajectory") and not _is_subsequence(expect["trajectory"], tools):
        problems.append(f"trajectory {expect['trajectory']} not followed (called {tools})")
    n = len(cards)
    if "proposals" in expect and n != expect["proposals"]:
        problems.append(f"{n} cards want {expect['proposals']}")
    if "proposals_min" in expect and n < expect["proposals_min"]:
        problems.append(f"{n} cards want at least {expect['proposals_min']}")
    if "proposals_max" in expect and n > expect["proposals_max"]:
        problems.append(f"{n} cards want at most {expect['proposals_max']}")
    if expect.get("card"):
        problems += _check_card(expect["card"], cards, today)
    if expect.get("reply_lang") and reply_language(reply) != expect["reply_lang"]:
        problems.append(f"reply not in {expect['reply_lang']}: {reply[:80]!r}")
    if expect.get("reply_contains_any"):
        low = normalise(reply).lower()
        if not any(normalise(s).lower() in low for s in expect["reply_contains_any"]):
            problems.append(f"reply mentions none of {expect['reply_contains_any']}: {reply[:80]!r}")
    if expect.get("reply_numbers_any") and not set(expect["reply_numbers_any"]) & numbers_in(reply):
        problems.append(f"reply quotes none of {expect['reply_numbers_any']}: {reply[:80]!r}")
    if expect.get("max_tool_errors") is not None and tool_errors > expect["max_tool_errors"]:
        problems.append(f"{tool_errors} tool calls failed")
    return problems

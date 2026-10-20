"""A statement read from pictures, checked by its own running balance (scenarios AJ2, AJ3).

Each row's balance, less what came in or plus what went out, is the balance
of the row before it. A reader that got a digit wrong breaks that link, and
the break names the page and the row to look at — nothing is posted quietly
wrong. Day Bank's 22-page statement agreed on every one of the 39 links of
its first four pages (2026-10-20). A statement is printed newest first or
oldest first; the order the links agree in is the one used.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _signed(row: dict[str, Any]) -> Decimal:
    amount = Decimal(str(row.get("amount") or 0))
    return amount if row.get("direction") == "credit" else -amount


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) < Decimal("0.01")


def balance_check(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``{links, agree, order, broken: [{page, row, balance, expected}]}``
    over the rows in the order printed (each with ``amount``, ``direction``,
    ``balance`` and ``page``). ``row`` is the row's place on its page."""
    on_page: dict[int, int] = {}
    placed = []
    for r in rows:
        page = int(r.get("page") or 1)
        on_page[page] = on_page.get(page, 0) + 1
        placed.append((r, page, on_page[page]))
    links = [(a, b) for a, b in zip(placed, placed[1:])
             if a[0].get("balance") is not None and b[0].get("balance") is not None]

    def bal(item) -> Decimal:
        return Decimal(str(item[0]["balance"]))

    newest_first = sum(1 for a, b in links if _close(bal(b) + _signed(a[0]), bal(a)))
    oldest_first = sum(1 for a, b in links if _close(bal(a) + _signed(b[0]), bal(b)))
    order = "newest_first" if newest_first >= oldest_first else "oldest_first"

    def agrees(a, b) -> bool:
        if order == "newest_first":
            return _close(bal(b) + _signed(a[0]), bal(a))
        return _close(bal(a) + _signed(b[0]), bal(b))

    bad = [i for i, (a, b) in enumerate(links) if not agrees(a, b)]
    broken = []
    i = 0
    while i < len(bad):
        k = bad[i]
        a, b = links[k]
        if i + 1 < len(bad) and bad[i + 1] == k + 1 and links[k + 1][0] is b:
            # a balance misread breaks the links on both its sides: the row between them is the one
            row, i = b, i + 2
        else:
            # one link: an amount misread — the row whose balance doesn't follow from it
            row, i = (a if order == "newest_first" else b), i + 1
        broken.append({"page": row[1], "row": row[2], "balance": str(bal(row))})
    return {"links": len(links), "agree": len(links) - len(bad), "order": order, "broken": broken[:20],
            "broken_count": len(broken)}


def _one_slip(read: Any, amount: Decimal) -> bool:
    """The two figures differ by one character — a digit dropped, added or
    misread, or the point — and nothing more. A row the reader skipped
    leaves a sum of two amounts next to it, which is no slip."""
    a = format(Decimal(str(read or 0)).normalize(), "f")
    b = format(amount.normalize(), "f")
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if abs(len(a) - len(b)) != 1:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def mend_amounts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A row whose amount alone breaks the chain, between two balances its
    other neighbours vouch for, takes the amount those balances say — in
    place — when the two differ by one slip of a digit (or only in which way
    the money went). Day Bank's statement had two: a card purchase read
    47,522,020 for 847,522,020 and a fee 32,000 for 320,000; both were exactly
    the difference of the balances around them (2026-10-20, scenario AJ3).
    Anything else — a balance misread, a page not read between them, a row
    the reader skipped — is left for the check to name.
    Returns ``[{page, row, read, amount, direction}]``."""
    on_page: dict[int, int] = {}
    placed = []
    for r in rows:
        page = int(r.get("page") or 1)
        on_page[page] = on_page.get(page, 0) + 1
        placed.append((r, page, on_page[page]))
    if len(placed) < 4 or any(r.get("balance") is None for r in rows):
        return []

    def bal(item) -> Decimal:
        return Decimal(str(item[0]["balance"]))

    newest = sum(1 for a, b in zip(placed, placed[1:]) if _close(bal(b) + _signed(a[0]), bal(a)))
    oldest = sum(1 for a, b in zip(placed, placed[1:]) if _close(bal(a) + _signed(b[0]), bal(b)))
    newest_first = newest >= oldest

    def agrees(i: int) -> bool:               # the link between rows i and i+1
        a, b = placed[i], placed[i + 1]
        return _close(bal(b) + _signed(a[0]), bal(a)) if newest_first else _close(bal(a) + _signed(b[0]), bal(b))

    mended = []
    for i in range(1, len(placed) - 2):
        if agrees(i) or not agrees(i - 1) or not agrees(i + 1):
            continue
        # link i alone is broken, and rows i and i+1 each agree with their other neighbour
        a, b = placed[i], placed[i + 1]
        if abs(a[1] - b[1]) > 1:              # a page between them wasn't read
            continue
        row, signed = (a, bal(a) - bal(b)) if newest_first else (b, bal(b) - bal(a))
        before, amount = row[0]["amount"], abs(signed)
        if signed == 0 or not _one_slip(before, amount):
            continue
        row[0]["amount"] = int(amount) if amount == amount.to_integral_value() else float(amount)
        row[0]["direction"] = "credit" if signed > 0 else "debit"
        row[0]["mended"] = True
        mended.append({"page": row[1], "row": row[2], "read": before, "amount": row[0]["amount"],
                       "direction": row[0]["direction"]})
    return mended

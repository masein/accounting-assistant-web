"""The company's own banks a message names (chat review 2026-11-10, scenarios BT1–BT5).

In a company's books the bank the user named lost to the chart's general «موجودی نقد و بانک»: "paid … from <bank>"
was credited to 1110, and «بانک دی شمال» was searched to 1110 above the bank's own account. A bank is named by
its entity's or account's name («بانک دی شمال», «دی شمال» without «بانک», «اینده» for «آینده») or by the bank's
usual spelling in either script ("dey", "ayande", «ملت») when only one of the company's accounts is at that bank.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select

# what an account's name carries around the bank's own name
_DECORATIONS = (re.compile(r"\s*[—–-]\s*bank account$"), re.compile(r"^حساب بانکی\s+"), re.compile(r"^حساب\s+"))
_BANK_WORD = re.compile(r"^(?:بانک|bank)\s+")
_FROM = {"از", "from"}
_TO = {"به", "to", "into"}
# a one-word bank name is a bank only beside one of these: «آینده» is also "the future", «سرمایه» capital, «شهر» a city
_CONTEXT = {"بانک", "bank", "حساب", "account", "کارت", "card", "via", "با"} | _FROM | _TO


@dataclass(frozen=True)
class NamedBank:
    code: str
    start: int
    end: int
    role: str | None = None          # "from" / "to": the word just before it in the message


def fold(text: str | None) -> str:
    """Letters and spaces as the matcher compares them: ي/ك, آ→ا, ZWNJ, punctuation, case."""
    from app.services.bank_sms import normalize
    t = normalize(text or "").lower().replace("آ", "ا").replace("أ", "ا").replace("إ", "ا")
    t = re.sub(r"[^\w\s]", " ", t)
    return " " + " ".join(t.split()) + " "


def money_codes(db) -> set[str]:
    """Every account that holds the company's money (cash, each bank's own account)."""
    from app.services.ai_accountant.cash_tools import cash_account_codes
    from app.services.locale_service import get_reporting_locale
    return set(cash_account_codes(db, locale=get_reporting_locale(db), mode="default"))


def _own_names(name: str) -> set[str]:
    """An account's or bank's name as it may be said: whole, without "— bank account" or «حساب», without «بانک»."""
    out = set()
    base = " ".join((name or "").split())
    for deco in _DECORATIONS:
        base = deco.sub("", base)
    for n in (name, base, _BANK_WORD.sub("", base)):
        n = fold(n).strip()
        if len(n) >= 3:
            out.add(n)
    return out


def _in_context(said: str, start: int, phrase: str) -> bool:
    """A name of several words stands alone; a single word needs a bank word or from/to just before it."""
    return " " in phrase or bool(set(said[:start].split()[-2:]) & _CONTEXT)


def _spellings_of(canon: str) -> set[str]:
    """Every spelling the app knows for one bank: "Dey", «بانک دی», "dey bank", "ayande"…"""
    from app.services.bank_sms import BANKS
    from app.services.statement_import import _label_banks
    out = {canon, *BANKS.get(canon, ())}
    out.update(sp for sp, c in _label_banks()[1] if c == canon)
    return {f for f in (fold(s).strip() for s in out) if len(f) >= 3}


def banks_named(db, text: str | None, *, codes: set[str] | None = None) -> list[NamedBank]:
    """The company's money accounts ``text`` names, in the order it names them (one per account)."""
    said = fold(text)
    if not said.strip():
        return []
    from app.models.account import Account
    from app.models.entity import Entity
    from app.services.statement_import import canonical_bank
    codes = codes if codes is not None else money_codes(db)
    if not codes:
        return []
    names: dict[str, set[str]] = {}
    for acc in db.execute(select(Account).where(Account.code.in_(codes))).scalars():
        names.setdefault(acc.code, set()).update(_own_names(acc.name))
    for ent in db.execute(select(Entity).where(Entity.type == "bank")).scalars():
        code = (ent.code or "").strip()
        if code in codes:
            names.setdefault(code, set()).update(_own_names(ent.name))
    hits: list[tuple[int, int, str, int]] = []           # (start, end, code, strength)
    at_bank: dict[str, set[str]] = {}
    for code, own in names.items():
        for n in own:
            for m in re.finditer(re.escape(f" {n} "), said):
                if _in_context(said, m.start() + 1, n):
                    hits.append((m.start() + 1, m.end() - 1, code, 2))
            canon = canonical_bank(n)
            if canon:
                at_bank.setdefault(canon, set()).add(code)
    for canon, at in at_bank.items():
        for sp in _spellings_of(canon):
            for m in re.finditer(re.escape(f" {sp} "), said):
                if not _in_context(said, m.start() + 1, sp):
                    continue
                # the bank's usual name decides only when one of the company's accounts is at that bank
                hits.append((m.start() + 1, m.end() - 1, next(iter(at)) if len(at) == 1 else "", 1))
    # the fullest name wins: «بانک دی شمال», not «بانک دی» inside it
    kept: list[tuple[int, int, str, int]] = []
    for h in sorted(hits, key=lambda h: (-(h[1] - h[0]), -h[3])):
        if not any(h[0] < k[1] and k[0] < h[1] for k in kept):
            kept.append(h)
    out: list[NamedBank] = []
    seen: set[str] = set()
    for start, end, code, _strength in sorted(kept):
        if not code or code in seen:
            continue
        seen.add(code)
        before = said[:start].split()[-3:]
        role = next(("from" if w in _FROM else "to" for w in reversed(before) if w in _FROM | _TO), None)
        out.append(NamedBank(code, start, end, role))
    return out


def own_transfer(db, text: str | None) -> tuple[str, str] | None:
    """(the bank the money left, the bank it went to) when ``text`` moves money between two of the company's own
    banks: «از دی شمال زدیم به حساب آینده غرب», "from Mellat to Saman"."""
    named = banks_named(db, text)
    src = {n.code for n in named if n.role == "from"}
    dst = {n.code for n in named if n.role == "to"}
    if len(src) == 1 and len(dst) == 1 and src != dst:
        return next(iter(src)), next(iter(dst))
    return None

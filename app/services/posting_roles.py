"""Where each kind of entry posts, when the chart isn't the seeded one.

Postings resolve a category ("ar", "net_pay_payable", "petty_cash", ...) to a
fixed code per locale (``account_resolver.POSTING_CODES``): Iran's 1112,
2180, 1160. After a company migrated from Sepidar its balances sit in its own
sub-ledgers — receivables 111201, salaries payable 211101, petty cash
111003 — so the first invoice or pay run after the migration opened a second
set of balances beside them (2026-10-12).

So a company can point a category at an account of its own (the setting
``posting_roles``), a migration suggests them from its sub-ledgers' titles
(only where exactly one fits), the owner changes them on the migration page,
and ``resolve_account_code`` / ``find_account_code`` read them first.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account, AccountLevel
from app.models.app_setting import AppSetting

SETTINGS_KEY = "posting_roles"

# The categories the page offers, in its order, and how a migrated
# sub-ledger's title names each: (category, the code's first digit,
# alternatives of words that must all appear, words that must not).
_RULES: list[tuple[str, str, tuple[tuple[str, ...], ...], tuple[str, ...]]] = [
    ("bank", "1", (("موجودی", "بانک"),), ("سپرده",)),
    ("petty_cash", "1", (("تنخواه",),), ()),
    ("ar", "1", (("دریافتنی", "تجاری"),), ("اسناد",)),
    ("supplier_advance", "1", (("پیشپرداخت", "خرید"),), ("خدمات",)),
    ("prepaid_expense", "1", (("پیشپرداخت", "هزینه"),), ()),
    ("vat_input", "1", (("ارزشافزوده",),), ()),
    ("ap", "2", (("پرداختنی", "تجاری"),), ("اسناد",)),
    ("customer_credit", "2", (("پیشدریافت",),), ()),
    ("vat_output", "2", (("ارزشافزوده",),), ()),
    ("net_pay_payable", "2", (("حقوق", "پرداختنی"),), ("مالیات", "بیمه")),
    ("paye_payable", "2", (("مالیات", "حقوق"),), ()),
    ("social_security_payable", "2", (("بیمه", "پرداختنی"),), ()),
    ("shareholder_current", "2", (("جاری", "شرکا"), ("جاری", "سهامدار")), ()),
    ("share_capital", "3", (("سرمایه",),), ("افزایش", "گذاری")),
    ("retained_earnings", "3", (("انباشته",),), ()),
    ("revenue", "4", (("فروش",),), ("برگشت", "تخفیف")),
    ("wages_expense", "6", (("حقوق",),), ("بیمه", "مالیات")),
    ("employer_social_expense", "6", (("بیمه", "کارفرما"),), ()),
    ("expense", "6", (("متفرقه",),), ()),
    ("bank_fee", "6", (("کارمزد",),), ()),
    ("depreciation_expense", "6", (("استهلاک",),), ()),
]
CATEGORIES = tuple(r[0] for r in _RULES)


def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).scalars().first()


def _memo_key() -> tuple:
    from app.db.tenant import get_current_company
    return ("posting_roles", get_current_company())


def get_overrides(db: Session) -> dict[str, dict]:
    """{category: {"code": ..., "source": "migration" | "owner"}} — read once
    per session and company (every posting resolves a category)."""
    key = _memo_key()
    if key in db.info:
        return db.info[key]
    row = _row(db)
    data: dict[str, dict] = {}
    if row and row.value:
        try:
            raw = json.loads(row.value) or {}
            # an owner's "the chart's default" is kept too (code None), so an import doesn't re-suggest it
            data = {c: v for c, v in raw.items() if isinstance(v, dict) and (v.get("code") or v.get("source") == "owner")}
        except ValueError:
            data = {}
    db.info[key] = data
    return data


def override_code(db: Session, category: str) -> str | None:
    """The company's own account for ``category`` when it still takes postings."""
    hit = get_overrides(db).get(category)
    if not hit or not hit.get("code"):
        return None
    acc = db.execute(select(Account.is_active).where(Account.code == hit["code"])).first()
    return hit["code"] if acc is not None and acc[0] else None


def save(db: Session, changes: dict[str, str | None], *, source: str) -> dict[str, dict]:
    """Point categories at a code, or back at the chart's default (None: the
    owner's, kept so a later import doesn't suggest it again). Unknown
    categories are refused by the caller. Flushes."""
    data = dict(get_overrides(db))
    for cat, code in changes.items():
        if code or source == "owner":
            data[cat] = {"code": code or None, "source": source}
        else:
            data.pop(cat, None)
    row = _row(db)
    value = json.dumps(data, ensure_ascii=False)
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    db.flush()
    db.info[_memo_key()] = data
    return data


def suggest(db: Session) -> dict[str, str]:
    """category → the one migrated sub-ledger (معین) whose title names it; a
    category two sub-ledgers fit is left for the owner."""
    from app.services.reporting.statement_lines import _squash

    subs = db.execute(
        select(Account.code, Account.name).where(Account.level == AccountLevel.SUB, Account.is_active.is_(True))
    ).all()
    out: dict[str, str] = {}
    for cat, head, alternatives, never in _RULES:
        hits = [code for code, name in subs
                if code.startswith(head)
                and any(all(w in _squash(name) for w in words) for words in alternatives)
                and not any(w in _squash(name) for w in never)]
        if len(hits) == 1:
            out[cat] = hits[0]
    # one VAT account for both sides (Sepidar's «مالیات ارزش افزوده»): input VAT
    # posted to it nets the liability, as it does in Sepidar
    if "vat_output" in out and "vat_input" not in out:
        out["vat_input"] = out["vat_output"]
    return out


def apply_suggestions(db: Session) -> dict[str, str]:
    """Take the chart's suggestions for every category the owner hasn't set;
    returns the ones that changed."""
    current = get_overrides(db)
    changes = {cat: code for cat, code in suggest(db).items()
               if current.get(cat, {}).get("source") != "owner" and current.get(cat, {}).get("code") != code}
    if changes:
        save(db, changes, source="migration")
    return changes


def view(db: Session) -> list[dict]:
    """Each category the page offers: where it posts now, its default, and who chose."""
    from app.services.account_resolver import find_account_code

    overrides = get_overrides(db)
    names = dict(db.execute(select(Account.code, Account.name)).all())
    out = []
    for cat in CATEGORIES:
        code = find_account_code(db, cat)
        out.append({"category": cat, "code": code, "name": names.get(code) if code else None,
                    "default_code": find_account_code(db, cat, own=False), "source": (overrides.get(cat) or {}).get("source")})
    return out

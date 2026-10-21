"""A company switched to its own account codes (docs/PLAN_OWN_CODES.md phase 2, scenarios AN3–AN6).

The owner chose, 2026-10-21, that فرهنگ نو's books carry the school's codes only. Its journals were
imported with the app's codes and the school's kept beside them (``accounts.source_codes``, #378): 6131
«هزینه های عمومی» is the school's 3000, 613104 «پذیرایی…» its 3001. The switch makes each account's code
the school's, keeps the app's code as the account's class (``class_code``, so every rule still knows what
it is: ``app.services.chart_codes``), and moves everything that names an account by its code along.

``preview`` says what would change and what must be settled first:

* **twins**: two accounts the school's journal gave one code under two spellings («وزارت دارایی» and
  «دارایی», both 1602): merged into one, the app's own account kept when one of them is (its role
  follows it);
* **clashes**: one code for two different accounts (4000 is «هزینه های پرسنلی» and, in another sheet,
  «سود و زیان»): an account with another code free takes it; otherwise the user gives one;
* **aside**: an app account nothing uses whose code the school takes (its 1110 «موجودی نقد و بانک»,
  the school's 1110 is «تنخواه گردان»): it keeps working under a free code.

``apply`` does it, with the user's codes, in one transaction: an audit row keeps every old → new code.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.account import Account, AccountLevel

# Every column that names an account by its code (a test keeps this list whole).
CODE_REFERENCES = (
    ("app.models.bank_statement", "BankStatement", "bank_account_code"),
    ("app.models.bank_statement", "BankStatementRow", "suggested_account_code"),
    ("app.models.commitment", "Commitment", "counter_account_code"),
    ("app.models.commitment", "Commitment", "holding_account_code"),
    ("app.models.commitment", "Commitment", "deposit_account_code"),
    ("app.models.entity", "Entity", "code"),
    ("app.models.fixed_asset", "FixedAsset", "asset_account_code"),
    ("app.models.fixed_asset", "FixedAsset", "accumulated_account_code"),
    ("app.models.fixed_asset", "FixedAsset", "expense_account_code"),
    ("app.models.learned_preference", "LearnedPreference", "account_code"),
    ("app.models.personal_holding", "PersonalHolding", "account_code"),
    ("app.models.petty_cash", "PettyCashTransaction", "counter_account_code"),
    ("app.models.recurring", "RecurringRule", "bank_account_code"),
    ("app.models.recurring", "RecurringRule", "counter_account_code"),
    ("app.models.savings_goal", "SavingsGoal", "account_code"),
    ("app.models.trial_balance", "TrialBalanceLine", "account_code"),
    ("app.models.budget", "BudgetLimit", "category"),               # a budget set by code
)


def _norm(name: str | None) -> str:
    from app.utils.text import fold_fa
    s = fold_fa((name or "").lower())
    return " ".join(re.sub(r"[\s\-–_.:،,()]+", " ", s).split())


def _twins(a: str, b: str) -> bool:
    """One account under two spellings: «دارایی» in «وزارت دارایی», «حقوق پرداختنی» and «حقوق پرداختنی -»."""
    x, y = _norm(a), _norm(b)
    return bool(x and y) and (x == y or x in y or y in x)


def _codes(acc: Account) -> list[str]:
    return [c.strip() for c in (acc.source_codes or "").split(",") if c.strip()]


def _used(db: Session) -> dict:
    from app.models.transaction import TransactionLine, include_deleted_transactions
    with include_deleted_transactions():
        return dict(db.execute(select(TransactionLine.account_id, func.count())
                               .group_by(TransactionLine.account_id)).all())


def _protected(db: Session) -> set[str]:
    from app.services.chart_service import protected_codes
    return protected_codes(db)


def preview(db: Session) -> dict[str, Any]:
    """What the switch would do (nothing is written)."""
    accounts = db.execute(select(Account).order_by(Account.code)).scalars().all()
    if any(a.class_code for a in accounts):
        raise HTTPException(status_code=409, detail="These books already use their own codes.")
    used = _used(db)
    protected = _protected(db)
    # 1) each account its code: one it alone has, else its first
    wanted = {a.id: _codes(a) for a in accounts}
    single = Counter(codes[0] for codes in wanted.values() if len(codes) == 1)
    proposed: dict = {}
    for a in accounts:
        codes = wanted[a.id]
        if not codes:
            continue
        free = [c for c in codes if not single.get(c)] if len(codes) > 1 else codes
        proposed[a.id] = (free or codes)[0]
    # 2) one code, several accounts: twins merge into one; different accounts clash
    by_code: dict[str, list[Account]] = defaultdict(list)
    for a in accounts:
        if a.id in proposed:
            by_code[proposed[a.id]].append(a)
    merges: list[dict] = []
    clashes: list[dict] = []
    for code, group in by_code.items():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda a: (a.code in protected, used.get(a.id, 0), -len(a.code)))
        if all(_twins(a.name, keep.name) for a in group):
            merges.append({"code": code, "keep": str(keep.id),
                           "merge": [str(a.id) for a in group if a.id != keep.id]})
        else:
            clashes.append({"code": code, "accounts": [str(a.id) for a in group]})
    merged = {m for g in merges for m in g["merge"]}
    # 3) an account nothing uses, in the way of a code the company takes: aside, under a free code
    taken = set(proposed.values())
    every = {a.code for a in accounts}
    aside: list[dict] = []
    for a in accounts:
        if a.id in proposed or a.code not in taken:
            continue
        if used.get(a.id):
            clashes.append({"code": a.code, "accounts": [str(a.id)] + [str(b.id) for b in by_code.get(a.code, [])]})
            continue
        new = a.code
        while new in every or new in taken:
            new += "0"
        every.add(new)
        aside.append({"id": str(a.id), "code": a.code, "new_code": new})
    rows = [{"id": str(a.id), "code": a.code, "name": a.name, "level": a.level.value, "lines": used.get(a.id, 0),
             "options": wanted[a.id], "proposed": proposed.get(a.id),
             "merged_into": next((g["keep"] for g in merges if str(a.id) in g["merge"]), None)}
            for a in accounts]
    return {"accounts": rows, "merges": merges, "clashes": clashes, "aside": aside,
            "ready": not clashes and bool(proposed),
            "counts": {"recoded": sum(1 for a in accounts if a.id in proposed and str(a.id) not in merged),
                       "merged": len(merged), "aside": len(aside), "clashes": len(clashes)}}


def apply(db: Session, *, codes: dict[str, str], merges: list[dict]) -> dict[str, Any]:
    """Switch the books to their own codes: ``codes`` (account id → its code, the preview's with the user's
    changes) and ``merges`` ([{keep, merge: [ids]}]). Everything in one go, or nothing."""
    import uuid as _uuid
    accounts = {str(a.id): a for a in db.execute(select(Account)).scalars().all()}
    if any(a.class_code for a in accounts.values()):
        raise HTTPException(status_code=409, detail="These books already use their own codes.")
    bad = [c for c in codes.values() if not re.fullmatch(r"\d{1,16}", (c or "").strip())]
    if bad:
        raise HTTPException(status_code=422, detail="An account code is digits only, at most 16.")
    unknown = [i for i in list(codes) + [m for g in merges for m in [g["keep"], *g["merge"]]] if i not in accounts]
    if unknown:
        raise HTTPException(status_code=422, detail="An account in the request isn't in these books.")
    gone = {m for g in merges for m in g["merge"]}
    # the final code of every account that stays: its new code, or the one it has
    final = {i: (codes.get(i) or a.code).strip() for i, a in accounts.items() if i not in gone}
    dupes = [c for c, n in Counter(final.values()).items() if n > 1]
    if dupes:
        raise HTTPException(status_code=409, detail=f"Code {dupes[0]} is given to more than one account.")

    old_code = {i: a.code for i, a in accounts.items()}
    # 1) every account keeps the app's code for what it is
    for a in accounts.values():
        a.class_code = a.code
    # 2) twins become one: their lines, their codes, the parent the company's own chart gives
    from app.models.transaction import TransactionLine
    from app.models.entity import Entity
    moved_lines = 0
    for g in merges:
        keep = accounts[g["keep"]]
        for other_id in g["merge"]:
            other = accounts[other_id]
            res = db.execute(update(TransactionLine).where(TransactionLine.account_id == other.id)
                             .values(account_id=keep.id).execution_options(synchronize_session=False))
            moved_lines += res.rowcount or 0
            db.execute(update(Entity).where(Entity.account_id == other.id).values(account_id=keep.id)
                       .execution_options(synchronize_session=False))
            keep.source_codes = ",".join(dict.fromkeys(_codes(keep) + _codes(other)))[:255] or None
            parent = db.get(Account, keep.parent_id) if keep.parent_id else None
            if (parent is None or not _codes(parent)) and other.parent_id:
                theirs = db.get(Account, other.parent_id)
                if theirs is not None and _codes(theirs):
                    keep.parent_id = theirs.id          # under the company's own heading
            for child in db.execute(select(Account).where(Account.parent_id == other.id)).scalars():
                child.parent_id = keep.id
            other.is_active, other.parent_id = False, None      # its shell, aside: its lines are the kept one's
    # 3) the codes, through placeholders, so no two accounts ever hold one
    for i, a in accounts.items():
        a.code = f"~{_uuid.uuid4().hex[:12]}"
    db.flush()
    for i, a in accounts.items():
        if i in gone:
            new = old_code[i]
            while new in final.values():
                new += "0"
            final[i] = new                              # a merged twin's shell, put aside under a free code
        a.code = final[i]
    db.flush()
    moved = {old_code[i]: final[i] for i in accounts if old_code[i] != final[i]}
    # 4) everything that names an account by its code follows it
    _follow(db, moved, {old_code[m]: final[g["keep"]] for g in merges for m in g["merge"]})
    # 5) the company's ledgers stand on their own: no app group over them in its Ledger
    for i, a in accounts.items():
        if a.parent_id and i not in gone and _codes(a):
            parent = db.get(Account, a.parent_id)
            if parent is not None and parent.level == AccountLevel.GROUP and not _codes(parent):
                a.parent_id = None
    # 6) cards made before the switch name the old codes: they can't be confirmed now
    from app.models.ai_accountant import AIProposal
    stale = db.execute(update(AIProposal).where(AIProposal.status == "pending").values(status="cancelled")
                       .execution_options(synchronize_session=False)).rowcount or 0
    from app.core.money import json_dumps
    from app.services.audit_service import log_audit_event
    log_audit_event(db, "own_codes", "chart", entity_id=None,
                    detail=json_dumps({"moved": moved, "merged": [{"keep": final[g["keep"]],
                                                                    "merged": [old_code[m] for m in g["merge"]]}
                                                                   for g in merges]}, ensure_ascii=False)[:60000])
    db.flush()
    _forget_caches()
    return {"recoded": len(moved), "merged": len(gone), "lines_moved": moved_lines, "cards_cancelled": stale,
            "moved": moved}


def _follow(db: Session, moved: dict[str, str], merged: dict[str, str]) -> None:
    """Every stored code of an account moves with it: statements, commitments, parties, fixed assets,
    learned preferences, holdings, petty cash, recurring rules, savings goals, trial balances, budgets, and
    the settings that name accounts (posting roles, the journal import's map, MTD categories)."""
    import importlib
    import json
    remap = {**moved, **merged}
    if not remap:
        return
    from sqlalchemy import case
    for module, cls_name, attr in CODE_REFERENCES:
        cls = getattr(importlib.import_module(module), cls_name)
        col = getattr(cls, attr)
        # one update, each row by the code it had: one pair at a time moved petty cash 111003 → 1110 and then,
        # as the app's own 1110 went aside, on to 11100 — on Postgres, whose order the pairs came in (AN6)
        db.execute(update(cls).where(col.in_(list(remap))).values({attr: case(remap, value=col)})
                   .execution_options(synchronize_session=False))
    from app.models.app_setting import AppSetting
    from app.services.journal_import import MAP_KEY
    from app.services.posting_roles import SETTINGS_KEY as ROLES_KEY
    from app.services.uk_mtd.settings import KEY as MTD_KEY
    for row in db.execute(select(AppSetting).where(AppSetting.key.in_((ROLES_KEY, MAP_KEY, MTD_KEY)))).scalars():
        try:
            value = json.loads(row.value or "{}")
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        if row.key == ROLES_KEY:                        # {category: {"code", "source"}}
            for role in value.values():
                if isinstance(role, dict) and role.get("code") in remap:
                    role["code"] = remap[role["code"]]
        elif row.key == MAP_KEY:                        # {"code|name" in the file: the account's code}
            value = {k: remap.get(v, v) if isinstance(v, str) else v for k, v in value.items()}
        elif isinstance(value.get("category_overrides"), dict):   # MTD: {account code: category}
            value["category_overrides"] = {remap.get(k, k): v for k, v in value["category_overrides"].items()}
        row.value = json.dumps(value, ensure_ascii=False)
    from app.services.posting_roles import _memo_key
    db.info.pop(_memo_key(), None)                      # read again: the session remembered the old codes
    db.flush()


def _forget_caches() -> None:
    try:
        from app.api.reports import invalidate_dashboard_cache
        invalidate_dashboard_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.insight_service import invalidate_insights_cache
        invalidate_insights_cache()
    except Exception:  # noqa: BLE001
        pass

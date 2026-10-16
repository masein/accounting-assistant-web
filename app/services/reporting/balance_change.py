"""What changed on the balance sheet (scenarios AA1–AA6).

The statement gives each line on two dates; the owner asked why assets rose
from 27.95 billion rials to about 47: was it cash, or something else? A line
is a bucket of accounts (the Iranian statement's lines, the UK's FRS 102 ones),
so this lists the accounts behind one line with their balance on both dates
and the change, biggest first. They add up to the line: the result not yet
closed into retained earnings (folded into it so the statement balances) is a
row of its own."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.money import plain
from app.services.reporting.common import balance_from_turnovers, classify_account_code
from app.services.reporting.repository import account_turnovers_upto, list_accounts

PL_ROW = "pl_to_date"            # the profit or loss not yet closed, on the retained-earnings line


def line_accounts(db: Session, *, statement: str, key: str, as_of: date, comparative_as_of: date,
                  currency: str | None = None) -> dict:
    """``statement`` is "iran" or "uk"; ``key`` a line of it (``ca_cash``, ``ca_debtors``…)."""
    if statement == "uk":
        from app.services.reporting import uk_statement_service as svc
        bucket_of = svc._bs_bucket_for_code
        pl_to_date, pl_key = svc._uk_pl_to_date, "eq_pl_account"
    else:
        from app.services.reporting import iran_statement_service as svc
        from app.services.reporting.statement_lines import line_map
        lines = line_map(db)
        bucket_of = lambda code: svc._bs_bucket_for_code(code, lines)  # noqa: E731
        pl_to_date, pl_key = svc._ir_pl_to_date, "eq_retained_earnings"

    accounts = list_accounts(db)
    now = {a: (d, c) for a, d, c in account_turnovers_upto(db, as_of, currency=currency)}
    then = {a: (d, c) for a, d, c in account_turnovers_upto(db, comparative_as_of, currency=currency)}
    rows = []
    for acc in accounts:
        bucket = bucket_of(acc.code or "")
        if not bucket or bucket[1] != key:
            continue
        kind = classify_account_code(acc.code or "")
        cur = plain(balance_from_turnovers(kind, *now.get(acc.id, (0, 0))))
        pri = plain(balance_from_turnovers(kind, *then.get(acc.id, (0, 0))))
        if cur == 0 and pri == 0:
            continue
        rows.append({"code": acc.code, "name": acc.name, "current": cur, "prior": pri, "change": plain(cur - pri)})
    if key == pl_key:
        cur, pri = plain(pl_to_date(db, accounts, as_of, currency)), plain(pl_to_date(db, accounts, comparative_as_of, currency))
        if cur or pri:
            rows.append({"code": None, "name": None, "kind": PL_ROW, "current": cur, "prior": pri, "change": plain(cur - pri)})
    rows.sort(key=lambda r: (-abs(r["change"]), r["code"] or ""))
    cur_total = plain(sum(r["current"] for r in rows))
    pri_total = plain(sum(r["prior"] for r in rows))
    return {"statement": statement, "key": key, "as_of": as_of.isoformat(), "comparative_as_of": comparative_as_of.isoformat(),
            "accounts": rows, "total": {"current": cur_total, "prior": pri_total, "change": plain(cur_total - pri_total)}}

"""A budget set by an account's code counts that account's spending (deep
browser test, 2026-10-02, #50).

The category field offers the chart's codes (#274), but actuals were matched
by account name only, so a budget saved as «6110» read 0 spent — and its
overspend alert never fired."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db.tenant import use_company
from app.services.reporting.common import EXPENSE, classify_account_code
from tests.test_time_billing_and_fees_http import co  # noqa: F401 — the company fixture


def _codes(api):
    """Two expense accounts under one group, one in another, and a bank account."""
    accounts = [a for a in api.get("/accounts").json() if len(a["code"]) >= 4]
    expense = [a for a in accounts if classify_account_code(a["code"]) == EXPENSE]
    by_group: dict[str, list[dict]] = {}
    for a in expense:
        by_group.setdefault(a["code"][:2], []).append(a)
    group, pair = next((g, v) for g, v in by_group.items() if len(v) >= 2)
    other = next(a for a in expense if not a["code"].startswith(group))
    bank = next(a for a in accounts if a["code"].startswith("11"))
    return group, pair[0], pair[1], other, bank["code"]


def _spend(api, code, bank, amount, on):
    r = api.post("/transactions", json={"date": on.isoformat(), "reference": f"B-{code}-{amount}", "description": "spend",
                                        "lines": [{"account_code": code, "debit": amount, "credit": 0},
                                                  {"account_code": bank, "debit": 0, "credit": amount}]})
    assert r.status_code in (200, 201), r.text


def _budget(api, month, category, amount):
    r = api.post("/budgets", json={"month": month, "category": category, "limit_amount": amount})
    assert r.status_code == 201, r.text


def _rows(api, month):
    return {r["category"]: r for r in api.get("/budgets/actual-vs-budget", params={"month": month}).json()["rows"]}


def test_a_budget_by_code_counts_its_account_and_a_group_code_its_sub_accounts(co):
    api, _cid = co
    group, a, b, other, bank = _codes(api)
    _spend(api, a["code"], bank, 300_000, date(2026, 9, 10))
    _spend(api, b["code"], bank, 200_000, date(2026, 9, 12))
    _spend(api, other["code"], bank, 50_000, date(2026, 9, 14))
    _spend(api, a["code"], bank, 999_000, date(2026, 10, 2))            # another month
    _budget(api, "2026-09", a["code"], 250_000)
    _budget(api, "2026-09", group, 1_000_000)
    rows = _rows(api, "2026-09")
    assert rows[a["code"]]["actual_amount"] == 300_000                   # was 0
    assert rows[a["code"]]["utilization_pct"] == 120.0
    assert rows[a["code"]]["label"] == f"{a['code']} — {a['name']}"
    names = {x["code"]: x["name"] for x in api.get("/accounts").json()}
    assert rows[group]["actual_amount"] == 500_000                       # 6110 and 6112, not the other group
    assert rows[group]["label"] == (f"{group} — {names[group]}" if group in names else group)


def test_a_budget_by_name_still_counts_in_any_case_and_letterform(co):
    api, _cid = co
    _group, a, _b, _other, bank = _codes(api)
    _spend(api, a["code"], bank, 120_000, date(2026, 9, 5))
    typed = a["name"].replace("ی", "ي").replace("ک", "ك").upper() + "  "
    _budget(api, "2026-09", typed, 100_000)
    assert _rows(api, "2026-09")[typed.strip()]["actual_amount"] == 120_000
    persian = a["code"].translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    _budget(api, "2026-08", persian, 100_000)
    _spend(api, a["code"], bank, 70_000, date(2026, 8, 5))
    assert _rows(api, "2026-08")[persian]["actual_amount"] == 70_000


def test_a_jalali_month_by_code(co):
    api, _cid = co
    _group, a, _b, _other, bank = _codes(api)
    _spend(api, a["code"], bank, 400_000, date(2026, 9, 25))             # 3 Mehr 1405
    _spend(api, a["code"], bank, 900_000, date(2026, 9, 20))             # 29 Shahrivar
    _budget(api, "1405-07", a["code"], 1_000_000)
    assert _rows(api, "1405-07")[a["code"]]["actual_amount"] == 400_000


def test_an_overspent_code_budget_rings_the_bell_by_its_name(co, db):
    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications
    api, cid = co
    _group, a, _b, _other, bank = _codes(api)
    _spend(api, a["code"], bank, 300_000, date(2026, 9, 10))
    _budget(api, "2026-09", a["code"], 250_000)
    with use_company(cid):
        refresh_notifications(db, today=date(2026, 9, 15))
        n = db.execute(select(Notification).where(Notification.dedupe_key == f"budget-2026-09-{a['code']}")).scalars().one()
    assert n.level == "high" and n.title == f"Budget exceeded: {a['code']} — {a['name']}"
    assert n.params["category"] == f"{a['code']} — {a['name']}"
    assert n.params["spent"] == "120" and n.message.endswith("(120%)")    # not "120.0"

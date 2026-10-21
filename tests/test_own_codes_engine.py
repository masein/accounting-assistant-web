"""A company's own account codes: renumbered, the same figures (scenarios AN1, AN2; docs/PLAN_OWN_CODES.md).

The app reads what an account is from its code's digits. A school that keeps its own codes stores the
app's code for what each account is in ``accounts.class_code``, and every rule reads that. So here a year of
every kind of entry is booked in an Iranian company, every report is taken, every account is renumbered to
a code whose digits say the wrong thing (an asset's 1110 becomes 81110: «an expense»), its class kept, and
every report is taken again: the figures must not move."""
from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from sqlalchemy import select

YEAR = 2025          # its own company: no other test books there


_MADE: list[str] = []


@pytest.fixture(autouse=True)
def _purge_made(db):
    """Each company made here goes again: the shared test database's unscoped queries would find its accounts."""
    yield
    from tests.test_admin_audit import _purge_company
    db.rollback()
    while _MADE:
        _purge_company(db, _MADE.pop())


def _company(db, locale: str = "ir"):
    from app.services.company_service import provision_company
    co, _owner = provision_company(db, name=f"Own codes {uuid.uuid4().hex[:6]}", locale=locale,
                                   base_currency="IRR" if locale == "ir" else "GBP",
                                   username=f"oc-{uuid.uuid4().hex[:6]}", password="Str0ng-own-codes!")
    _MADE.append(str(co.id))
    return str(co.id)


def _in(cid):
    from app.db.tenant import use_company
    return use_company(cid)


def _post(db, on, desc, dr, cr, amount):
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ledger_posting import create_transaction_from_payload
    create_transaction_from_payload(db, TransactionCreate(date=on, description=desc, lines=[
        TransactionLineCreate(account_code=dr, debit=amount, credit=0),
        TransactionLineCreate(account_code=cr, debit=0, credit=amount)]))


IR = dict(capital="3110", petty="1110", fixed="1210", accdep="1219", prepay="1150", debtors="1112", sales="4110",
          vat_out="2130", wages="6110", wages_payable="2180", supplies="6112", creditors="2110", deprec="6120",
          charges="6210", vat_in="1130", travel="6130")
UK = dict(capital="3000", petty="1220", fixed="0020", accdep="0021", prepay="1300", debtors="1100", sales="4000",
          vat_out="2200", wages="7100", wages_payable="2250", supplies="7600", creditors="2100", deprec="8500",
          charges="8000", vat_in="1400", travel="7500")


def _year(db, bank: str, k: dict) -> None:
    M = 1_000_000
    _post(db, date(YEAR, 1, 2), "سرمایه اولیه", bank, k["capital"], 2_000 * M)
    _post(db, date(YEAR, 1, 3), "صندوق", k["petty"], bank, 50 * M)
    _post(db, date(YEAR, 1, 10), "خرید اموال", k["fixed"], bank, 600 * M)
    _post(db, date(YEAR, 1, 12), "پیش‌پرداخت اجاره", k["prepay"], bank, 120 * M)
    for m in range(1, 13):
        _post(db, date(YEAR, m, 5), "فروش", k["debtors"], k["sales"], (300 + 10 * m) * M)
        _post(db, date(YEAR, m, 6), "مالیات فروش", k["debtors"], k["vat_out"], 27 * M)
        _post(db, date(YEAR, m, 20), "وصول", bank, k["debtors"], (280 + 10 * m) * M)
        _post(db, date(YEAR, m, 25), "حقوق", k["wages"], k["wages_payable"], 90 * M)
        _post(db, date(YEAR, m, 28), "پرداخت حقوق", k["wages_payable"], bank, 90 * M)
        _post(db, date(YEAR, m, 8), "خرید ملزومات", k["supplies"], k["creditors"], 30 * M)
        if m % 2 == 0:
            _post(db, date(YEAR, m, 15), "پرداخت به فروشنده", k["creditors"], bank, 55 * M)
        _post(db, date(YEAR, m, 27), "استهلاک", k["deprec"], k["accdep"], 10 * M)
        _post(db, date(YEAR, m, 26), "کارمزد", k["charges"], bank, 1 * M)
    _post(db, date(YEAR, 6, 30), "مالیات خرید", k["vat_in"], bank, 8 * M)
    _post(db, date(YEAR, 9, 1), "سفر", k["travel"], k["petty"], 12 * M)


def _reports(db, locale: str = "ir") -> dict:
    from app.services.cfo_intelligence import build_cfo_report
    from app.services.reporting import financial_statement_service as fs
    span = dict(from_date=date(YEAR, 1, 1), to_date=date(YEAR, 12, 31))
    out = {
        "income": fs.build_income_statement(db, **span),
        "balance": fs.build_balance_sheet(db, to_date=date(YEAR, 12, 31)),
        "cash_flow": fs.build_cash_flow_statement(db, **span),
    }
    if locale == "ir":
        from app.services.reporting import iran_statement_service as ir
        out.update(ir_income=ir.build_iran_income_statement(db, **span),
                   ir_balance=ir.build_iran_balance_sheet(db, as_of=date(YEAR, 12, 31)),
                   ir_cash_flow=ir.build_iran_cash_flow(db, **span))
    else:
        from app.services.reporting import uk_statement_service as uk
        for name in ("build_uk_income_statement", "build_uk_balance_sheet", "build_uk_cash_flow", "build_uk_changes_in_equity"):
            fn = getattr(uk, name, None)
            if fn is not None:
                kw = dict(as_of=date(YEAR, 12, 31)) if "balance" in name else span
                out[name] = fn(db, **kw)
    out = {k: v.model_dump(mode="json") for k, v in out.items()}
    import asyncio
    import dataclasses
    from app.api.manager_reports import trial_balance
    from app.api.reports import get_owner_dashboard, invalidate_dashboard_cache
    from app.core.money import json_dumps
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput
    from app.services.ai_accountant.read_tools import GetFinancialStatement, GetFinancialStatementInput
    from app.services.ai_accountant.spending_tools import GetSpendingSummary, GetSpendingSummaryInput
    from app.services.insight_service import compute_insights, invalidate_insights_cache

    def plain_json(value):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif dataclasses.is_dataclass(value):
            value = dataclasses.asdict(value)
        elif isinstance(value, list):
            value = [plain_json(v) for v in value]
        return json.loads(json_dumps(value, fallback=str))
    out["cfo"] = plain_json(build_cfo_report(db))
    out["trial_balance"] = plain_json(trial_balance(from_date=date(YEAR, 1, 1), to_date=date(YEAR, 12, 31), page=1,
                                                    page_size=500, format="json", currency=None, db=db))
    invalidate_dashboard_cache()
    out["dashboard"] = plain_json(get_owner_dashboard(currency=None, db=db, months_back=12))
    invalidate_insights_cache()
    out["insights"] = plain_json(compute_insights(db, today=date(YEAR, 12, 31), use_cache=False))
    ctx = ToolContext(db=db, user_id="own-codes")
    out["cash_tool"] = plain_json(asyncio.run(GetCashPosition().run(ctx, GetCashPositionInput())))
    for st in ("balance_sheet", "income_statement", "trial_balance", "cash_flow"):
        out[f"tool_{st}"] = plain_json(asyncio.run(GetFinancialStatement().run(ctx, GetFinancialStatementInput(
            statement=st, from_date=date(YEAR, 1, 1), to_date=date(YEAR, 12, 31)))))
    out["spending_tool"] = plain_json(asyncio.run(GetSpendingSummary().run(ctx, GetSpendingSummaryInput(
        period="custom", from_date=date(YEAR, 1, 1), to_date=date(YEAR, 12, 31)))))
    return out


def _renumber(db) -> dict[str, str]:
    """Every account a code whose digits mislead, its class kept: 1110 → 81110 («an expense»)."""
    from app.models.account import Account
    from app.models.entity import Entity
    moved: dict[str, str] = {}
    for acc in db.execute(select(Account)).scalars().all():
        new = "8" + acc.code
        moved[new] = acc.code
        acc.class_code, acc.code = acc.code, new
    for e in db.execute(select(Entity).where(Entity.code.isnot(None))).scalars().all():
        e.code = "8" + e.code.strip()
    db.commit()
    return moved


def _as_before(value, moved: dict[str, str]):
    """The second run's output with each new code put back, and each code inside words (a line's label) too."""
    if isinstance(value, dict):
        return {k: _as_before(v, moved) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_before(v, moved) for v in value]
    if isinstance(value, str):
        if value in moved:
            return moved[value]
        import re
        for new, old in moved.items():
            if new in value:
                value = re.sub(rf"(?<![0-9A-Za-z]){re.escape(new)}(?![0-9A-Za-z])", old, value)
        return value
    return value


def _differences(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _differences(a.get(k), b.get(k), f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items → {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _differences(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {json.dumps(a, ensure_ascii=False)[:80]} → {json.dumps(b, ensure_ascii=False)[:80]}"]


def test_an1_a_chart_made_here_has_no_classes(db):
    from app.models.account import Account
    from app.services.chart_codes import class_of, own_codes_in_use
    cid = _company(db)
    with _in(cid):
        assert db.execute(select(Account.code).where(Account.class_code.isnot(None))).first() is None
        assert class_of("1110") == "1110" and own_codes_in_use() is False


@pytest.mark.parametrize("locale, codes", [("ir", IR), ("uk", UK)])
def test_an2_renumbered_with_their_classes_kept_the_figures_do_not_move(db, locale, codes):
    from app.services.ai_accountant.entity_create import _resolve_bank_account
    from app.models.entity import Entity
    from app.services.chart_codes import class_of
    cid = _company(db, locale)
    with _in(cid):
        bank, _ = _resolve_bank_account(db, "بانک ملت" if locale == "ir" else "Barclays", None, None)
        db.add(Entity(type="bank", name="بانک ملت" if locale == "ir" else "Barclays", code=bank))
        db.commit()
        _year(db, bank, codes)
        db.commit()
        before = _reports(db, locale)
        moved = _renumber(db)
        assert class_of("8" + codes["sales"]) == codes["sales"]
        after = _as_before(_reports(db, locale), moved)
    diffs = _differences(before, after)
    assert diffs == [], "\n".join(diffs[:40])


def test_an2_the_pages_drill_down_by_class(db):
    """A chart's drill-down asks for «41,42,43» (revenue) and the CFO's lists for asset accounts: by what
    each account is, so a company's own codes still answer (docs/PLAN_OWN_CODES.md phase 4)."""
    from app.api.manager_reports import accounts_list
    from app.api.reports import search_transactions
    from app.services.ai_accountant.entity_create import _resolve_bank_account
    from app.models.entity import Entity
    cid = _company(db)
    with _in(cid):
        bank, _ = _resolve_bank_account(db, "بانک ملت", None, None)
        db.add(Entity(type="bank", name="بانک ملت", code=bank))
        db.commit()
        _year(db, bank, IR)
        db.commit()
        kw = dict(account_code=None, month=None, from_date=date(YEAR, 1, 1), to_date=date(YEAR, 12, 31), search=None,
                  entity_name=None, currency=None, page=1, page_size=200, sort_by="date", sort_dir="desc", db=db)
        before = search_transactions(account_code_prefix="41,42,43", **kw)
        moved = _renumber(db)
        after = search_transactions(account_code_prefix="41,42,43", **kw)
        assert before.total_count == after.total_count == 12 and before.total_credit == after.total_credit
        assert all(moved.get(r.account_code, "").startswith("41") for r in after.rows)
        listed = {a["code"]: a["class_code"] for a in accounts_list(db=db)}
        assert listed["81110"] == "1110"                                       # the pages read the class

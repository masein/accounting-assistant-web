"""Fixed-asset register (roadmap 2026-09 §4.3): Jalali and Gregorian months,
straight-line and declining-balance schedules (the Iranian 5 % rule, residual
values, depreciation brought over), the month-end run (once per month, closed
periods caught up), acquisition and disposal postings, the register, the
routes and their roles."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import jdatetime
import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from app.models.fixed_asset import FixedAsset, FixedAssetDepreciation
from app.models.transaction import Transaction, TransactionLine
from app.services import fixed_assets as fa

J = lambda y, m, d: jdatetime.date(y, m, d).togregorian()          # noqa: E731


# --- calendar ------------------------------------------------------------------------------------------

def test_jalali_months():
    mehr = J(1405, 7, 1)
    assert mehr == date(2026, 9, 23)
    assert fa.month_start(date(2026, 10, 5), "jalali") == mehr
    assert fa.next_month(mehr, "jalali") == J(1405, 8, 1)
    assert fa.month_end(mehr, "jalali") == J(1405, 8, 1) - timedelta(days=1)
    assert fa.next_month(J(1405, 12, 1), "jalali") == J(1406, 1, 1)
    assert fa.month_label(mehr, "jalali") == "1405/07"
    assert fa.months_between(J(1405, 1, 1), J(1406, 1, 1), "jalali") == 12


def test_gregorian_months():
    assert fa.month_start(date(2026, 2, 17), "gregorian") == date(2026, 2, 1)
    assert fa.next_month(date(2026, 12, 1), "gregorian") == date(2027, 1, 1)
    assert fa.month_end(date(2028, 2, 1), "gregorian") == date(2028, 2, 29)
    assert fa.month_label(date(2026, 9, 1), "gregorian") == "2026-09"


def test_iran_starts_the_month_after_use():
    assert fa.default_start(J(1405, 7, 10), "ir") == J(1405, 8, 1)
    assert fa.default_start(J(1405, 7, 1), "ir") == J(1405, 8, 1)          # even on the 1st
    assert fa.default_start(date(2026, 10, 2), "uk") == date(2026, 10, 1)


# --- schedules (pure) ---------------------------------------------------------------------------------------

@dataclass
class A:
    cost: int
    method: str = "straight_line"
    life_months: int | None = None
    rate_bps: int | None = None
    residual: int = 0
    depreciation_start: date = date(2026, 1, 1)
    opening_accumulated: int = 0
    opening_date: date | None = None
    disposed_on: date | None = None


def _amounts(asset, cal="gregorian"):
    return [m.amount for m in fa.schedule(asset, cal)]


def test_straight_line_is_exact():
    s = fa.schedule(A(cost=1_000_000, residual=100_000, life_months=36), "gregorian")
    assert len(s) == 36 and {m.amount for m in s} == {25_000} and s[-1].closing_nbv == 100_000
    assert s[0].period_start == date(2026, 1, 1) and s[-1].period_start == date(2028, 12, 1)
    assert _amounts(A(cost=1_000, life_months=3)) == [333, 333, 334]
    assert sum(_amounts(A(cost=999_999_999, life_months=7))) == 999_999_999


def test_straight_line_in_jalali_months():
    s = fa.schedule(A(cost=1_200_000, life_months=12, depreciation_start=J(1405, 1, 1)), "jalali")
    assert [m.period_start for m in s][:3] == [J(1405, 1, 1), J(1405, 2, 1), J(1405, 3, 1)]
    assert s[-1].period_start == J(1405, 12, 1) and sum(m.amount for m in s) == 1_200_000


def test_depreciation_brought_over():
    s = fa.schedule(A(cost=1_200_000, life_months=12, opening_accumulated=600_000,
                      opening_date=date(2026, 6, 30)), "gregorian")
    assert [m.period_start.month for m in s] == [7, 8, 9, 10, 11, 12] and {m.amount for m in s} == {100_000}
    # brought over as of mid-month: that month is still covered by the next posting
    s = fa.schedule(A(cost=1_200_000, life_months=12, opening_accumulated=500_000,
                      opening_date=date(2026, 6, 15)), "gregorian")
    assert s[0].period_start == date(2026, 6, 1) and sum(m.amount for m in s) == 700_000


def test_disposal_stops_the_schedule():
    s = fa.schedule(A(cost=1_200_000, life_months=12, disposed_on=date(2026, 4, 15)), "gregorian")
    assert [m.period_start.month for m in s] == [1, 2, 3]


def test_declining_balance_and_the_five_percent_rule():
    s = fa.schedule(A(cost=1_000_000, method="declining_balance", rate_bps=2500), "gregorian")
    years = [sum(m.amount for m in s[i:i + 12]) for i in range(0, len(s), 12)]
    assert years == [250_000, 187_500, 140_625, 105_469, 79_102, 59_326, 44_495, 33_371, 25_028, 18_771,
                     14_078, 42_235]                       # the last: below 5 % of cost → all of it
    assert len(s) == 144 and s[-1].closing_nbv == 0
    assert {m.amount for m in s[:12]} == {20_833, 20_834} and sum(m.amount for m in s[:12]) == 250_000


def test_declining_balance_stops_at_the_residual():
    s = fa.schedule(A(cost=1_000_000, residual=100_000, method="declining_balance", rate_bps=2500), "gregorian")
    assert sum(m.amount for m in s) == 900_000 and s[-1].closing_nbv == 100_000
    assert all(m.closing_nbv >= 100_000 for m in s)


def test_declining_balance_with_depreciation_brought_over_mid_year():
    s = fa.schedule(A(cost=1_000_000, method="declining_balance", rate_bps=2400, opening_accumulated=100_000,
                      opening_date=date(2026, 6, 30)), "gregorian")
    first_year = s[:6]                                          # Jul–Dec of asset year 1, pro rata on 900k
    assert sum(m.amount for m in first_year) == 900_000 * 24 // 100 * 6 // 12
    assert s[6].period_start == date(2027, 1, 1)


def test_the_statutory_iranian_presets():
    got = {c.key: (c.method, c.life_months, c.statutory) for c in fa.categories("ir")}
    assert got["building_concrete"] == ("straight_line", 300, True)
    assert got["building_other"] == ("straight_line", 180, True)
    assert got["vehicle"] == ("straight_line", 72, True)
    assert got["vehicle_hire"] == ("straight_line", 48, True)
    assert got["computer"] == ("straight_line", 36, True)
    assert got["furniture"] == ("straight_line", 60, True)
    assert got["other"] == ("straight_line", None, False)
    assert not any(c.statutory for c in fa.categories("uk"))


def test_gain_and_loss_accounts_land_on_the_statements():
    from app.services.reporting.iran_statement_service import _bucket_for_code
    from app.services.reporting.uk_statement_service import _pl_bucket_for_code
    assert _bucket_for_code("4310") == "other_operating_income"
    assert _bucket_for_code("6220") == "other_operating_expenses"
    assert _pl_bucket_for_code("4200") == "other_operating_income"
    assert _pl_bucket_for_code("7860") == "admin_expenses"


# --- the books -------------------------------------------------------------------------------------------------

def _company(db, client, locale):
    c = Company(id=uuid.uuid4(), name=f"Assets {locale}", slug=f"fa-{uuid.uuid4().hex[:8]}", locale=locale,
                base_currency="IRR" if locale == "ir" else "GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale=locale)
        sup = Entity(id=uuid.uuid4(), name="Pars Machinery", type="supplier", company_id=c.id)
        db.add(sup)
        db.commit()

    def login(role="owner"):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   company_id=str(c.id), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    return {"company": c, "login": login, "supplier": sup, "cal": "jalali" if locale == "ir" else "gregorian"}


@pytest.fixture()
def ir(db, client):
    co = _company(db, client, "ir")
    yield co
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(co["company"].id))


@pytest.fixture()
def uk(db, client):
    co = _company(db, client, "uk")
    yield co
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(co["company"].id))


def _months_ago(co, n):
    """First day of the month ``n`` months before the current one (company calendar)."""
    cal = co["cal"]
    start = fa.month_start(date.today(), cal)
    for _ in range(n):
        start = fa.month_start(start - timedelta(days=1), cal)
    return start


def _asset(db, co, **kw):
    data = {"name": "CNC lathe", "category": "other", "cost": 3_600_000, "life_months": 36,
            "acquired_on": _months_ago(co, 5), "depreciation_start": _months_ago(co, 5)} | kw
    with use_company(co["company"].id):
        a = fa.create_asset(db, data)
        db.commit()
    return a


def _run(db, co, **kw):
    with use_company(co["company"].id):
        out = fa.run(db, **kw)
        db.commit()
    return out


def _lines(db, co, txn_id):
    with use_company(co["company"].id):
        return sorted((code, int(d), int(c)) for code, d, c in db.execute(
            select(Account.code, TransactionLine.debit, TransactionLine.credit)
            .join(TransactionLine, TransactionLine.account_id == Account.id)
            .where(TransactionLine.transaction_id == uuid.UUID(str(txn_id)))))


def test_a_run_posts_each_ended_month_once(db, ir):
    a = _asset(db, ir)
    assert a.number == "FA-0001" and a.asset_account_code == "1210" and a.expense_account_code == "6120"
    preview = _run(db, ir, preview=True)
    assert preview["months"] == 5 and all(j["transaction_id"] is None for j in preview["journals"])
    with use_company(ir["company"].id):
        assert db.execute(select(FixedAssetDepreciation)).scalars().all() == []
    out = _run(db, ir)
    assert [j["amount"] for j in out["journals"]] == [100_000] * 5 and out["totals"] == {"IRR": 500_000}
    first = out["journals"][0]
    assert first["label"] == fa.month_label(_months_ago(ir, 5), "jalali")
    assert first["date"] == fa.month_end(_months_ago(ir, 5), "jalali").isoformat()
    assert _lines(db, ir, first["transaction_id"]) == [("1219", 0, 100_000), ("6120", 100_000, 0)]
    with use_company(ir["company"].id):
        t = db.get(Transaction, uuid.UUID(first["transaction_id"]))
        assert t.reference == f"DEP-{first['label']}" and t.currency == "IRR"
    assert _run(db, ir)["journals"] == []                                   # never twice


def test_one_journal_per_month_and_currency(db, ir):
    _asset(db, ir)
    _asset(db, ir, name="Forklift", cost=1_200_000, life_months=12)
    _asset(db, ir, name="Imported press", cost=36_000, life_months=36, currency="USD")
    out = _run(db, ir, through=fa.month_end(_months_ago(ir, 5), "jalali"))
    assert [(j["currency"], j["amount"], j["assets"]) for j in out["journals"]] == \
        [("IRR", 200_000, 2), ("USD", 1_000, 1)]
    assert len(_lines(db, ir, out["journals"][0]["transaction_id"])) == 4


def test_a_closed_month_is_caught_up_on_the_first_open_day(db, ir):
    from app.services.period_service import set_closed_period
    _asset(db, ir)
    closed = fa.month_end(_months_ago(ir, 3), "jalali")
    with use_company(ir["company"].id):
        set_closed_period(db, closed)
        db.commit()
    try:
        out = _run(db, ir)
        catch_up = out["journals"][0]
        assert catch_up["date"] == (closed + timedelta(days=1)).isoformat() and catch_up["amount"] == 300_000
        assert len(catch_up["months"]) == 3
        assert [j["amount"] for j in out["journals"][1:]] == [100_000, 100_000]
    finally:
        with use_company(ir["company"].id):
            set_closed_period(db, None)
            db.commit()


def test_a_future_through_date_is_today(db, ir):
    _asset(db, ir)
    out = _run(db, ir, through=date.today() + timedelta(days=400), preview=True)
    assert out["through"] == date.today().isoformat() and out["months"] == 5


def test_acquisition_postings(db, ir):
    paid = _asset(db, ir, acquisition="bank")
    with use_company(ir["company"].id):
        t = db.get(Transaction, paid.acquisition_transaction_id)
        assert t.date == paid.acquired_on and t.reference == "FA-0001-ACQ"
    assert _lines(db, ir, paid.acquisition_transaction_id) == [("1110", 0, 3_600_000), ("1210", 3_600_000, 0)]
    credit = _asset(db, ir, acquisition="payable", entity_id=ir["supplier"].id)
    assert credit.number == "FA-0002"
    assert _lines(db, ir, credit.acquisition_transaction_id) == [("1210", 3_600_000, 0), ("2110", 0, 3_600_000)]
    with use_company(ir["company"].id):
        link = db.execute(select(TransactionEntity).where(
            TransactionEntity.transaction_id == credit.acquisition_transaction_id)).scalar_one()
        assert (link.entity_id, link.role) == (ir["supplier"].id, "supplier")


def test_disposal_at_a_gain(db, ir):
    a = _asset(db, ir, cost=1_200_000, life_months=12, depreciation_start=_months_ago(ir, 6),
               acquired_on=_months_ago(ir, 6))
    today = date.today()
    with use_company(ir["company"].id):
        preview = fa.dispose(db, a, on=today, proceeds=800_000, preview=True)
        assert db.execute(select(FixedAssetDepreciation)).scalars().all() == []
        out = fa.dispose(db, a, on=today, proceeds=800_000)
        db.commit()
    assert preview["gain"] == out["gain"] == 200_000 and out["accumulated"] == 600_000
    assert len(out["depreciation_posted_first"]) == 6
    assert _lines(db, ir, out["transaction_id"]) == [("1110", 800_000, 0), ("1210", 0, 1_200_000),
                                                     ("1219", 600_000, 0), ("4310", 0, 200_000)]
    with use_company(ir["company"].id):
        assert db.execute(select(Account.name).where(Account.code == "4310")).scalar_one() == \
            "سود حاصل از فروش دارایی‌های ثابت"
        row = fa.register(db)["assets"][0]
    assert (row["status"], row["net_book_value"], row["disposal_proceeds"]) == ("disposed", 0, 800_000)
    assert _run(db, ir)["journals"] == []                       # nothing more for a disposed asset


def test_scrapping_is_a_loss(db, ir):
    a = _asset(db, ir, cost=1_200_000, life_months=12, depreciation_start=_months_ago(ir, 2),
               acquired_on=_months_ago(ir, 2))
    with use_company(ir["company"].id):
        out = fa.dispose(db, a, on=date.today(), proceeds=0)
        db.commit()
    assert out["loss"] == 1_000_000
    assert _lines(db, ir, out["transaction_id"]) == [("1210", 0, 1_200_000), ("1219", 200_000, 0),
                                                     ("6220", 1_000_000, 0)]


def test_disposal_rules(db, ir):
    a = _asset(db, ir)
    with use_company(ir["company"].id):
        with pytest.raises(Exception) as e:
            fa.dispose(db, a, on=date.today() + timedelta(days=1))
        assert e.value.status_code == 422
        with pytest.raises(Exception) as e:
            fa.dispose(db, a, on=a.acquired_on - timedelta(days=1))
        assert e.value.status_code == 422
        with pytest.raises(Exception) as e:
            fa.dispose(db, a, on=date.today(), proceeds=5, bank_account_code="9999")
        assert e.value.status_code == 422
        assert db.execute(select(FixedAssetDepreciation)).scalars().all() == []   # nothing posted first
        fa.dispose(db, a, on=date.today(), proceeds=5)
        db.commit()
        with pytest.raises(Exception) as e:
            fa.dispose(db, a, on=date.today())
        assert e.value.status_code == 409


def test_uk_categories_accounts_and_months(db, uk):
    van = _asset(db, uk, name="Van", category="motor_vehicle", life_months=None, cost=2_400_000,
                 depreciation_start=None, acquired_on=date.today() - timedelta(days=70))
    assert (van.method, van.rate_bps, van.life_months) == ("declining_balance", 2500, None)
    assert (van.asset_account_code, van.accumulated_account_code, van.expense_account_code) == ("0030", "0031", "8500")
    assert van.currency == "GBP" and van.depreciation_start == fa.month_start(van.in_service_on, "gregorian")
    out = _run(db, uk)
    assert all(j["label"].count("-") == 1 for j in out["journals"])          # 2026-07 style labels
    assert all(j["amount"] == 50_000 for j in out["journals"])               # 2.4M × 25 % / 12


# --- HTTP -----------------------------------------------------------------------------------------------------------

def _body(co, **kw):
    return {"name": "Office desks", "category": "furniture", "cost": 6_000_000,
            "acquired_on": (_months_ago(co, 3) + timedelta(days=2)).isoformat()} | kw


def test_routes_register_and_run(db, ir):
    owner = ir["login"]("owner")
    cats = owner.get("/fixed-assets/categories").json()
    assert cats["calendar"] == "jalali" and cats["start_rule"] == "next_month"
    assert {c["key"] for c in cats["categories"]} >= {"vehicle", "furniture", "other"}
    r = owner.post("/fixed-assets", json=_body(ir))
    assert r.status_code == 201, r.text
    desk = r.json()
    assert (desk["method"], desk["life_months"], desk["depreciation_start"]) == \
        ("straight_line", 60, _months_ago(ir, 2).isoformat())                # the month after use
    assert len(desk["schedule"]) == 60 and desk["schedule"][0]["amount"] == 100_000 and not desk["has_postings"]
    reg = owner.get("/fixed-assets").json()
    assert reg["due"] == {"months": 2, "amount": {"IRR": 200_000}, "oldest": fa.month_label(_months_ago(ir, 2), "jalali")}
    assert reg["totals"]["IRR"] == {"cost": 6_000_000, "accumulated": 0, "net_book_value": 6_000_000, "count": 1}
    assert owner.get("/fixed-assets/depreciation-run").json()["months"] == 2
    run = owner.post("/fixed-assets/depreciation-run", json={}).json()
    assert run["totals"] == {"IRR": 200_000}
    reg = owner.get("/fixed-assets").json()
    assert reg["assets"][0]["accumulated"] == 200_000 and reg["due"]["months"] == 0
    assert reg["by_category"] == [{"currency": "IRR", "category": "furniture", "cost": 6_000_000,
                                   "accumulated": 200_000, "net_book_value": 5_800_000, "count": 1}]
    got = owner.get(f"/fixed-assets/{desk['id']}").json()
    assert [m["posted"] for m in got["schedule"][:3]] == [True, True, False]


def test_create_validation(db, ir):
    owner = ir["login"]("owner")
    for body, fragment in [
        (_body(ir, category="other"), "life of 1–1200 months"),
        (_body(ir, residual=6_000_000), "residual value below it"),
        (_body(ir, asset_account_code="9999"), "Account not found: 9999"),
        (_body(ir, in_service_on="2020-01-01"), "before it was acquired"),
        (_body(ir, opening_accumulated=10), "brought-over depreciation"),
        (_body(ir, method="declining_balance"), "yearly rate"),
    ]:
        r = owner.post("/fixed-assets", json=body)
        assert r.status_code == 422 and fragment in r.text, (body, r.text)
    assert owner.post("/fixed-assets", json=_body(ir, cost=0)).status_code == 422
    assert owner.post("/fixed-assets", json=_body(ir, acquisition="gift")).status_code == 422
    with use_company(ir["company"].id):
        assert db.execute(select(FixedAsset)).scalars().all() == []


def test_edit_and_delete_rules(db, ir):
    owner = ir["login"]("owner")
    a = owner.post("/fixed-assets", json=_body(ir)).json()
    r = owner.patch(f"/fixed-assets/{a['id']}", json={"life_months": 120, "location": "HQ"})
    assert r.status_code == 200 and (r.json()["life_months"], r.json()["location"]) == (120, "HQ")
    assert owner.patch(f"/fixed-assets/{a['id']}", json={"method": "declining_balance"}).status_code == 422
    owner.post("/fixed-assets/depreciation-run", json={})
    r = owner.patch(f"/fixed-assets/{a['id']}", json={"life_months": 60})
    assert r.status_code == 409 and "can't change" in r.text
    assert owner.patch(f"/fixed-assets/{a['id']}", json={"name": "Desks, 2nd floor"}).json()["name"] == "Desks, 2nd floor"
    assert owner.delete(f"/fixed-assets/{a['id']}").status_code == 409
    fresh = owner.post("/fixed-assets", json=_body(ir, acquired_on=date.today().isoformat())).json()
    assert owner.delete(f"/fixed-assets/{fresh['id']}").status_code == 204
    assert owner.get(f"/fixed-assets/{fresh['id']}").status_code == 404


def test_dispose_over_http(db, ir):
    owner = ir["login"]("owner")
    a = owner.post("/fixed-assets", json=_body(ir)).json()
    body = {"on": date.today().isoformat(), "proceeds": 7_000_000}
    prev = owner.post(f"/fixed-assets/{a['id']}/dispose/preview", json=body).json()
    assert prev["transaction_id"] is None and prev["gain"] == 1_200_000
    r = owner.post(f"/fixed-assets/{a['id']}/dispose", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["gain"] == 1_200_000 and r.json()["asset"]["status"] == "disposed"
    assert owner.post(f"/fixed-assets/{a['id']}/dispose", json=body).status_code == 409


def test_roles(db, ir):
    a = ir["login"]("owner").post("/fixed-assets", json=_body(ir)).json()
    viewer = ir["login"]("viewer")
    assert viewer.get("/fixed-assets").status_code == 200
    assert viewer.get(f"/fixed-assets/{a['id']}").status_code == 200
    assert viewer.post("/fixed-assets", json=_body(ir)).status_code == 403
    assert viewer.post("/fixed-assets/depreciation-run", json={}).status_code == 403
    assert viewer.post(f"/fixed-assets/{a['id']}/dispose", json={"on": date.today().isoformat()}).status_code == 403
    assert ir["login"]("accountant").post("/fixed-assets/depreciation-run", json={}).status_code == 200
    employee = ir["login"]("employee")
    assert employee.get("/fixed-assets").status_code == 403


def test_another_companys_assets_are_invisible(db, ir, uk):
    theirs = uk["login"]("owner").post("/fixed-assets", json=_body(uk, category="computer")).json()
    owner = ir["login"]("owner")
    assert owner.get(f"/fixed-assets/{theirs['id']}").status_code == 404
    assert owner.get("/fixed-assets").json()["assets"] == []
    assert owner.post(f"/fixed-assets/{theirs['id']}/dispose", json={"on": date.today().isoformat()}).status_code == 404
    assert owner.post("/fixed-assets/depreciation-run", json={}).json()["journals"] == []


# --- AI tool and page -------------------------------------------------------------------------------------------

def test_the_ai_tool_reads_the_register(db, ir):
    import asyncio

    from app.services.ai_accountant.asset_tools import GetFixedAssets, GetFixedAssetsInput
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.orchestrator import build_default_registry, build_personal_registry
    tool = GetFixedAssets()
    with use_company(ir["company"].id):
        ctx = ToolContext(db=db, user_id="u", username="owner")
        empty = asyncio.run(tool.run(ctx, GetFixedAssetsInput()))
    assert empty["count"] == 0 and empty["note"] == "No fixed assets are registered yet."
    _asset(db, ir)
    with use_company(ir["company"].id):
        out = asyncio.run(tool.run(ctx, GetFixedAssetsInput()))
    assert out["count"] == 1 and out["assets"][0]["number"] == "FA-0001"
    assert out["due"]["months"] == 5 and "not posted yet" in out["note"]
    assert set(out["assets"][0]) >= {"cost", "accumulated", "net_book_value", "monthly_charge"}
    assert "get_fixed_assets" in {t.name for t in build_default_registry()}
    assert "get_fixed_assets" not in {t.name for t in build_personal_registry()}


def test_the_page_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    core = (root / "js" / "01-core.js").read_text(encoding="utf-8")
    ops = (root / "js" / "12-ops.js").read_text(encoding="utf-8")
    assert 'data-page="fixed-assets"' in html and html.count('data-page="fixed-assets"') == 2   # nav + card
    assert "'fixed-assets': ['owner', 'cfo', 'accountant']" in core and "'fixed-assets']);" in core
    assert "if (page === 'fixed-assets') { loadFixedAssets(); }" in ops
    block = ops.split("// ═══════ Fixed-asset register", 1)[1]
    assert "confirm(" not in block.replace("uiConfirm(", "")                  # the in-app dialog only
    assert "onclick" not in block

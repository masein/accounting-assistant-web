"""Where new entries post after a migration (scenarios O7, O8, 2026-10-12).

After a real company migrated from Sepidar, its balances sat in its own
sub-ledgers (receivables 111201, salaries payable 211101, petty cash
111003) while every posting still resolved to the seeded chart's fixed codes
(1112, 2180, 1160): the first invoice or pay run opened a second set of
balances beside the migrated ones, and the debtors and creditors report
showed no one. A company of its own (suggestions take a sub-ledger only when
it is the one that fits, and other tests' charts share the test database)."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account, AccountLevel
from app.models.company import Company
from app.services import posting_roles
from app.services.account_resolver import find_account_code, resolve_account_code
from tests.test_migration_import import HEADER_4, HEADER_TAFSILI, _ss_xml, _upload


@pytest.fixture()
def co(db, client):
    c = Company(id=uuid.uuid4(), name="Sepidar Roles Co", slug=f"sr-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True,
                               company_id=str(c.id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    from tests.conftest import _CSRFTestClient
    yield {"company": c, "api": _CSRFTestClient(client, csrf)}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def _chart():
    kol = _ss_xml([
        HEADER_4,
        ["1110", "موجودي نقد و بانك", "0", "0", "550.0000", "0.0000"],
        ["1112", "حسابها و اسناد دريافتني تجاري", "0", "0", "4200000.0000", "0.0000"],
        ["2111", "ساير حسابهاي پرداختني", "0", "0", "0.0000", "3800550.0000"],
        ["2112", "پيش دريافتها", "0", "0", "0.0000", "400000.0000"],
    ])
    moein = _ss_xml([
        HEADER_4,
        ["111003", "تنخواه گردانهاي ريالي", "0", "0", "50.0000", "0.0000"],
        ["111005", "موجودي بانكهاي ريالي", "0", "0", "500.0000", "0.0000"],
        ["111201", "حسابهاي دريافتني تجاري", "0", "0", "4200000.0000", "0.0000"],
        ["211101", "حقوق و دستمزد پرداختني", "0", "0", "0.0000", "3000000.0000"],
        ["211123", "حق بيمه پرداختني كاركنان", "0", "0", "0.0000", "500000.0000"],
        ["211128", "ماليات حقوق پرداختني كاركنان", "0", "0", "0.0000", "300000.0000"],
        ["211130", "ماليات ارزش افزوده پرداختي", "0", "0", "0.0000", "550.0000"],
        ["211201", "پيش دريافت فروش خدمات", "0", "0", "0.0000", "400000.0000"],
        ["411003", "فروش خدمات", "0", "0", "0.0000", "0.0000"],
    ])
    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["001", "بانك نمونه نقش 0101234567009", "حساب بانكي", "0", "0", "500.0000", "0.0000"],
        ["20001", "مشتري نمونه نقش", "طرف مقابل", "0", "0", "4200000.0000", "0.0000"],
        ["10001", "كارمند نمونه نقش", "طرف مقابل", "0", "0", "0.0000", "3000000.0000"],
    ])
    return [("كل.xls", kol), ("معين.xls", moein), ("تفصيلي.xls", tafsili)]


def _import(api):
    r = api.post("/migration/import/preview", files=_upload(_chart()))
    assert r.status_code == 200, r.text
    c = api.post("/migration/import/confirm", json={"token": r.json()["token"], "opening": "replace",
                                                   "opening_date": date.today().isoformat()})
    assert c.status_code == 200, c.text
    return c.json()["result"]


def test_o7_new_entries_post_to_the_imported_accounts(co, db):
    api = co["api"]
    result = _import(api)
    assert result["posting_roles"] == {
        "bank": "111005", "petty_cash": "111003", "ar": "111201", "customer_credit": "211201",
        "vat_output": "211130", "vat_input": "211130", "net_pay_payable": "211101",
        "paye_payable": "211128", "social_security_payable": "211123", "revenue": "411003",
    }
    with use_company(co["company"].id):
        db.expire_all()
        assert resolve_account_code(db, "ar") == "111201"
        assert resolve_account_code(db, "net_pay_payable") == "211101"
        assert find_account_code(db, "petty_cash") == "111003"
        assert resolve_account_code(db, "ap") == "2110"          # nothing fits: the chart's default, for the owner to pick

    shown = {r["category"]: r for r in api.get("/accounts/posting-roles").json()}
    assert shown["ar"]["code"] == "111201" and shown["ar"]["source"] == "migration" and shown["ar"]["default_code"] == "1112"
    assert shown["ap"]["code"] == "2110" and shown["ap"]["source"] is None

    # A bank added later goes under the bank sub-ledger, not on the next free 111x.
    b = api.post("/entities", json={"type": "bank", "name": "بانک تازه نقش"})
    assert b.status_code in (200, 201), b.text
    with use_company(co["company"].id):
        acc = db.execute(select(Account).where(Account.code == b.json()["code"])).scalars().one()
        assert acc.code.startswith("111005") and len(acc.code) == 9
        assert acc.level == AccountLevel.DETAIL and acc.parent.code == "111005"

    # The owner's choice stands, and a later import doesn't undo it.
    r = api.put("/accounts/posting-roles", json={"roles": {"ap": "211130", "ar": None}})
    assert r.status_code == 200, r.text
    shown = {x["category"]: x for x in r.json()}
    assert shown["ap"]["code"] == "211130" and shown["ap"]["source"] == "owner"
    assert shown["ar"]["code"] == "1112" and shown["ar"]["source"] == "owner"     # back on the chart's default
    _import(api)
    shown = {x["category"]: x for x in api.get("/accounts/posting-roles").json()}
    assert shown["ap"]["code"] == "211130" and shown["ap"]["source"] == "owner"
    assert shown["ar"]["code"] == "1112"                                           # not suggested again
    assert api.put("/accounts/posting-roles", json={"roles": {"nope": "1110"}}).status_code == 422
    assert api.put("/accounts/posting-roles", json={"roles": {"ap": "999999"}}).status_code == 422


def test_o8_debtors_and_creditors_show_each_partys_migrated_balance(co):
    api = co["api"]
    _import(api)
    today = date.today().isoformat()
    body = api.get(f"/manager-reports/operational/debtor-creditor?from_date={today}&to_date={today}").json()
    debtors = {r["entity_name"]: r["total"] for r in body["debtors"]}
    creditors = {r["entity_name"]: r["total"] for r in body["creditors"]}
    assert debtors == {"مشتری نمونه نقش": 4200000}
    assert creditors == {"کارمند نمونه نقش": 3000000}


def test_the_seeded_chart_posts_where_it_always_did(co, db):
    with use_company(co["company"].id):
        assert posting_roles.suggest(db) == {}
        assert resolve_account_code(db, "ar") == "1112" and resolve_account_code(db, "net_pay_payable") == "2180"

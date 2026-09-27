"""Chart-of-accounts management (roadmap 2026-09 §4.5): adding accounts under
a parent (codes and levels follow it), renaming, deactivating and deleting
with their guards, the tree with rolled-up balances, opening balances, and
the roles."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.company import Company
from app.models.transaction import Transaction, include_deleted_transactions
from app.schemas.transaction import TransactionCreate, TransactionLineCreate


@pytest.fixture()
def co(db, client):
    c = Company(id=uuid.uuid4(), name="Chart Co", slug=f"coa-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = c.id
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()

    def login(role="owner"):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   company_id=str(cid), role=role)
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        from tests.conftest import _CSRFTestClient
        return _CSRFTestClient(client, csrf)
    yield {"cid": cid, "login": login}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(cid))


def _post(db, co, lines, on=None, ref="J"):
    from app.services.ledger_posting import create_transaction_from_payload
    with use_company(co["cid"]):
        t = create_transaction_from_payload(db, TransactionCreate(
            date=on or date.today() - timedelta(days=3), reference=ref, currency="IRR",
            lines=[TransactionLineCreate(account_code=c, debit=d, credit=cr) for c, d, cr in lines]))
        db.commit()
        return t.id


def _id(db, co, code):
    with use_company(co["cid"]):
        return str(db.execute(select(Account.id).where(Account.code == code)).scalar_one())


# --- adding --------------------------------------------------------------------------------------------

def test_a_child_takes_its_code_and_level_from_the_parent(db, co):
    owner = co["login"]()
    assert owner.get("/accounts/suggest-code/1110").json()["code"] == "111001"
    r = owner.post("/accounts", json={"name": "بانک ملت جاری", "parent_code": "1110"})
    assert r.status_code == 201, r.text
    first = r.json()
    assert (first["code"], first["level"], first["is_active"]) == ("111001", "SUB", True)
    second = owner.post("/accounts", json={"name": "بانک ملی", "parent_code": "1110"}).json()
    assert second["code"] == "111002"
    detail = owner.post("/accounts", json={"name": "شعبه ونک", "parent_code": "111001"}).json()
    assert (detail["code"], detail["level"]) == ("11100101", "DETAIL")
    general = owner.post("/accounts", json={"name": "Loans to staff", "parent_code": "11", "code": "1170"}).json()
    assert (general["code"], general["level"]) == ("1170", "GENERAL")
    group = owner.post("/accounts", json={"name": "Long-term debt", "code": "22"}).json()
    assert (group["level"], group["parent_id"]) == ("GROUP", None)


@pytest.mark.parametrize("body, status, fragment", [
    ({"name": "x", "parent_code": "1110", "code": "2110"}, 422, "starts with it"),
    ({"name": "x", "parent_code": "1110", "code": "1110"}, 422, "starts with it"),
    ({"name": "x", "parent_code": "11", "code": "1110"}, 409, "already exists"),
    ({"name": "x", "parent_code": "1110", "code": "11x1"}, 422, "digits only"),
    ({"name": "x", "parent_code": "9876"}, 404, "Parent account not found"),
    ({"name": "x"}, 422, "needs a code"),
    ({"name": "   ", "parent_code": "1110"}, 422, "needs a name"),
])
def test_adding_is_checked(db, co, body, status, fragment):
    r = co["login"]().post("/accounts", json=body)
    assert r.status_code == status and fragment in r.text, r.text


def test_a_detail_account_has_no_children(db, co):
    owner = co["login"]()
    sub = owner.post("/accounts", json={"name": "Sub", "parent_code": "1110"}).json()
    detail = owner.post("/accounts", json={"name": "Detail", "parent_code": sub["code"]}).json()
    r = owner.post("/accounts", json={"name": "Deeper", "parent_code": detail["code"]})
    assert r.status_code == 422 and "can't have children" in r.text


# --- renaming, deactivating, deleting -----------------------------------------------------------------------

def test_rename_and_detail_type(db, co):
    owner = co["login"]()
    acc = owner.post("/accounts", json={"name": "Old", "parent_code": "1110"}).json()
    r = owner.patch(f"/accounts/{acc['id']}", json={"name": "New name", "detail_type": "bank"})
    assert (r.json()["name"], r.json()["detail_type"]) == ("New name", "bank")
    assert owner.patch(f"/accounts/{acc['id']}", json={"detail_type": None}).json()["detail_type"] is None


def test_deactivating_keeps_history_and_stops_postings(db, co):
    owner = co["login"]()
    acc = owner.post("/accounts", json={"name": "Old bank", "parent_code": "1110"}).json()
    _post(db, co, [(acc["code"], 500, 0), ("4110", 0, 500)])
    r = owner.patch(f"/accounts/{acc['id']}", json={"is_active": False})
    assert r.status_code == 409 and "still has a balance of 500" in r.text
    _post(db, co, [("1110", 500, 0), (acc["code"], 0, 500)])                   # moved out: zero balance
    r = owner.patch(f"/accounts/{acc['id']}", json={"is_active": False, "name": "Old bank (closed)"})
    assert r.status_code == 200 and (r.json()["is_active"], r.json()["name"]) == (False, "Old bank (closed)")

    assert acc["code"] not in {a["code"] for a in owner.get("/accounts?limit=500").json()}
    assert acc["code"] in {a["code"] for a in owner.get("/accounts?limit=500&include_inactive=true").json()}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _post(db, co, [(acc["code"], 1, 0), ("4110", 0, 1)])
    assert e.value.status_code == 422 and "inactive" in e.value.detail
    db.rollback()
    import asyncio

    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    with use_company(co["cid"]):
        found = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u"), SearchAccountsInput(query="Old bank")))
    assert acc["code"] not in {m["code"] for m in found["matches"]}
    assert owner.patch(f"/accounts/{acc['id']}", json={"is_active": True}).json()["is_active"] is True


def test_deactivation_guards(db, co):
    owner = co["login"]()
    r = owner.patch(f"/accounts/{_id(db, co, '1110')}", json={"is_active": False})
    assert r.status_code == 409 and "automatic postings" in r.text                # the bank default
    parent = owner.post("/accounts", json={"name": "P", "parent_code": "1110"}).json()
    child = owner.post("/accounts", json={"name": "C", "parent_code": parent["code"]}).json()
    r = owner.patch(f"/accounts/{parent['id']}", json={"is_active": False, "name": "renamed?"})
    assert r.status_code == 409 and child["code"] in r.text
    assert owner.get(f"/accounts/{parent['id']}").json()["name"] == "P"          # nothing changed on the refusal
    owner.patch(f"/accounts/{child['id']}", json={"is_active": False})
    owner.patch(f"/accounts/{parent['id']}", json={"is_active": False})
    r = owner.patch(f"/accounts/{child['id']}", json={"is_active": True})
    assert r.status_code == 422 and "parent" in r.text
    r = owner.post("/accounts", json={"name": "under an inactive", "parent_code": parent["code"]})
    assert r.status_code == 422


def test_delete_only_what_was_never_used(db, co):
    owner = co["login"]()
    assert owner.delete(f"/accounts/{_id(db, co, '1112')}").status_code == 409   # receivables default
    parent = owner.post("/accounts", json={"name": "P", "parent_code": "1110"}).json()
    child = owner.post("/accounts", json={"name": "C", "parent_code": parent["code"]}).json()
    assert owner.delete(f"/accounts/{parent['id']}").status_code == 409          # has a child
    tid = _post(db, co, [(child["code"], 10, 0), ("4110", 0, 10)])
    with use_company(co["cid"]):                                                  # undo it…
        from datetime import datetime, timezone
        db.get(Transaction, tid).deleted_at = datetime.now(timezone.utc)
        db.commit()
    r = owner.delete(f"/accounts/{child['id']}")
    assert r.status_code == 409 and "deactivate it instead" in r.text             # …the entry still exists
    unused = owner.post("/accounts", json={"name": "Typo", "parent_code": "1110"}).json()
    assert owner.delete(f"/accounts/{unused['id']}").status_code == 204
    assert owner.get(f"/accounts/{unused['id']}").status_code == 404


def test_the_tree_rolls_balances_up(db, co):
    owner = co["login"]()
    a = owner.post("/accounts", json={"name": "A", "parent_code": "1110"}).json()
    b = owner.post("/accounts", json={"name": "B", "parent_code": "1110"}).json()
    _post(db, co, [(a["code"], 300, 0), (b["code"], 200, 0), ("4110", 0, 500)])
    owner.patch(f"/accounts/{b['id']}", json={"name": "B"})
    roots = owner.get("/accounts/tree").json()["accounts"]
    group = next(r for r in roots if r["code"] == "11")
    cash = next(ch for ch in group["children"] if ch["code"] == "1110")
    assert (cash["balance"], cash["total"]) == (0, 500) and cash["protected"]
    assert {ch["code"]: ch["total"] for ch in cash["children"]} == {a["code"]: 300, b["code"]: 200}
    assert group["total"] == 500 and group["level"] == "GROUP"
    revenue = next(r for r in roots if r["code"] == "41")
    assert revenue["total"] == -500


def test_the_tree_can_leave_out_inactive_accounts(db, co):
    owner = co["login"]()
    a = owner.post("/accounts", json={"name": "Dormant", "parent_code": "1110"}).json()
    owner.patch(f"/accounts/{a['id']}", json={"is_active": False})

    def codes(nodes):
        return {n["code"] for n in nodes} | {c for n in nodes for c in codes(n["children"])}
    assert a["code"] in codes(owner.get("/accounts/tree").json()["accounts"])
    assert a["code"] not in codes(owner.get("/accounts/tree?include_inactive=false").json()["accounts"])


# --- opening balances --------------------------------------------------------------------------------------------

def _opening(owner, lines, on=None):
    return owner.put("/accounts/opening-balances", json={
        "on": (on or date.today() - timedelta(days=30)).isoformat(),
        "lines": [{"account_code": c, "debit": d, "credit": cr} for c, d, cr in lines]})


def test_opening_balances_post_one_journal_and_replace_it(db, co):
    owner = co["login"]()
    empty = owner.get("/accounts/opening-balances").json()
    assert empty["lines"] == [] and empty["transaction_id"] is None and empty["date"]
    r = _opening(owner, [("1110", 1_000_000, 0), ("1112", 400_000, 0), ("2110", 0, 300_000), ("3110", 0, 1_100_000)])
    assert r.status_code == 200, r.text
    assert (r.json()["adjustment"], r.json()["lines"]) == (0, 4)
    got = owner.get("/accounts/opening-balances").json()
    assert got["source"] == "manual" and {ln["account_code"]: (ln["debit"], ln["credit"]) for ln in got["lines"]} == \
        {"1110": (1_000_000, 0), "1112": (400_000, 0), "2110": (0, 300_000), "3110": (0, 1_100_000)}
    # saving again replaces it — one live opening journal, and an imbalance goes to 3999
    r = _opening(owner, [("1110", 900_000, 0), ("3110", 0, 800_000)])
    assert (r.json()["adjustment"], r.json()["lines"]) == (100_000, 3)
    with use_company(co["cid"]):
        live = db.execute(select(Transaction).where(Transaction.reference == "OPENING-BALANCES")).scalars().all()
        assert len(live) == 1
        with include_deleted_transactions():
            assert len(db.execute(select(Transaction).where(Transaction.reference == "OPENING-BALANCES")).scalars().all()) == 2
        assert db.execute(select(Account.name).where(Account.code == "3999")).scalar_one() == "تعدیلات افتتاحیه"
    lines = {ln["account_code"]: (ln["debit"], ln["credit"]) for ln in owner.get("/accounts/opening-balances").json()["lines"]}
    assert lines["3999"] == (0, 100_000)
    assert _opening(owner, []).json()["transaction_id"] is None                     # clearing them
    assert owner.get("/accounts/opening-balances").json()["lines"] == []


def test_opening_balances_replace_the_migration_opening(db, co):
    from app.services.migration_import import OPENING_REFERENCE as MIGRATION
    _post(db, co, [("1110", 5, 0), ("3110", 0, 5)], ref=MIGRATION)
    owner = co["login"]()
    assert owner.get("/accounts/opening-balances").json()["source"] == "migration"
    _opening(owner, [("1110", 7, 0), ("3110", 0, 7)])
    with use_company(co["cid"]):
        assert db.execute(select(Transaction).where(Transaction.reference == MIGRATION)).scalars().all() == []


@pytest.mark.parametrize("lines, fragment", [
    ([("11", 5, 0)], "is a group"),
    ([("9876", 5, 0)], "Account not found"),
    ([("1110", 5, 5)], "not both"),
])
def test_opening_balance_lines_are_checked(db, co, lines, fragment):
    owner = co["login"]()
    _opening(owner, [("1110", 10, 0), ("3110", 0, 10)])
    r = _opening(owner, lines)
    assert r.status_code == 422 and fragment in r.text
    assert len(owner.get("/accounts/opening-balances").json()["lines"]) == 2       # the old one stands


def test_opening_balances_respect_dates_and_the_closed_period(db, co):
    owner = co["login"]()
    _opening(owner, [("1110", 10, 0), ("3110", 0, 10)])
    assert _opening(owner, [("1110", 1, 0)], on=date.today() + timedelta(days=2)).status_code == 422
    lock = (date.today() - timedelta(days=10)).isoformat()
    assert owner.put("/admin/closed-period", json={"closed_period": lock}).status_code == 200
    try:
        r = _opening(owner, [("1110", 99, 0), ("3110", 0, 99)], on=date.today() - timedelta(days=20))
        assert r.status_code == 422
        assert {ln["debit"] for ln in owner.get("/accounts/opening-balances").json()["lines"]} == {10, 0}
    finally:
        owner.put("/admin/closed-period", json={"closed_period": None})


def test_an_inactive_account_takes_no_opening_balance(db, co):
    owner = co["login"]()
    a = owner.post("/accounts", json={"name": "Dormant", "parent_code": "1110"}).json()
    owner.patch(f"/accounts/{a['id']}", json={"is_active": False})
    r = _opening(owner, [(a["code"], 5, 0)])
    assert r.status_code == 422 and "inactive" in r.text


# --- roles and tenancy ------------------------------------------------------------------------------------------------

def test_roles_and_tenancy(db, co):
    viewer = co["login"]("viewer")
    assert viewer.get("/accounts/tree").status_code == 200
    assert viewer.get("/accounts/opening-balances").status_code == 200
    assert viewer.post("/accounts", json={"name": "x", "parent_code": "1110"}).status_code == 403
    assert viewer.put("/accounts/opening-balances", json={"on": date.today().isoformat(), "lines": []}).status_code == 403
    assert co["login"]("accountant").post("/accounts", json={"name": "ok", "parent_code": "1110"}).status_code == 201
    assert co["login"]("employee").get("/accounts/tree").status_code == 403
    oid = uuid.uuid4()
    db.add(Company(id=oid, name="Other", slug=f"oth-{uuid.uuid4().hex[:8]}", locale="ir", base_currency="IRR",
                   status="active", token_version=0))
    db.commit()
    try:
        with use_company(oid):
            seed_chart_if_empty(db, locale="ir")
            db.commit()
            theirs = str(db.execute(select(Account.id).where(Account.code == "6112")).scalar_one())
        db.expunge_all()
        owner = co["login"]("owner")
        assert owner.patch(f"/accounts/{theirs}", json={"name": "hijack"}).status_code == 404
        assert owner.delete(f"/accounts/{theirs}").status_code == 404
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(oid))


def test_the_page_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    core = (root / "js" / "01-core.js").read_text(encoding="utf-8")
    ops = (root / "js" / "12-ops.js").read_text(encoding="utf-8")
    assert html.count('data-page="accounts"') == 2
    import re
    valid = re.search(r"const validPages = new Set\(\[([^\]]*)\]\)", core).group(1)
    assert "accounts: ['owner', 'cfo', 'accountant']" in core and "'accounts'" in valid
    assert "if (page === 'accounts') { loadChartOfAccounts(); }" in ops
    block = ops.split("// ═══════ Chart of accounts", 1)[1]
    assert "onclick" not in block and "confirm(" not in block.replace("uiConfirm(", "")


def test_the_accounts_postings_really_use_are_protected_whatever_the_locale(db, client):
    """Seen in the step-3 screenshots: a UK-locale company on the Iranian chart
    offered Deactivate/Delete on 1110, which its bank postings resolve to."""
    from app.services.chart_service import protected_codes
    cid = uuid.uuid4()
    db.add(Company(id=cid, name="Mixed", slug=f"mix-{uuid.uuid4().hex[:8]}", locale="uk", base_currency="GBP",
                   status="active", token_version=0))
    db.commit()
    try:
        with use_company(cid):
            seed_chart_if_empty(db, locale="ir")
            db.commit()
            codes = protected_codes(db)
        assert {"1110", "1112", "2110", "1219", "6120"} <= codes and "1200" not in codes
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(cid))


def test_checkboxes_are_not_styled_as_text_fields():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    rule = css.split('input[type="checkbox"], input[type="radio"] {', 1)[1].split("}", 1)[0]
    assert "width: auto" in rule and "height: auto" in rule and "margin: 0" in rule
    assert css.index('input[type="checkbox"], input[type="radio"] {') > css.index("input, textarea, select {")

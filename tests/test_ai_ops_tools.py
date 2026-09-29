"""The last modules the chat couldn't reach (roadmap 2026-09 §5.1): purchase
orders, recurring rules, the cap table, exchange rates, petty cash and the
audit trail, and the confirm-gated period lock and recurring rule."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.services.ai_accountant import guardrails
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.ops_tools import (
    GetAuditTrail, GetAuditTrailInput, GetCapTable, GetExchangeRates, GetExchangeRatesInput, GetPettyCash,
    ListPurchaseOrders, ListPurchaseOrdersInput, ListRecurringRules, ListRecurringRulesInput, NoInput,
    ProposeCreateRecurringRule, ProposeCreateRecurringRuleInput, ProposeLockPeriod, ProposeLockPeriodInput,
)
from tests.test_payroll_lifecycle_http import co  # noqa: F401 — fixture


@pytest.fixture()
def books(co, db):
    from app.db.seed import seed_chart_if_empty
    session, cid = co
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    return session(), cid


def _user(db, cid, role):
    from app.core.auth import hash_password
    from app.models.user import User
    ph, salt = hash_password("x")
    u = User(username=f"{role}-{uuid.uuid4().hex[:6]}", password_hash=ph, password_salt=salt, role=role,
             is_admin=role == "owner", is_active=True, company_id=uuid.UUID(cid))
    db.add(u)
    db.commit()
    return str(u.id), u.username


def _tool(cid, db, tool, args, user_id="u1"):
    with use_company(cid):
        return asyncio.run(tool.run(ToolContext(db=db, user_id=user_id, username="tester", user_message="test"), args))


def _execute(cid, db, card, user_id="u1"):
    with use_company(cid):
        res = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id=user_id,
                               actor_username="tester")
        db.commit()
        return res


# --- reads -------------------------------------------------------------------------------------------------------

def test_purchase_orders(books, db):
    api, cid = books
    delta = api.post("/entities", json={"type": "supplier", "name": "Delta Supplies"}).json()
    other = api.post("/entities", json={"type": "supplier", "name": "Other Co"}).json()
    for sup, status, lines in ((delta, "issued", [("Paper", 10, 50_000)]), (delta, "cancelled", [("Ink", 1, 9)]),
                               (other, "issued", [("Desk", 2, 1_000_000)])):
        r = api.post("/purchase-orders", json={"entity_id": sup["id"], "order_date": "2026-09-01", "status": status,
                                               "lines": [{"description": d, "ordered_qty": q, "unit_price": p}
                                                         for d, q, p in lines]})
        assert r.status_code == 201, r.text
    out = _tool(cid, db, ListPurchaseOrders(), ListPurchaseOrdersInput(supplier="delta", open_only=True))
    (po,) = out["purchase_orders"]
    assert (po["supplier_name"], po["status"], po["total"]) == ("Delta Supplies", "issued", 500_000)
    assert po["lines"] == [{"description": "Paper", "ordered_qty": 10.0, "received_qty": 0.0, "billed_qty": 0.0,
                            "unit_price": 50_000}]
    assert _tool(cid, db, ListPurchaseOrders(), ListPurchaseOrdersInput())["count"] == 3
    assert _tool(cid, db, ListPurchaseOrders(), ListPurchaseOrdersInput(status="cancelled"))["count"] == 1
    with pytest.raises(ToolError):
        _tool(cid, db, ListPurchaseOrders(), ListPurchaseOrdersInput(supplier="Nobody"))


def test_cap_table_rates_and_petty_cash(books, db):
    api, cid = books
    ali = api.post("/entities", json={"type": "shareholder", "name": "Ali Rezaei"}).json()
    assert api.post("/equity/shareholdings", json={"entity_id": ali["id"], "percent": 60, "shares": 600}).status_code == 201
    cap = _tool(cid, db, GetCapTable(), NoInput())
    (row,) = cap["rows"]
    assert (row["entity_name"], row["percent"], row["shares"]) == ("Ali Rezaei", 60.0, 600)

    for when, rate in (("2026-09-01", 1_000_000), ("2026-09-20", 1_050_000)):
        r = api.post("/fx/rates", json={"from_currency": "USD", "to_currency": "IRR", "rate": rate, "effective_date": when})
        assert r.status_code == 201, r.text
    fx = _tool(cid, db, GetExchangeRates(), GetExchangeRatesInput(currency="usd"))
    assert fx["base_currency"] == "IRR"
    assert [(x["from"], x["to"], x["rate"], x["date"]) for x in fx["rates"]] == [("USD", "IRR", 1_050_000.0, "2026-09-20")]
    assert fx["waiting_for_a_rate"]["count"] == 0

    holder_id, holder = _user(db, cid, "employee")
    r = api.post("/petty-cash/accounts", json={"username": holder})
    assert r.status_code == 201, r.text
    assert api.post(f"/petty-cash/accounts/{r.json()['id']}/deposit", json={"amount": 5_000_000, "bank_account_code": "1110"}).status_code in (200, 201)
    pc = _tool(cid, db, GetPettyCash(), NoInput())
    assert pc["total_balance"] == 5_000_000 and pc["accounts"][0]["balance"] == 5_000_000


def test_the_audit_trail(books, db):
    api, cid = books
    owner_id, owner = _user(db, cid, "owner")
    card = _tool(cid, db, ProposeCreateRecurringRule(), ProposeCreateRecurringRuleInput(
        name="Office rent", amount=80_000_000, start_date=date.today() + timedelta(days=3), account_code="6112"),
        user_id=owner_id)
    _execute(cid, db, card, user_id=owner_id)
    out = _tool(cid, db, GetAuditTrail(), GetAuditTrailInput(entity_type="recurring_rule"))
    (ev,) = out["events"]
    assert (ev["action"], ev["what"], ev["source"]) == ("create", "recurring_rule", "ai-assistant")
    assert "Office rent" in ev["detail"]
    assert _tool(cid, db, GetAuditTrail(), GetAuditTrailInput(entity_type="recurring_rule", username="nobody"))["count"] == 0


# --- proposals ---------------------------------------------------------------------------------------------------

def test_a_recurring_rule_from_the_chat(books, db):
    api, cid = books
    landlord = api.post("/entities", json={"type": "supplier", "name": "Landlord"}).json()
    start = date.today() + timedelta(days=5)
    card = _tool(cid, db, ProposeCreateRecurringRule(), ProposeCreateRecurringRuleInput(
        name="Office rent", amount=80_000_000, start_date=start, account_code="6112", party="landlord"))
    assert card["preview"]["bank_account_code"] and card["preview"]["party"] == "Landlord"
    assert "Pay 80,000,000 monthly" in card["summary"]
    with use_company(cid):
        assert guardrails.proposal_amount(db, "propose_create_recurring_rule", card["preview"]) is None
    _execute(cid, db, card)
    rules = _tool(cid, db, ListRecurringRules(), ListRecurringRulesInput())["rules"]
    (rule,) = rules
    assert (rule["name"], rule["frequency"], rule["amount"], rule["next_run_date"], rule["counter_account_code"]) == (
        "Office rent", "monthly", 80_000_000, start.isoformat(), "6112")
    for kw, code in (({"frequency": "fortnightly"}, "bad_frequency"), ({"direction": "sideways"}, "bad_direction"),
                     ({"account_code": "9999"}, "account_not_found"), ({"party": "Nobody"}, "entity_not_found"),
                     ({"end_date": start - timedelta(days=1)}, "bad_dates")):
        with pytest.raises(ToolError) as e:
            _tool(cid, db, ProposeCreateRecurringRule(), ProposeCreateRecurringRuleInput(**{
                "name": "x", "amount": 1, "start_date": start, "account_code": "6112", **kw}))
        assert e.value.code == code, kw


def test_only_the_owner_locks_the_books(books, db):
    from fastapi import HTTPException

    from app.db.tenant import tenant_bypass
    from app.models.user import User
    from app.services.period_service import get_closed_period
    _api, cid = books
    owner_id, _ = _user(db, cid, "owner")
    acct_id, _ = _user(db, cid, "accountant")
    through = date.today().replace(day=1) - timedelta(days=1)                       # last month's end

    def set_role(role):
        with tenant_bypass():
            db.execute(select(User).where(User.id == uuid.UUID(owner_id))).scalar_one().role = role
            db.commit()

    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposeLockPeriod(), ProposeLockPeriodInput(through=through), user_id=acct_id)
    assert e.value.code == "not_allowed"
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposeLockPeriod(), ProposeLockPeriodInput(through=date.today()), user_id=owner_id)
    assert e.value.code == "lock_in_future"
    # the role is checked again at Confirm: an owner demoted in between can't lock
    card = _tool(cid, db, ProposeLockPeriod(), ProposeLockPeriodInput(through=through), user_id=owner_id)
    set_role("accountant")
    with pytest.raises(HTTPException) as e:
        _execute(cid, db, card, user_id=owner_id)
    assert e.value.status_code == 403
    set_role("owner")
    card = _tool(cid, db, ProposeLockPeriod(), ProposeLockPeriodInput(through=through), user_id=owner_id)
    assert "nothing is locked yet" in card["summary"]
    with use_company(cid):
        assert guardrails.proposal_amount(db, "propose_lock_period", card["preview"]) is None
    _execute(cid, db, card, user_id=owner_id)
    with use_company(cid):
        assert get_closed_period(db) == through
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposeLockPeriod(), ProposeLockPeriodInput(through=through), user_id=owner_id)
    assert e.value.code == "already_locked"


def test_the_tools_are_registered_and_prompted():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT, build_default_registry, build_personal_registry
    names = {"list_purchase_orders", "list_recurring_rules", "get_cap_table", "get_exchange_rates", "get_petty_cash",
             "get_audit_trail", "propose_lock_period", "propose_create_recurring_rule"}
    assert names <= {t.name for t in build_default_registry()}
    assert not names & {t.name for t in build_personal_registry()}
    for n in names:
        assert f"``{n}``" in SYSTEM_PROMPT, n

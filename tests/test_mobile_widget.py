"""The home-screen widget's numbers (scenario N33; roadmap
ROADMAP_ANDROID_CHAT P3.7)."""
from __future__ import annotations

from app.db.tenant import use_company
from app.services.ai_accountant import guardrails  # noqa: F401
from tests.test_ai_guardrails import D, _login, co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def test_n33_cash_and_the_budget_left(client, db, co, phone):
    web = _login(client, co["cid"], co["owner"])
    for body in (
        {"date": D, "description": "Capital", "lines": [{"account_code": "1110", "debit": 50_000_000, "credit": 0},
                                                         {"account_code": "3110", "debit": 0, "credit": 50_000_000}]},
        {"date": D, "description": "Taxi", "lines": [{"account_code": "6130", "debit": 400_000, "credit": 0},
                                                      {"account_code": "1110", "debit": 0, "credit": 400_000}]},
    ):
        assert web.post("/transactions", json=body).status_code in (200, 201)
    client.cookies.clear()
    r = client.get(f"{API}/summary", headers=phone)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cash"] == {"total": 49_600_000, "currency": "IRR"} and body["as_of"]
    assert body["budget"] is None                                  # no budgets set this month


def test_n33_with_a_budget_set(client, db, co, phone):
    from app.models.budget import BudgetLimit
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.period_tools import _this_month
    with use_company(co["cid"]):
        month = _this_month(ToolContext(db=db, user_id=str(co["owner"].id)))      # in the company's calendar
        db.add(BudgetLimit(month=month, category="6130", limit_amount=10_000_000))
        db.commit()
    body = client.get(f"{API}/summary", headers=phone).json()
    assert body["budget"] == {"left": 10_000_000, "total": 10_000_000, "currency": "IRR"}

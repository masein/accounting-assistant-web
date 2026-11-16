"""Small fixes (chat review 2026-11-10, item 25, scenarios CH1–CH3). Figures made up."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.db.tenant import use_company
from tests.test_statement_export import _company


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, day, desc, dr, cr, amount):
    r = api.post("/transactions", json={"date": day.isoformat(), "description": desc, "currency": "IRR", "lines": [
        {"account_code": dr, "debit": amount, "credit": 0}, {"account_code": cr, "debit": 0, "credit": amount}]})
    assert r.status_code == 201, r.text


def test_ch1_imported_vouchers_are_not_a_recurring_payment(db, co):
    from app.services.recurring_detection import detect_recurring
    api, cid = co
    today = date.today()
    for i, months in enumerate((3, 2, 1)):
        day = today - timedelta(days=30 * months)
        _post(api, day, f"سند واردشده {101 + i}", "6112", "1110", 5_000_000)
        _post(api, day + timedelta(days=1), f"اجاره دفتر {201 + i}", "6112", "1110", 7_000_000)
    with use_company(cid):
        found = {c.description for c in detect_recurring(db, today=today)}
    assert not any("واردشده" in d for d in found)
    assert any("اجاره" in d for d in found)


def test_ch2_a_money_account_never_used_is_not_listed(db, co):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput
    api, cid = co
    bank = api.post("/entities", json={"type": "bank", "name": "بانک نمونه"}).json()["code"]
    api.post("/entities", json={"type": "bank", "name": "بانک بی‌استفاده"})
    _post(api, date.today(), "سرمایه", bank, "3110", 9_000_000)
    with use_company(cid):
        out = asyncio.run(GetCashPosition().run(ToolContext(db=db, user_id="u-ch"), GetCashPositionInput()))
    assert [a["account_code"] for a in out["accounts"]] == [bank] and out["total"] == 9_000_000


def test_ch3_tools_are_not_mentioned():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    assert 'Never mention tools, "the tool"' in SYSTEM_PROMPT

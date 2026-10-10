"""Payroll in the phone's chat (scenario N34; roadmap ROADMAP_ANDROID_CHAT
P3.3): the recent runs and an employee's pay as tables, their latest
payslip as a PDF to open or share."""
from __future__ import annotations

from app.services.ai_accountant import blocks as B
from app.services.ai_accountant.payroll_tools import GetPayroll, GetPayrollInput
from tests.test_ai_payroll_budget_tools import _tool, staff  # noqa: F401  (fixture)
from tests.test_payroll_lifecycle_http import _run, co  # noqa: F401  (fixture)
from tests.test_mobile_contract import contract_errors


def test_n34_the_runs_and_an_employees_pay_with_the_payslip(staff, db):
    api, cid, sara, _ = staff
    run = _run(api).json()
    assert api.post(f"/payroll/runs/{run['id']}/post").status_code == 200
    runs = _tool(cid, db, GetPayroll(), GetPayrollInput())
    (table,) = B.data_blocks([{"name": "get_payroll", "tool_use_id": "c1", "result": runs}], lang="fa", calendar="jalali")
    assert table["kind"] == "payroll_runs" and contract_errors(table) == []
    assert table["rows"][0]["label"] == "خرداد ۱۴۰۵" and table["rows"][0]["sub"] == "ثبت‌شده"   # Khordad's run
    assert table["rows"][0]["value"] == runs["runs"][0]["total_net"]

    mine = _tool(cid, db, GetPayroll(), GetPayrollInput(employee="sara"))
    history, payslip = B.data_blocks([{"name": "get_payroll", "tool_use_id": "c2", "result": mine}], lang="fa", calendar="jalali")
    assert history["kind"] == "pay_history" and history["title"] == "Sara Ahmadi"
    assert payslip["type"] == "file" and payslip["id"] != history["id"] and contract_errors(payslip) == []
    assert payslip["path"] == f"/api/mobile/v1/documents/payslips/{run['id']}/{sara['id']}"
    # the same PDF the web prints
    r = api.get(payslip["path"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")

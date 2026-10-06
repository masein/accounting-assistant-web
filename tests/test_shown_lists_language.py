"""The lists a page shows as they come — an invoice's Moadian problems, a bank
SMS it couldn't read, a statement's analysis, an upload's errors — read in the
page's language, dates in the company's calendar (retest 2, 2026-10-02, #53:
they reached Persian pages in English)."""
from __future__ import annotations

from datetime import date

from app.core.messages import localize_lists, said, say_all
from app.utils.jalali import format_jalali
from tests.test_moadian import _customer, _invoice, _setup, co  # noqa: F401 — the company fixture

FA = {"X-UI-Language": "fa"}


def test_an_invoices_moadian_problems_in_persian(co):
    api, _cid = co
    inv = _invoice(api, _customer(api))                        # no memory id set yet
    en = api.get(f"/moadian/invoices/{inv['id']}/preview").json()["problems"]
    fa = api.get(f"/moadian/invoices/{inv['id']}/preview", headers=FA).json()["problems"]
    assert "Enter the company's tax memory id (شناسه یکتای حافظه مالیاتی) in the مودیان settings." in en
    assert "شناسه یکتای حافظه مالیاتی شرکت را در تنظیمات مودیان وارد کنید." in fa and len(fa) == len(en)
    listed = api.get("/moadian/invoices", params={"state": "all"}, headers=FA).json()["invoices"]
    assert all(not any("Enter the" in p for p in row["problems"]) for row in listed)
    _setup(api)
    bare = _invoice(api, _customer(api, national_id=None, economic_code=None, postal_code=None))
    warnings = api.get(f"/moadian/invoices/{bare['id']}/preview", headers=FA).json()["warnings"]
    assert "مشتری شناسه ملی یا کد اقتصادی ندارد؛ این فاکتور از نوع دوم ارسال می‌شود." in warnings, warnings
    out = api.post("/moadian/export", json={"invoice_ids": [inv["id"], "00000000-0000-0000-0000-000000000000"]},
                   headers=FA).json()
    assert "فاکتور پیدا نشد." in [p for s in out["skipped"] for p in s["problems"]]


def test_an_unread_bank_sms_says_why_in_persian(co):
    api, _cid = co
    msgs = api.post("/bank-sms/preview", json={"text": "سلام، وقت بخیر"}, headers=FA).json()["messages"]
    assert msgs and "مبلغی پیدا نشد" in msgs[0]["problems"]
    msgs = api.post("/bank-sms/preview", json={"text": "hello there"}).json()["messages"]
    assert "no amount found" in msgs[0]["problems"]


def test_a_statements_analysis_warns_in_persian(co, db):
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    api, cid = co
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    expense, bank = "6112", "1110"
    r = api.post("/transactions", json={"date": date.today().isoformat(), "reference": "LOSS-1", "description": "هزینه",
                                        "lines": [{"account_code": expense, "debit": 9_000_000, "credit": 0},
                                                  {"account_code": bank, "debit": 0, "credit": 9_000_000}]})
    assert r.status_code in (200, 201), r.text
    body = api.get("/manager-reports/financial/cash-flow", headers=FA).json()
    warnings = (body.get("analysis") or {}).get("warnings") or []
    assert warnings and all("Net cash" not in w and "Negative operating" not in w for w in warnings), warnings
    assert any("وجه نقد خالص" in w or "جریان نقد عملیاتی" in w for w in warnings), warnings


def test_a_duplicate_upload_names_its_day_in_the_company_calendar(co):
    api, _cid = co
    assert api.put("/admin/display-calendar", json={"calendar": "jalali"}).status_code == 200
    csv = b"Date,Description,Debit,Credit,Balance\n2026-09-01,Opening,0,9000000,9000000\n2026-09-03,Rent,2500000,0,6500000\n"
    files = {"file": ("mellat.csv", csv, "text/csv")}
    first = api.post("/brain/bank-statements/upload", files=files, headers=FA)
    assert first.status_code == 200, first.text
    again = api.post("/brain/bank-statements/upload", files={"file": ("mellat.csv", csv, "text/csv")}, headers=FA).json()
    assert again["duplicate"] is True
    [err] = again["errors"]
    assert "وارد شده است" in err and format_jalali(date.today()) in err and "already imported" not in err, err


def test_the_helpers():
    assert say_all(["no date found", "something new", 7], "fa") == ["تاریخی پیدا نشد", "something new", 7]
    out = localize_lists({"skipped": [{"problems": ["Invoice not found."]}], "note": "no date found"}, "es")
    assert out == {"skipped": [{"problems": ["Factura no encontrada."]}], "note": "no date found"}
    assert localize_lists({"errors": ["empty message"]}, "en") == {"errors": ["empty message"]}
    # a nested reason made of several messages is said part by part
    from app.core.messages import localize_detail
    said_ = localize_detail("No transaction rows could be read from this statement. no date found; no amount found", "fa")
    assert said_.startswith("هیچ ردیف تراکنشی") and "تاریخی پیدا نشد" in said_ and "مبلغی پیدا نشد" in said_
    assert said(["empty message"], None) == ["empty message"]           # no request: as it was

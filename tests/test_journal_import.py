"""Historical journals from another system (roadmap 2026-09 §4.11): header
detection in English and Persian, the presets (QuickBooks month-first dates
and blank continuation rows, Xero journal numbers, Iranian Jalali dates and
Persian digits), amounts in every shape, voucher grouping, account matching
and remembered choices, and posting — ready vouchers only, never twice."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionLine
from app.services import journal_import as ji


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(rows) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode("utf-8")


def _parse(name, data, **kw):
    return ji.parse_file(ji.read_rows(name, data), **kw)


D1, D2 = date.today() - timedelta(days=40), date.today() - timedelta(days=20)
US = lambda d: d.strftime("%m/%d/%Y")                                                # noqa: E731


def quickbooks_export():
    return _xlsx([
        ["Acme Trading Ltd"], ["Journal"], ["All Dates"], [],
        ["", "Date", "Transaction Type", "Num", "Name", "Memo/Description", "Account", "Debit", "Credit"],
        ["", US(D1), "Journal Entry", "", "", "Owner's investment", "Checking", "10,000.00", ""],
        ["", "", "", "", "", "Owner's investment", "Owner's Equity", "", "10,000.00"],
        ["", "", "", "", "", "", "", "$10,000.00", "$10,000.00"],               # the transaction's total row
        ["", US(D2), "Expense", "", "Office Depot", "Paper", "Office Supplies", "250.50", ""],
        ["", "", "", "", "Office Depot", "Paper", "Checking", "", "250.50"],
        ["TOTAL", "", "", "", "", "", "", "$10,250.50", "$10,250.50"],
    ])


def xero_export():
    return _csv([
        ["Journal Number", "Date", "Account Code", "Account", "Description", "Reference", "Debit", "Credit"],
        ["101", D1.strftime("%d %b %Y"), "1200", "Business Bank Account", "Sale INV-7", "INV-7", "1200.00", ""],
        ["101", D1.strftime("%d %b %Y"), "4000", "Sales", "Sale INV-7", "INV-7", "", "1000.00"],
        ["101", D1.strftime("%d %b %Y"), "2200", "VAT", "Sale INV-7", "INV-7", "", "200.00"],
        ["102", D2.strftime("%d %b %Y"), "7200", "Rent", "Rent refund", "", "(900.00)", ""],  # a negative debit
        ["102", D2.strftime("%d %b %Y"), "1200", "Business Bank Account", "Rent refund", "", "", "(900.00)"],
    ])


def _jalali(d):
    import jdatetime
    j = jdatetime.date.fromgregorian(date=d)
    return f"{j.year}/{j.month:02d}/{j.day:02d}".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def sepidar_export():
    return _xlsx([
        ["گزارش دفتر روزنامه"],
        ["شماره سند", "تاریخ سند", "کد حساب", "نام حساب", "کد تفصیلی", "شرح", "بدهکار", "بستانکار"],
        ["۱۲", _jalali(D1), "1110", "موجودی نقد و بانک", "", "دریافت از مشتری", "۱۲۰٬۰۰۰٬۰۰۰", ""],
        ["", "", "1112", "حساب‌ها و اسناد دریافتنی تجاری", "شرکت آریا", "دریافت از مشتری", "", "۱۲۰٬۰۰۰٬۰۰۰"],
        ["۱۳", _jalali(D2), "6112", "سایر هزینه‌های عملیاتی", "", "خرید لوازم", "۸٬۵۰۰٬۰۰۰", ""],
        ["", "", "2110", "حساب‌ها و اسناد پرداختنی تجاری", "فروشگاه پارس", "خرید لوازم", "", "۸٬۵۰۰٬۰۰۰"],
        ["", "", "", "جمع", "", "", "۱۲۸٬۵۰۰٬۰۰۰", "۱۲۸٬۵۰۰٬۰۰۰"],
    ])


# --- values ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, value", [
    ("10,000.00", 10000), ("$10,250.50", 10250.5), ("(900.00)", -900), ("-12", -12), ("12-", -12),
    ("۱۲۰٬۰۰۰٬۰۰۰", 120_000_000), ("1.234.567,89", 1234567.89), ("1234,5", 1234.5), ("", 0), ("-", 0),
    (None, 0), ("150 CR", -150), (" 7 ", 7),
])
def test_amounts(raw, value):
    assert float(ji.parse_amount(raw)) == value


@pytest.mark.parametrize("raw, order, expected", [
    ("2026-09-27", "dmy", date(2026, 9, 27)), ("2026-09-27 00:00:00", "dmy", date(2026, 9, 27)),
    ("27/09/2026", "dmy", date(2026, 9, 27)), ("09/27/2026", "mdy", date(2026, 9, 27)),
    ("09/27/2026", "dmy", date(2026, 9, 27)),          # 27 can't be a month: swapped
    ("27 Sep 2026", "dmy", date(2026, 9, 27)), ("Sep 27, 2026", "mdy", date(2026, 9, 27)),
    ("1405/07/05", "dmy", date(2026, 9, 27)), ("۱۴۰۵/۰۷/۰۵", "dmy", date(2026, 9, 27)),
    ("46292", "dmy", date(2026, 9, 27)),               # an Excel serial
    ("31/02/2026", "dmy", None), ("soon", "dmy", None),
])
def test_dates(raw, order, expected):
    assert ji.parse_date(raw, order=order) == expected


# --- parsing ---------------------------------------------------------------------------------------------

def test_quickbooks_journal_report():
    p = _parse("journal.xlsx", quickbooks_export())
    assert (p["preset"], p["grouping"], p["header_row"]) == ("quickbooks", "block", 5)
    v1, v2 = p["vouchers"]
    assert (v1.on, v1.debit, v1.credit, len(v1.lines)) == (D1, 10000, 10000, 2)
    assert (v2.on, v2.debit, v2.credit) == (D2, 251, 251)          # 250.50 → whole units, half up
    assert [ln.account_name for ln in v1.lines] == ["Checking", "Owner's Equity"]
    assert v2.lines[0].party == "Office Depot" and v2.lines[0].description == "Paper"
    assert p["rows_without_account"] == 0                           # balanced total rows net to nothing


def test_xero_journal_report():
    p = _parse("journals.csv", xero_export())
    assert (p["preset"], p["grouping"]) == ("xero", "number")
    v1, v2 = p["vouchers"]
    assert (v1.number, v1.reference, v1.debit, len(v1.lines)) == ("101", "INV-7", 1200, 3)
    # a negative debit is a credit, and the other way round
    assert [(ln.account_code, ln.debit, ln.credit) for ln in v2.lines] == [("7200", 0, 900), ("1200", 900, 0)]


def test_sepidar_journal_in_persian():
    p = _parse("روزنامه.xlsx", sepidar_export())
    assert (p["preset"], p["grouping"], p["header_row"]) == ("sepidar", "number", 2)
    v1, v2 = p["vouchers"]
    assert (v1.number, v1.on, v1.debit) == ("12", D1, 120_000_000)
    assert v1.lines[1].party == "شرکت آریا" and v2.on == D2 and v2.lines[0].account_code == "6112"
    assert set(p["columns"]) >= {"voucher", "date", "account_code", "account_name", "party", "description",
                                 "debit", "credit"}


def test_a_signed_amount_column_and_date_grouping():
    data = _csv([["Date", "Account", "Memo", "Amount"],
                 [D1.isoformat(), "Cash", "Loan in", "5000"], [D1.isoformat(), "Bank loan", "Loan in", "-5000"],
                 [D2.isoformat(), "Rent", "", "700"], [D2.isoformat(), "Cash", "", "-700"]])
    p = _parse("x.csv", data)
    assert (p["preset"], p["grouping"], len(p["vouchers"])) == ("generic", "date", 2)
    assert [(ln.debit, ln.credit) for ln in p["vouchers"][0].lines] == [(5000, 0), (0, 5000)]


def test_the_user_can_correct_a_column():
    data = _csv([["Date", "Ledger", "Dr", "Cr", "Notes"], [D1.isoformat(), "Cash", "10", "", "x"],
                 [D1.isoformat(), "Sales", "", "10", "x"]])
    p = _parse("x.csv", data)                                       # "Ledger" isn't a name we know…
    assert p["needs"] == ["account"] and p["vouchers"] == [] and p["columns"]["debit"] == 2
    p = _parse("x.csv", data, columns={"account_name": 1, "description": 4})
    assert p["needs"] == [] and [ln.account_name for ln in p["vouchers"][0].lines] == ["Cash", "Sales"]
    p = _parse("x.csv", data, columns={"account_name": 1, "debit": None})   # a column taken away
    assert p["needs"] == ["amounts"]


def test_rounding_keeps_vouchers_balanced():
    data = _csv([["Journal Number", "Date", "Account", "Debit", "Credit"],
                 ["1", D1.isoformat(), "Cash", "10.50", ""], ["1", D1.isoformat(), "Rent", "10.50", ""],
                 ["1", D1.isoformat(), "Sales", "", "21.00"]])
    p = _parse("x.csv", data)
    v = p["vouchers"][0]
    assert v.debit == v.credit == 21 and p["rounded_lines"] == 2   # 11 + 11 ≠ 21: one gives the unit back
    assert sorted(ln.debit for ln in v.lines if ln.debit) == [10, 11]


def test_a_file_with_nothing_recognisable_opens_for_mapping():
    p = _parse("x.csv", _csv([["Name", "Phone"], ["a", "1"]]))
    assert set(p["needs"]) == {"amounts", "account", "date"} and p["headers"] == ["Name", "Phone"]
    with pytest.raises(Exception) as e:
        _parse("x.csv", b"")
    assert e.value.status_code == 422


# --- the books ------------------------------------------------------------------------------------------------

@pytest.fixture()
def co(db, client):
    cid = uuid.uuid4()
    db.add(Company(id=cid, name="Import Co", slug=f"imp-{uuid.uuid4().hex[:8]}", locale="ir", base_currency="IRR",
                   status="active", token_version=0))
    db.commit()
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


def test_accounts_match_by_code_then_name_and_the_rest_are_suggested(db, co):
    p = _parse("روزنامه.xlsx", sepidar_export())
    with use_company(co["cid"]):
        accounts = {a["code"]: a for a in ji.match_accounts(db, p["vouchers"])}
    assert all(a["how"] == "code" for a in accounts.values())
    q = _parse("journal.xlsx", quickbooks_export())
    with use_company(co["cid"]):
        accs = {a["name"]: a for a in ji.match_accounts(db, q["vouchers"])}
    assert accs["Checking"]["mapped_to"] is None and accs["Checking"]["how"] is None
    data = _csv([["Date", "Account", "Debit", "Credit"], [D1.isoformat(), "فروش", "", "5"],
                 [D1.isoformat(), "موجودی نقد و بانك", "5", ""]])      # Arabic ك, still the same name
    with use_company(co["cid"]):
        named = {a["name"]: a for a in ji.match_accounts(db, _parse("n.csv", data)["vouchers"])}
    assert (named["فروش"]["mapped_to"], named["فروش"]["how"]) == ("4110", "name")
    assert named["موجودی نقد و بانك"]["mapped_to"] == "1110"


def test_import_posts_ready_vouchers_once_and_remembers_choices(db, co):
    q = _parse("journal.xlsx", quickbooks_export())
    with use_company(co["cid"]):
        preview = ji.review(db, q)
        assert preview["counts"] == {"unmapped_account": 2} and preview["voucher_count"] == 2
        keys = {a["name"]: a["key"] for a in preview["accounts"]}
        choice = {keys["Checking"]: "1110", keys["Owner's Equity"]: "3110", keys["Office Supplies"]: "6112"}
        out = ji.apply(db, q, account_map=choice)
        db.commit()
    assert out == {"posted": 2, "skipped": {}, "voucher_count": 2}
    with use_company(co["cid"]):
        txns = db.execute(select(Transaction).where(Transaction.reference.like("IMPORT-quickbooks-%"))
                          .order_by(Transaction.date)).scalars().all()
        assert [(t.date, t.currency) for t in txns] == [(D1, "IRR"), (D2, "IRR")]
        lines = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txns[1].id)).scalars().all()
        assert sorted((ln.debit, ln.credit) for ln in lines) == [(0, 251), (251, 0)]
        party = db.execute(select(Entity).join(TransactionEntity, TransactionEntity.entity_id == Entity.id)
                           .where(TransactionEntity.transaction_id == txns[1].id)).scalars().all()
        assert [(e.name, e.type) for e in party] == [("Office Depot", "supplier")]   # from the expense line
        again = ji.review(db, _parse("journal.xlsx", quickbooks_export()))
        assert again["counts"] == {"already_imported": 2}             # mapping remembered, and never twice
        assert {a["how"] for a in again["accounts"]} == {"chosen"}


def test_problem_vouchers_are_reported_not_posted(db, co):
    from app.services.period_service import set_closed_period
    old = date.today() - timedelta(days=100)
    data = _csv([["Journal Number", "Date", "Account Code", "Account", "Debit", "Credit"],
                 ["1", old.isoformat(), "1110", "Cash", "10", ""], ["1", old.isoformat(), "4110", "Sales", "", "10"],
                 ["2", D1.isoformat(), "1110", "Cash", "10", ""], ["2", D1.isoformat(), "4110", "Sales", "", "9"],
                 ["3", (date.today() + timedelta(days=5)).isoformat(), "1110", "Cash", "1", ""],
                 ["3", (date.today() + timedelta(days=5)).isoformat(), "4110", "Sales", "", "1"],
                 ["4", D2.isoformat(), "1110", "Cash", "3", ""], ["4", D2.isoformat(), "9999", "Mystery", "", "3"],
                 ["5", D2.isoformat(), "1110", "Cash", "4", ""], ["5", D2.isoformat(), "4110", "Sales", "", "4"]])
    p = _parse("x.csv", data)
    with use_company(co["cid"]):
        set_closed_period(db, date.today() - timedelta(days=60))
        db.commit()
        try:
            rev = ji.review(db, p)
            assert rev["counts"] == {"closed_period": 1, "unbalanced": 1, "future": 1, "unmapped_account": 1, "ready": 1}
            assert [r["problem"] for r in rev["vouchers"]][-1] is None           # problems listed first
            out = ji.apply(db, p, account_map={})
            db.commit()
        finally:
            set_closed_period(db, None)
            db.commit()
    assert out["posted"] == 1 and out["skipped"] == {"closed_period": 1, "unbalanced": 1, "future": 1,
                                                      "unmapped_account": 1}


def _year(first, last, sale, *, opening_cash):
    """One fiscal year as Sepidar exports it: the opening voucher, a sale, the
    closing of the temporary accounts and the اختتامیه."""
    j1, jz = _jalali(first), _jalali(last)
    return _xlsx([
        ["گزارش دفتر روزنامه"],
        ["شماره سند", "تاریخ سند", "کد حساب", "نام حساب", "کد تفصیلی", "شرح", "بدهکار", "بستانکار"],
        ["۱", j1, "1110", "موجودی نقد و بانک", "", "سند افتتاحیه", f"{opening_cash}", ""],
        ["", "", "3110", "سرمایه", "", "سند افتتاحیه", "", f"{opening_cash}"],
        ["۲", j1, "1112", "حساب‌ها و اسناد دریافتنی تجاری", "شرکت آریا", "فروش خدمات", f"{sale}", ""],
        ["", "", "4110", "فروش", "", "فروش خدمات", "", f"{sale}"],
        ["۳", jz, "4110", "فروش", "", "بستن حساب‌های موقت", f"{sale}", ""],
        ["", "", "3300", "سود (زیان) انباشته", "", "بستن حساب‌های موقت", "", f"{sale}"],
        ["۴", jz, "3110", "سرمایه", "", "سند اختتامیه", f"{opening_cash}", ""],
        ["", "", "3300", "سود (زیان) انباشته", "", "سند اختتامیه", f"{sale}", ""],
        ["", "", "1110", "موجودی نقد و بانک", "", "سند اختتامیه", "", f"{opening_cash}"],
        ["", "", "1112", "حساب‌ها و اسناد دریافتنی تجاری", "", "سند اختتامیه", "", f"{sale}"],
    ])


def test_a_years_closing_is_left_out_and_its_opening_posts_once(db, co):
    """E4: posted as they come, a year's closing vouchers wiped it out — its
    revenue read zero and its balance sheet zeros — and the next year's
    opening voucher doubled every balance the year before had made."""
    from app.models.account import Account
    from app.services.cfo_intelligence import ledger_balance
    y1 = (date.today() - timedelta(days=700), date.today() - timedelta(days=400))
    y2 = (date.today() - timedelta(days=399), date.today() - timedelta(days=30))
    p1 = _parse("1402.xlsx", _year(*y1, 5_000_000, opening_cash=100_000_000))
    with use_company(co["cid"]):
        rev = ji.review(db, p1)
        assert rev["counts"] == {"ready": 2, "year_end_closing": 2}           # the first year's opening posts
        ji.apply(db, p1, account_map={a["key"]: a["mapped_to"] or "3300" for a in rev["accounts"]})
        db.commit()

        def bal(code):
            return ledger_balance(db, (code,), None, date.today())
        assert bal("4110") == -5_000_000                                      # the year's sales stand (it was 0)
        assert (bal("1110"), bal("1112"), bal("3110")) == (100_000_000, 5_000_000, -100_000_000)   # not wiped

        p2 = _parse("1403.xlsx", _year(*y2, 7_000_000, opening_cash=100_000_000))
        rev2 = ji.review(db, p2)
        assert rev2["counts"] == {"ready": 1, "year_end_closing": 2, "opening_repeat": 1}
        ji.apply(db, p2, account_map={a["key"]: a["mapped_to"] or "3300" for a in rev2["accounts"]})
        db.commit()
        assert bal("1110") == 100_000_000                                     # not doubled to 200,000,000
        assert (bal("4110"), bal("1112")) == (-12_000_000, 12_000_000)


def test_a_voucher_imported_before_the_date_was_in_the_reference_is_still_recognised(db, co):
    """References carry the voucher's date now (numbers restart each fiscal
    year); one imported under the old form counts as already imported on its
    own day only — the same number on another day is another voucher."""
    def one(day, amount):
        return _parse("old.csv", _csv([["Journal Number", "Date", "Account Code", "Account", "Debit", "Credit"],
                                       ["2", day.isoformat(), "1110", "Cash", str(amount), ""],
                                       ["2", day.isoformat(), "4110", "Sales", "", str(amount)]]))
    same_day, other_day = one(D1, 10), one(D2, 5)
    with use_company(co["cid"]):
        db.add(Transaction(id=uuid.uuid4(), date=D1, reference=f"IMPORT-{same_day['preset']}-2",
                           description="imported before 2026-10-07", currency="IRR"))
        db.commit()
        assert ji.review(db, same_day)["counts"] == {"already_imported": 1}
        assert ji.review(db, other_day)["counts"] == {"ready": 1}


def test_an_opening_voucher_after_the_migrated_opening_balances_is_left_out(db, co):
    """The chart migration posts opening balances (MIGRATION-OPENING), usually
    on the year's first day; the journal's own opening voucher on that same day
    would have doubled them."""
    from app.services.migration_import import OPENING_REFERENCE
    first = date.today() - timedelta(days=200)
    p = _parse("1404.xlsx", _year(first, first + timedelta(days=150), 3_000_000, opening_cash=40_000_000))
    with use_company(co["cid"]):
        db.add(Transaction(id=uuid.uuid4(), date=first, reference=OPENING_REFERENCE,
                           description="opening balances", currency="IRR"))
        db.commit()
        counts = ji.review(db, p)["counts"]
    assert counts == {"ready": 1, "year_end_closing": 2, "opening_repeat": 1}


def test_english_year_end_wording_is_recognised():
    v = ji.Voucher(key="1", number="1", on=D1, reference=None,
                   lines=[ji.Line(row=1, account_code="", account_name="", description="Closing entry FY2025", debit=1, credit=0)])
    assert ji._year_end_kind(v) == "closing"
    v.lines[0].description = "Opening balances brought forward"
    assert ji._year_end_kind(v) == "opening"
    v.lines[0].description = "Office supplies"
    assert ji._year_end_kind(v) is None


# --- HTTP ---------------------------------------------------------------------------------------------------------

def test_the_routes(db, co):
    owner = co["login"]("owner")
    r = owner.post("/migration/journals/preview", files={"file": ("journals.csv", xero_export(), "text/csv")},
                   data={"preset": "auto"})
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["preset"] == "xero" and prev["voucher_count"] == 2 and "fields" in prev
    keys = {a["code"]: a["key"] for a in prev["accounts"]}
    mapping = {keys["1200"]: "1110", keys["4000"]: "4110", keys["2200"]: "2130", keys["7200"]: "6112"}
    rev = owner.post("/migration/journals/review", json={"token": prev["token"], "account_map": mapping}).json()
    assert rev["counts"] == {"ready": 2}
    r = owner.post("/migration/journals/apply", json={"token": prev["token"], "account_map": mapping})
    assert r.status_code == 200 and r.json()["posted"] == 2
    assert owner.post("/migration/journals/apply", json={"token": prev["token"]}).status_code == 410   # used up
    assert owner.post("/migration/journals/review", json={"token": "../../etc/passwd"}).status_code == 422
    bad = owner.post("/migration/journals/preview", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
    assert bad.status_code == 422
    assert co["login"]("viewer").post("/migration/journals/preview",
                                      files={"file": ("j.csv", xero_export(), "text/csv")}).status_code == 403
    assert co["login"]("accountant").post("/migration/journals/preview",
                                          files={"file": ("j.csv", xero_export(), "text/csv")}).status_code == 200


def test_the_migration_page_has_the_importer():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    ops = (root / "js" / "12-ops.js").read_text(encoding="utf-8")
    assert 'id="ji-file"' in html and 'id="ji-preset"' in html and 'id="ji-preview-btn"' in html
    block = ops.split("// ═══════ Historical journals", 1)[1]
    assert "/migration/journals/preview" in block and "/migration/journals/apply" in block
    assert "onclick" not in block and "confirm(" not in block.replace("uiConfirm(", "")

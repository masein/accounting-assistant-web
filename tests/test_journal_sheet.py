"""The journal as the tax office's Excel sheet (2026-11-02, scenarios BL1–BL7): the owner's
accountant's file, one row per voucher line with its voucher's number, کل and معین."""
from __future__ import annotations

import io
from datetime import date

import pytest

from tests.test_own_codes_engine import _MADE, _in, _purge_made  # noqa: F401 — fixture
from tests.test_own_codes_engine import _company as _school
from tests.test_statement_export import _company

URL = "/manager-reports/books/general-journal/export"
HEADER = ["ردیف", "تاریخ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح",
          "مبلغ بدهکار (ریال)", "مبلغ بستانکار (ریال)"]


@pytest.fixture()
def company(client, db):
    made = []

    def make(locale="ir", currency="IRR", calendar="jalali"):
        api, cid = _company(client, db, locale, currency)
        made.append(cid)
        assert api.put("/admin/display-calendar", json={"calendar": calendar}).status_code == 200
        return api
    yield make
    from tests.test_admin_audit import _purge_company
    for cid in made:
        _purge_company(db, cid)


def _post(api, when, lines, description=None, currency="IRR"):
    body = {"date": when, "description": description, "currency": currency,
            "lines": [{"account_code": c, "debit": d, "credit": k, **({"line_description": n} if n else {})}
                      for c, d, k, n in lines]}
    r = api.post("/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _sheet(api, **params):
    from openpyxl import load_workbook
    r = api.get(URL, params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(r.content))
    assert len(wb.worksheets) == 1
    return wb.worksheets[0], r


def _lines(ws):
    return [[c.value for c in row[:9]] for row in ws.iter_rows(min_row=2) if row[0].value is not None]


def test_the_tax_office_file(company):
    """BL1, BL3, BL4, BL5: the owner's columns, کل and معین, the cells, the totals."""
    api = company()
    sub = api.post("/accounts", json={"name": "بانک دی", "parent_code": "1110"})
    assert sub.status_code == 201, sub.text
    bank = sub.json()["code"]
    # 1405/01/05: tuition into the bank (a معین under 1110); the voucher's description on every line
    _post(api, "2026-03-25", [(bank, 160_000_000, 0, None), ("4110", 0, 160_000_000, None)], "شهریه")
    # 1405/01/08: rent, a line of its own words; a description that looks like a formula stays text
    _post(api, "2026-03-28", [("6112", 600_000_000, 0, "اجاره مدرسه"), (bank, 0, 600_000_000, "=HYPERLINK(\"x\")")])
    gone = _post(api, "2026-04-02", [("6112", 1, 0, None), (bank, 0, 1, None)], "deleted")
    assert api.delete(f"/transactions/{gone}").status_code == 204
    # 1405/03/31: three lines
    _post(api, "2026-06-21", [("6110", 50_000_000, 0, None), ("2180", 0, 45_000_000, None), ("2170", 0, 5_000_000, None)],
          "حقوق خرداد")
    _post(api, "2026-06-22", [("6112", 7, 0, None), (bank, 0, 7, None)], "the next quarter")

    ws, r = _sheet(api, from_date="2026-03-21", to_date="2026-06-21", currency="IRR")
    from urllib.parse import unquote
    assert unquote(r.headers["content-disposition"]).endswith("دفتر روزنامه 1405-01-01 تا 1405-03-31.xlsx")
    assert ws.title.startswith("دفتر روزنامه 1405 ") and ws.sheet_view.rightToLeft
    assert [c.value for c in ws[1]][:9] == HEADER
    assert ws.freeze_panes == "A2"
    rows = _lines(ws)
    assert ws.auto_filter.ref == f"A1:I{len(rows) + 1}"
    assert [r[0] for r in rows] == [1, 1, 2, 2, 3, 3, 3]                     # the voucher's number on each line
    assert [r[1] for r in rows] == ["1405/01/05"] * 2 + ["1405/01/08"] * 2 + ["1405/03/31"] * 3
    tiers = {(r[4] or r[2]): r[2:6] for r in rows}
    assert tiers[int(bank)] == [1110, "موجودی نقد و بانک", int(bank), "بانک دی"]   # a معین names its کل (BL3)
    assert tiers[4110][2:] == [None, None]                                  # a کل: the معین cells empty
    assert rows[0][6] == rows[1][6] == "شهریه"                               # the voucher's description
    assert rows[2][6] == "اجاره مدرسه"                                      # the line's own first
    formula = ws.cell(row=5, column=7)
    assert formula.value == "=HYPERLINK(\"x\")" and formula.data_type == "s"    # text, never a formula
    assert rows[0][7:] == [160_000_000, 0] and rows[1][7:] == [0, 160_000_000]
    money = ws.cell(row=2, column=8)
    assert isinstance(money.value, int) and "#,##0" in money.number_format and '"-"' in money.number_format
    assert isinstance(ws.cell(row=2, column=3).value, int)                   # codes are numbers
    assert all(r[0] != 4 and "deleted" not in str(r[6]) for r in rows)      # deleted: not there; next quarter: not there
    # the totals beside the last line, the difference under them (BL5); numbers, not formulas
    last = len(rows) + 1
    assert ws[f"J{last}"].value == ws[f"K{last}"].value == 810_000_000
    assert ws[f"J{last + 1}"].value == 0
    assert all(c.data_type != "f" for row in ws.iter_rows() for c in row)


def test_numbers_go_on_through_the_year(company):
    """BL2: the second quarter goes on from the first; a new year starts again at 1."""
    api = company()
    for when in ("2025-04-01", "2026-03-10", "2026-03-18", "2026-03-25", "2026-05-01", "2026-07-01", "2026-07-02"):
        _post(api, when, [("6112", 10, 0, None), ("1110", 0, 10, None)], when)
    ws, _ = _sheet(api, from_date="2026-06-22", to_date="2026-09-22")        # 1405 Q2
    assert [(r[0], r[6]) for r in _lines(ws)][::2] == [(3, "2026-07-01"), (4, "2026-07-02")]
    # over the new year: 1404's numbers go on (three before), 1405 starts at 1
    ws, _ = _sheet(api, from_date="2026-03-15", to_date="2026-03-31")
    assert [(r[0], r[1]) for r in _lines(ws)][::2] == [(3, "1404/12/27"), (1, "1405/01/05")]


def test_each_currency(company):
    """BL6: rials, dollars in dollars, and every voucher at its rial value."""
    api = company()
    assert api.post("/fx/rates", json={"from_currency": "USD", "to_currency": "IRR", "rate": 1_000_000,
                                       "effective_date": "2026-01-01"}).status_code == 201
    _post(api, "2026-04-01", [("6112", 900, 0, None), ("1110", 0, 900, None)], "rials")
    _post(api, "2026-04-02", [("6112", 12.5, 0, None), ("1110", 0, 12.5, None)], "dollars", currency="USD")
    ws, _ = _sheet(api, from_date="2026-03-21", to_date="2026-06-21", currency="irr")
    assert {r[6] for r in _lines(ws)} == {"rials"}
    ws, _ = _sheet(api, from_date="2026-03-21", to_date="2026-06-21", currency="USD")
    assert ws.cell(row=1, column=8).value == "مبلغ بدهکار (دلار آمریکا)"
    rows = _lines(ws)
    assert {r[6] for r in rows} == {"dollars"} and rows[0][7] == pytest.approx(12.5)
    assert "0.00" in ws.cell(row=2, column=8).number_format
    ws, _ = _sheet(api, from_date="2026-03-21", to_date="2026-06-21", currency="ALL")
    assert ws.cell(row=1, column=8).value == "مبلغ بدهکار (ریال)"
    rows = _lines(ws)
    assert [r[0] for r in rows] == [1, 1, 2, 2]
    assert rows[2][7] == 12_500_000                                          # the dollars at their rial value


def test_a_uk_company_reads_it_in_english_and_gregorian(company):
    """BL7: English headers, Gregorian dates, numbers from 1 January."""
    api = company(locale="uk", currency="GBP", calendar="gregorian")
    _post(api, "2026-01-05", [("7100", 10, 0, None), ("1200", 0, 10, None)], "January", currency="GBP")
    _post(api, "2026-04-01", [("7100", 90, 0, None), ("1200", 0, 90, None)], "April", currency="GBP")
    ws, r = _sheet(api, from_date="2026-04-01", to_date="2026-06-30")
    assert [c.value for c in ws[1]][:9] == ["Voucher", "Date", "General ledger code", "General ledger account",
                                            "Subsidiary code", "Subsidiary account", "Description",
                                            "Debit (GBP)", "Credit (GBP)"]
    assert not ws.sheet_view.rightToLeft and ws.title.startswith("General journal 2026")
    rows = _lines(ws)
    assert [r[:2] for r in rows] == [[2, "2026/04/01"], [2, "2026/04/01"]]
    from urllib.parse import unquote
    assert unquote(r.headers["content-disposition"]).endswith("General journal 2026-04-01 to 2026-06-30.xlsx")


def test_a_school_file_comes_back_as_it_went_in(db):
    """A journal in the owner's file's shape (فرهنگ نو's, tests/test_own_codes_switch.py), five of its
    vouchers on one day, imported and switched to the school's codes, comes back with the same voucher
    numbers, dates, codes and amounts. A file imported at once was entered in one moment: its own numbers
    keep the day's order. «حقوق پرداختنی» is the app's own account moved under 1600: a معین by its place."""
    import openpyxl

    from app.services.reporting import journal_sheet
    from tests.test_own_codes_switch import ROWS, _import, _switched
    rows = [(v, "1404/05/01" if v <= 5 else d, *rest) for v, d, *rest in ROWS]
    wb = openpyxl.Workbook()
    wb.active.append(HEADER)
    for r in rows:
        wb.active.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    cid = _school(db)
    with _in(cid):
        _import(db, buf.getvalue())
    _switched(db, cid)
    with _in(cid):
        back = journal_sheet.rows(db, date(2025, 7, 23), date(2025, 8, 22), "IRR")   # 1404/05/01–1404/05/31
    assert len(back) == len(rows)
    assert [(r[0], r[1], r[7], r[8]) for r in back] == [(r[0], r[1], r[7], r[8]) for r in rows]
    codes = {(r[0], r[7], r[8]): (r[2], r[4]) for r in back}
    for v, _d, kol, _kn, moin, _mn, _w, debit, credit in rows:
        if kol != 1310:                       # 1310 / 131000 (a معین with no name of its own): one account here
            assert codes[(v, debit, credit)] == (kol, moin), (v, kol, moin)

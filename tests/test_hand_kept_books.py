"""Hand-kept books: a school's Excel journal (scenarios P1–P8, 2026-10-12).

The second real company kept two schools' books by hand: workbooks holding a
bank statement, a chart and a trial balance beside the journal; headers like
«کد حساب معین» and «مبلغ بدهکار (ریال)»; a ledger with no sub-ledger on some
lines; codes that follow no standard; an opening entered after three months;
«بستن حساب بانک سپه»; years typed wrong; a rial out. A made-up school, in a
company of its own (tests share one database)."""
from __future__ import annotations

import io

from openpyxl import Workbook
from sqlalchemy import func, select

from app.db.tenant import use_company
from app.models.account import Account
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from tests.test_posting_roles import co  # noqa: F401 — the fixture

HEAD = ["ردیف", "تاریخ ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح",
        "مبلغ بدهکار (ریال)", "مبلغ بستانکار (ریال)"]
BANK = (1100, "بانک ملت جاری 7748", None, None)
PETTY = (1110, "تنخواه گردان", 111001, "تنخواه گردان خانم نمونه")
TUITION = (1200, "درآمد شهریه", 120000, "درآمد شهریه")
SUPPLIES = (1300, "مخارج مدرسه", 1302, "ملزومات اداری")
FEES = (1300, "مخارج مدرسه", 1301, "کارمزد بانکی")
RENT = (1310, "هزینه اجاره", 131000, None)              # a sub-ledger code with no title
SISTER = (1600, "بستانکاران", 160100, "دبستان نمونه")
RETAINED = (1500, "سود وزیان انباشته", 1501, "سود وزیان انباشته")
SEPAH = (1101, "بانک سپه حساب 770800233303", None, None)
OTHER = (3000, "هزینه های عمومی", 3006, "سایر هزینه های مدرسه")
STAFF = (4000, "هزینه های پرسنلی", None, None)


def _row(n, day, acct, desc, debit, credit, *, number=True, date=True):
    kc, kt, mc, mt = acct
    return [n if number else None, day if date else None, kc, kt, mc, mt, desc, debit or None, credit or None]


def _journal_rows():
    rows = [
        # a year typed wrong: 1401 for 1404 (P7); the bank line has only its ledger (P3)
        _row(1, "1401/01/20", BANK, "واریز شهریه", 200_000_000, 0),
        _row(1, "1401/01/20", TUITION, "واریز شهریه", 0, 200_000_000, number=False, date=False),
        _row(2, "1404/01/29", SUPPLIES, "خرید ملزومات", 50_000_000, 0),
        _row(2, "1404/01/29", PETTY, "خرید ملزومات", 0, 50_000_000, number=False, date=False),
        _row(3, "1404/02/10", RENT, "اجاره فروردین", 70_000_000, 0),
        _row(3, "1404/02/10", BANK, "اجاره فروردین", 0, 70_000_000, number=False, date=False),
        # the opening entered after a quarter (P5), a rial out (P8)
        _row(4, "1404/03/31", BANK, "تراز افتتاحیه", 1_000_000_000, 0),
        _row(4, "1404/03/31", PETTY, "تراز افتتاحیه", 100_000_000, 0, number=False, date=False),
        _row(4, "1404/03/31", SISTER, "تراز افتتاحیه", 0, 600_000_001, number=False, date=False),
        _row(4, "1404/03/31", RETAINED, "تراز افتتاحیه", 0, 500_000_000, number=False, date=False),
        # closing a bank account is a real entry (P6)
        _row(5, "1404/05/01", OTHER, "بابت بستن حساب بانک سپه", 1_162, 0),
        _row(5, "1404/05/01", SEPAH, "بابت بستن حساب بانک سپه", 0, 1_162, number=False, date=False),
        # 1403 typed for 1404 (P7)
        _row(6, "1403/06/05", STAFF, "حقوق شهریور", 30_000_000, 0),
        _row(6, "1403/06/05", BANK, "حقوق شهریور", 0, 30_000_000, number=False, date=False),
        # fifty out: refused (P8)
        _row(7, "1404/07/10", SUPPLIES, "خرید کتاب", 1_000_050, 0),
        _row(7, "1404/07/10", BANK, "خرید کتاب", 0, 1_000_000, number=False, date=False),
    ]
    for i in range(8, 28):                           # a year's bank charges: the typed years are a few among many
        rows += [_row(i, f"1404/08/{i - 7:02d}", FEES, "کارمزد", 10_000, 0),
                 _row(i, f"1404/08/{i - 7:02d}", BANK, "کارمزد", 0, 10_000, number=False, date=False)]
    rows.append([None, None, None, None, None, None, None, 1_521_201_212, 1_521_201_163])   # a totals row
    return rows


def _workbook(journal_rows, *, journal_head=HEAD) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "صورتحساب بانک"
    ws.append(["ردیف", "تاریخ", "شرح", "بدهکار", "بستانکار", "مانده"])
    for i in range(1, 6):
        ws.append([i, f"1404/01/0{i}", "واریز", 0, 1000 * i, 5000 * i])
    chart = wb.create_sheet("کدینگ حسابها")
    chart.append(["ردیف", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح"])
    chart.append([1, 1100, "بانک ملت جاری 7748", 110000, "بانک ملت جاری 7748", None])
    tb = wb.create_sheet("تراز")
    tb.append(["سرفصل حساب", "بدهکار", "بستانکار", "مانده بدهکار", "مانده بستانکار"])
    tb.append(["بانک ملت جاری 7748", 1_200_000_000, 101_000_000, 1_099_000_000, 0])
    js = wb.create_sheet("دفتر روزنامه 1404")
    js.append(journal_head)
    for r in journal_rows:
        js.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _preview(api, data, name="اسناد 1404.xlsx"):
    r = api.post("/migration/journals/preview", files={"file": (name, data, "application/octet-stream")}, data={"preset": "auto"})
    assert r.status_code == 200, r.text
    return r.json()


def _balance(db, code):
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one()
    d, c = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0), func.coalesce(func.sum(TransactionLine.credit), 0))
                      .where(TransactionLine.account_id == acc.id)).one()
    return int(d - c)


def test_p1_p2_p3_the_journal_sheet_and_its_columns_read_themselves(co):
    p = _preview(co["api"], _workbook(_journal_rows()))
    assert p["sheet"] == "دفتر روزنامه 1404"                                  # not the bank statement first
    assert p["preset"] == "generic"                                           # a hand-kept journal, not Hesabfa
    assert [s["name"] for s in p["sheets"]] == ["صورتحساب بانک", "کدینگ حسابها", "تراز", "دفتر روزنامه 1404"]
    assert p["needs"] == [] and p["columns"] == {
        "voucher": 0, "date": 1, "account_code": 4, "account_name": 5, "account_code_general": 2,
        "account_name_general": 3, "description": 6, "debit": 7, "credit": 8}
    assert p["voucher_count"] == 27 and p["counts"].get("unbalanced") == 1   # only the one fifty out
    names = {a["name"] for a in p["accounts"]}
    assert {"بانک ملت جاری 7748", "هزینه های پرسنلی", "بانک سپه حساب 770800233303"} <= names   # ledger-only lines kept
    assert "هزینه اجاره" in names and "" not in names                       # a blank sub-ledger title takes its ledger's

    # a sheet whose header leaves the number and the date blank
    blank = _preview(co["api"], _workbook(_journal_rows(), journal_head=["", ""] + HEAD[2:]))
    assert blank["columns"]["voucher"] == 0 and blank["columns"]["date"] == 1 and blank["voucher_count"] == 27

    # another sheet can be read, and starts from its own columns
    other = co["api"].post("/migration/journals/review", json={"token": p["token"], "sheet": "صورتحساب بانک"})
    assert other.status_code == 200 and other.json()["sheet"] == "صورتحساب بانک"
    assert "voucher" not in other.json()["columns"]                          # a row counter, not a voucher number


def test_p4_to_p8_a_schools_year_goes_in_whole(co, db):
    api = co["api"]
    p = _preview(api, _workbook(_journal_rows()))
    plan = {a["name"]: (a["mapped_to"], a["how"]) for a in p["accounts"]}
    assert plan["درآمد شهریه"] == ("new:41", "create")
    assert plan["ملزومات اداری"][0] == "new:61" and plan["هزینه اجاره"][0] == "new:61"
    assert plan["کارمزد بانکی"][0] == "new:62"
    assert plan["بانک ملت جاری 7748"][0] == "new:bank"
    assert plan["دبستان نمونه"][0] == "new:21"                                 # through its ledger «بستانکاران»
    assert plan["سود وزیان انباشته"] == ("3300", "role")
    assert plan["تنخواه گردان خانم نمونه"][0].startswith("new:")
    assert p["stray_years"] == 2 and p["counts"]["out_of_year"] == 2 and p["file_year"] == 1404

    fixed = api.post("/migration/journals/review", json={"token": p["token"], "years": "fix"}).json()
    assert "out_of_year" not in fixed["counts"] and fixed["years_mode"] == "fix"
    assert fixed["counts"] == {"ready": 26, "unbalanced": 1}                  # the opening is in (P5)

    out = api.post("/migration/journals/apply", json={"token": p["token"], "years": "fix"})
    assert out.status_code == 200, out.text
    out = out.json()
    assert out["posted"] == 26 and out["skipped"] == {"unbalanced": 1} and out["accounts_created"] >= 8

    with use_company(co["company"].id):
        db.expire_all()
        bank = db.execute(select(Entity).where(Entity.type == "bank", Entity.name == "بانک ملت جاری 7748")).scalars().one()
        # 200 in, 70 rent, 1,000 opening, 30 salaries, 20 × 10,000 fees: the typed years moved into 1404
        assert _balance(db, bank.code) == 200_000_000 - 70_000_000 + 1_000_000_000 - 30_000_000 - 200_000
        dates = sorted(d for (d,) in db.execute(select(Transaction.date)))
        from app.utils.jalali import gregorian_to_jalali
        assert {gregorian_to_jalali(d)[0] for d in dates} == {1404}
        tuition = db.execute(select(Account).where(Account.name == "درآمد شهریه")).scalars().one()
        assert tuition.code.startswith("41") and _balance(db, tuition.code) == -200_000_000
        petty = db.execute(select(Account).where(Account.name == "تنخواه گردان خانم نمونه")).scalars().one()
        assert petty.statement_line == "ca_cash"                              # a petty cash reads as cash whatever its code
        sister = db.execute(select(Account).where(Account.name == "دبستان نمونه")).scalars().one()
        assert sister.code.startswith("21") and _balance(db, sister.code) == -600_000_000   # the rial absorbed (P8)
        assert _balance(db, "3300") == -500_000_000
        other = db.execute(select(Account).where(Account.name == "سایر هزینه های مدرسه")).scalars().one()
        assert _balance(db, other.code) == 1_162                              # «بستن حساب بانک سپه» posted (P6)
        assert other.parent.name == "هزینه های عمومی"                         # under its own ledger, as the school keeps it
        sepah = db.execute(select(Entity).where(Entity.type == "bank", Entity.account_number == "770800233303")).scalars().one()
        assert sepah.name == "بانک سپه"                                       # the number kept apart from the name

    # the next year: its opening repeats the year's balances and is left out; the accounts are found again
    nxt = [_row(1, "1405/01/01", BANK, "تراز افتتاحیه", 1_099_800_000, 0),
           _row(1, "1405/01/01", RETAINED, "تراز افتتاحیه", 0, 1_099_800_000, number=False, date=False),
           _row(2, "1405/01/15", TUITION, "واریز شهریه", 0, 9_000_000),
           _row(2, "1405/01/15", BANK, "واریز شهریه", 9_000_000, 0, number=False, date=False),
           _row(3, "1405/01/16", SUPPLIES, "بابت بستن حسابهای موقت", 0, 5),
           _row(3, "1405/01/16", RETAINED, "بابت بستن حسابهای موقت", 5, 0, number=False, date=False)]
    q = _preview(api, _workbook(nxt), "اسناد 1405.xlsx")
    assert q["counts"] == {"opening_repeat": 1, "ready": 1, "year_end_closing": 1}
    plan = {a["name"]: a["how"] for a in q["accounts"]}
    assert plan["درآمد شهریه"] == "chosen" and plan["بانک ملت جاری 7748"] == "chosen"

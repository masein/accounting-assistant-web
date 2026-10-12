"""A company's years from Sepidar, voucher by voucher (scenarios O9–O11, 2026-10-12).

A real company's three years imported cleanly through the journal import,
the statements agreeing with Sepidar's, but: every bank line stayed on the
bank sub-ledger (111005) with the banks' own accounts at 0; the debtors and
creditors report was empty, the parties' balances being spread over other
receivables, salaries and other payables; and with the full chart's twins
(rial and foreign-currency petty cash, ...) half the kinds of entry found no
account to post to. A company of its own (tests share one database)."""
from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy import func, select

from app.db.tenant import use_company
from app.models.account import Account
from app.models.entity import Entity
from app.services.posting_roles import _pick
from tests.test_migration_import import HEADER_4, HEADER_TAFSILI, _ss_xml, _upload
from tests.test_posting_roles import co  # noqa: F401 — the fixture

BANK = "بانك نمونه تاريخ 0101234567011"
CLIENT = "مشتري نمونه تاريخ"
EMPLOYEE = "كارمند نمونه تاريخ"


def _chart():
    zero = ["0", "0", "0", "0"]
    kol = _ss_xml([HEADER_4, *[[c, t, *zero] for c, t in [
        ("1110", "موجودي نقد و بانك"), ("1113", "ساير حسابها و اسناد دريافتني"), ("2111", "ساير حسابهاي پرداختني"),
        ("4110", "فروش"), ("6110", "هزينه هاي كاركنان")]]])
    moein = _ss_xml([HEADER_4, *[[c, t, *zero] for c, t in [
        ("111003", "تنخواه گردانهاي ريالي"), ("111004", "تنخواه گردانهاي ارزي"),
        ("111005", "موجودي بانكهاي ريالي"), ("111006", "موجودي بانكهاي ارزي"),
        ("111304", "ساير حسابهاي دريافتني"), ("211101", "حقوق و دستمزد پرداختني"), ("211104", "تسويه حقوق پرداختني"),
        ("211201", "پيش دريافت فروش خدمات"), ("211209", "ساير پيش دريافتها"),
        ("411003", "فروش خدمات"), ("611001", "حقوق پايه"), ("611020", "ساير حقوق و مزايا")]]])
    tafsili = _ss_xml([HEADER_TAFSILI,
                       ["001", BANK, "حساب بانكي", *zero],
                       ["20001", CLIENT, "طرف مقابل", *zero],
                       ["10001", EMPLOYEE, "طرف مقابل", *zero]])
    return [("كل.xls", kol), ("معين.xls", moein), ("تفصيلي.xls", tafsili)]


def _journal() -> bytes:
    """Sepidar's «اقلام اسناد»: one row per voucher line, the detail account by name."""
    rows = [
        ("1", "1403/04/19", "111304", "ساير حسابهاي دريافتني", CLIENT, "فروش اقساطي", 4_200_000, 0),
        ("1", "1403/04/19", "411003", "فروش خدمات", "", "فروش اقساطي", 0, 4_200_000),
        ("2", "1403/05/31", "611001", "حقوق پايه", "", "حقوق مرداد", 3_000_000, 0),
        ("2", "1403/05/31", "211101", "حقوق و دستمزد پرداختني", EMPLOYEE, "حقوق مرداد", 0, 3_000_000),
        ("3", "1403/06/10", "111005", "موجودي بانكهاي ريالي", BANK, "فروش نقدي", 5_000_000, 0),
        ("3", "1403/06/10", "411003", "فروش خدمات", "", "فروش نقدي", 0, 5_000_000),
        ("4", "1403/06/12", "111003", "تنخواه گردانهاي ريالي", "", "شارژ تنخواه", 50, 0),
        ("4", "1403/06/12", "111005", "موجودي بانكهاي ريالي", BANK, "شارژ تنخواه", 0, 50),
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ش سند", "تاریخ", "کد معین", "نام معین", "نام تفصیلی", "شرح", "بدهکار", "بستانکار"])
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _import(api):
    r = api.post("/migration/import/preview", files=_upload(_chart()))
    assert r.status_code == 200, r.text
    assert api.post("/migration/import/confirm", json={"token": r.json()["token"]}).status_code == 200
    p = api.post("/migration/journals/preview", files={"file": ("اقلام اسناد 1403.csv", _journal(), "text/csv")},
                 data={"preset": "auto"})
    assert p.status_code == 200, p.text
    assert p.json()["preset"] == "sepidar" and p.json()["counts"] == {"ready": 4}, p.json()["counts"]
    out = api.post("/migration/journals/apply", json={"token": p.json()["token"], "account_map": {}})
    assert out.status_code == 200, out.text
    return out.json()


def _balance(db, code):
    from app.models.transaction import TransactionLine
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one()
    d, c = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0), func.coalesce(func.sum(TransactionLine.credit), 0))
                      .where(TransactionLine.account_id == acc.id)).one()
    return int(d - c)


def test_o9_a_banks_lines_go_to_its_own_account(co, db):
    out = _import(co["api"])
    assert out["posted"] == 4 and out["bank_lines"] == 2
    with use_company(co["company"].id):
        db.expire_all()
        bank = db.execute(select(Entity).where(Entity.type == "bank", Entity.account_number == "0101234567011")).scalars().one()
        assert bank.code == "111005001"                              # the rial sub-ledger, not 111006
        assert _balance(db, "111005001") == 4_999_950
        assert _balance(db, "111005") == 0


def test_o10_debtors_and_creditors_from_the_imported_years(co, db):
    _import(co["api"])
    with use_company(co["company"].id):
        db.expire_all()
        names = [e.name for e in db.execute(select(Entity)).scalars()]
        assert len(names) == len(set(names)) == 3                     # matched by name: no second «supplier»
    today = date.today().isoformat()
    body = co["api"].get(f"/manager-reports/operational/debtor-creditor?from_date=2024-03-20&to_date={today}").json()
    assert {r["entity_name"]: r["total"] for r in body["debtors"]} == {"مشتری نمونه تاریخ": 4_200_000}
    assert {r["entity_name"]: r["total"] for r in body["creditors"]} == {"کارمند نمونه تاریخ": 3_000_000}


def test_o11_every_kind_of_entry_finds_its_account_in_a_full_chart(co):
    out = _import(co["api"])
    roles = {r["category"]: r["code"] for r in co["api"].get("/accounts/posting-roles").json()}
    assert roles["petty_cash"] == "111003" and roles["bank"] == "111005"       # the rial twin
    assert roles["net_pay_payable"] == "211101"                                # not «تسویه حقوق»
    assert roles["customer_credit"] == "211201" and roles["wages_expense"] == "611001"   # not «سایر ...»
    assert roles["revenue"] == "411003"
    assert isinstance(out["posting_roles"], dict)


def test_pick_settles_twins_by_currency_catch_all_use_and_title():
    hits = [("111003", "تنخواه گردانهای ریالی"), ("111004", "تنخواه گردانهای ارزی")]
    assert _pick(hits, {}) == "111003"
    assert _pick([("211201", "پیش دریافت فروش خدمات"), ("211209", "سایر پیش دریافتها")], {}) == "211201"
    five = [("611301", "هزینه استهلاک"), ("611302", "هزینه استهلاک تاسیسات"), ("611305", "هزینه استهلاک کامپیوتر")]
    assert _pick(five, {}) == "611301"                                          # the plainest title
    assert _pick(five, {"611305": 12, "611301": 3}) == "611305"                 # unless the books use another
    assert _pick([("1", "حساب یک"), ("2", "حساب دو")], {}) is None              # still a tie: the owner picks

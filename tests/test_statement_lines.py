"""The balance sheet reads a migrated chart (scenarios O5, O6, 2026-10-12).

A Sepidar chart numbers its ledgers otherwise than the seeded chart the
Iranian statements' prefixes were written for. A migration names each
ledger's line from its title, the accounts under it inherit it, and cash is
one figure everywhere. Made-up ledgers in codes no other test uses (the test
database is shared, so the balance sheet is read over this test's accounts)."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.services.cash_service import cash_on_hand, company_cash_predicate
from app.services.locale_service import get_reporting_locale
from app.services.reporting import statement_lines as sl
from app.services.reporting.iran_statement_service import _balance_sheet_buckets, _bs_bucket_for_code, _is_cash_code
from tests.test_migration_import import HEADER_4, _ss_xml, _upload


@pytest.mark.parametrize("code,title,line", [
    ("1110", "موجودي نقد و بانك", "ca_cash"),
    ("111003", "تنخواه گردانهاي ريالي", "ca_cash"),
    ("111005", "موجودي بانكهاي ريالي", "ca_cash"),
    ("1111", "سپرده هاي كوتاه مدت", "ca_st_investments"),
    ("111401", "سپرده ‌هاي كوتاه مدت نزد بانكها", "ca_st_investments"),   # deposits at banks are not cash
    ("1114", "سرمايه گذاري ‌هاي كوتاه مدت", "ca_st_investments"),
    ("1112", "حسابها و اسناد دريافتني تجاري", "ca_trade_receivables"),
    ("1113", "ساير حسابها و اسناد دريافتني", "ca_trade_receivables"),
    ("1118", "پيش پرداختها", "ca_prepayments"),
    ("111804", "پيش پرداخت خريد", "ca_prepayments"),
    ("1210", "دارايي‌هاي ثابت مشهود", "nca_ppe"),
    ("1211", "استهلاك انباشته", "nca_ppe"),
    ("121102", "استهلاك دارايي نامشهود", "nca_intangibles"),
    ("1212", "داراييهاي ثابت نامشهود", "nca_intangibles"),
    ("121201", "نرم افزارها", "nca_intangibles"),
    ("2110", "حسابها و اسناد پرداختني تجاري", "cl_trade_payables"),
    ("211128", "ماليات حقوق پرداختني كاركنان", "cl_trade_payables"),   # withheld, not the company's tax
    ("2112", "پيش ‌دريافتها", "cl_advances"),
    ("3110", "سرمايه", "eq_capital"),
    ("3113", "سود و زيان انباشته", "eq_retained_earnings"),
    ("3114", "سود و زيان جاري", "eq_retained_earnings"),
    ("11", "داراييهاي جاري", None),                                  # says nothing a rule knows
    ("211116", "جاري شركا", None),                                    # takes its ledger's line
    ("911105", "اسناد تضميني بابت تسهيلات مالي دريافتي", None),        # memorandum: not on the balance sheet
])
def test_a_title_names_its_line(code, title, line):
    assert sl.infer(code, title) == line


def _chart():
    kol = _ss_xml([
        HEADER_4,
        ["1181", "پيش پرداختها آزمون", "0", "0", "0", "0"],
        ["1182", "سرمايه گذاري هاي كوتاه مدت آزمون", "0", "0", "0", "0"],
        ["1183", "موجودي نقد و بانك آزمون", "0", "0", "0", "0"],
        ["1285", "داراييهاي ثابت نامشهود آزمون", "0", "0", "0", "0"],
        ["1286", "استهلاك انباشته آزمون", "0", "0", "0", "0"],
        ["2187", "پيش دريافتها آزمون", "0", "0", "0", "0"],
        ["3188", "سود و زيان انباشته آزمون", "0", "0", "0", "0"],
        ["3189", "سرمايه آزمون", "0", "0", "0", "0"],
    ])
    moein = _ss_xml([
        HEADER_4,
        ["118104", "پيش پرداخت خريد آزمون", "0", "0", "0", "0"],
        ["118201", "سپرده هاي كوتاه مدت نزد بانكها آزمون", "0", "0", "0", "0"],
        ["118301", "تنخواه گردانها آزمون", "0", "0", "0", "0"],
        ["128501", "نرم افزارها آزمون", "0", "0", "0", "0"],
        ["128602", "استهلاك دارايي نامشهود آزمون", "0", "0", "0", "0"],
        ["218701", "پيش دريافت فروش خدمات آزمون", "0", "0", "0", "0"],
        ["318801", "سود و زيان انباشته آزمون", "0", "0", "0", "0"],
        ["318901", "سرمايه ثبت شده آزمون", "0", "0", "0", "0"],
    ])
    return [("كل.xls", kol), ("معين.xls", moein)]


BALANCES = [("118104", 900, 0), ("118201", 300, 0), ("118301", 50, 0), ("128501", 700, 0),
            ("128602", 0, 100), ("218701", 0, 1000), ("318801", 0, 400), ("318901", 0, 450)]


def _import_chart(auth_client):
    """No balances in the files, so the books' opening voucher is never touched."""
    r = auth_client.post("/migration/import/preview", files=_upload(_chart()))
    assert r.status_code == 200, r.text
    c = auth_client.post("/migration/import/confirm", json={"token": r.json()["token"], "opening": "keep"})
    assert c.status_code == 200, c.text


def test_o5_the_balance_sheet_reads_the_imported_chart(auth_client, db):
    _import_chart(auth_client)
    t = auth_client.post("/transactions", json={"date": date.today().isoformat(), "description": "مانده‌های آزمون خطوط ترازنامه",
                                               "lines": [{"account_code": a, "debit": d, "credit": k} for a, d, k in BALANCES]})
    assert t.status_code == 201, t.text

    db.expire_all()
    mine = db.execute(select(Account).where(Account.code.in_([b[0] for b in BALANCES]))).scalars().all()
    assert {a.code: a.parent.statement_line for a in mine}["118104"] == "ca_prepayments"   # the ledger's line, inherited
    buckets = _balance_sheet_buckets(db, mine, date.today(), None)
    assert {k[1]: v for k, v in buckets.items() if v} == {
        "ca_prepayments": 900, "ca_st_investments": 300, "ca_cash": 50, "nca_intangibles": 600,
        "cl_advances": 1000, "eq_retained_earnings": 400, "eq_capital": 450,
    }
    # The code prefixes alone put them on catch-all lines, and advances received
    # on «liabilities of assets held for sale» (the standard's 218).
    assert {_bs_bucket_for_code(code)[1] for code, _d, _k in BALANCES} == {
        "ca_other", "nca_other", "cl_held_for_sale_liab", "eq_other"}


def test_o6_one_cash_figure(auth_client, db):
    _import_chart(auth_client)
    db.expire_all()
    lines = sl.line_map(db)
    is_cash = company_cash_predicate(db)
    if (get_reporting_locale(db) or "").lower() != "uk":
        assert is_cash("118301") and is_cash("1160") and is_cash("1110")      # petty cash, migrated or seeded
        accounts = db.execute(select(Account)).scalars().all()
        bs_cash = _balance_sheet_buckets(db, accounts, date.today(), None).get(("current_assets", "ca_cash"), 0)
        assert cash_on_hand(db, locale=get_reporting_locale(db), as_of=date.today()) == bs_cash
    assert _is_cash_code("118301", lines) and not _is_cash_code("118201", lines)

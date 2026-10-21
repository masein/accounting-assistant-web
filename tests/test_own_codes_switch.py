"""A company switched to its own account codes (docs/PLAN_OWN_CODES.md phase 2, scenarios AN3–AN7).

A school's journal in the shape of فرهنگ نو's (2026-10-21): its own codes, a ledger (کل) and a
sub-ledger (معین) a line; one code under two spellings («وزارت دارایی» and «دارایی» are 1602,
«حقوق پرداختنی» and «حقوق پرداختنی -» 1603, two «پذیرایی…» 3001); rent under 1310 / 131000;
5000 «سود و زیان»; and petty cash 1110, the code of the app's own «موجودی نقد و بانک». Imported as the
chat imports it, then switched: the Ledger reads the school's codes, the statements don't move, and
everything that named an account by its code follows it."""
from __future__ import annotations

import io
import json
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from tests.test_own_codes_engine import _MADE, _company, _in, _purge_made  # noqa: F401 — fixture

M = 1_000_000
ROWS = [  # voucher, date, ledger code, ledger, sub code, sub, words, debit, credit
    (1, "1404/05/01", 1100, "بانک دی حساب 0105222028000", None, None, "شهریه", 300 * M, 0),
    (1, "1404/05/01", 2000, "درآمد شهریه", None, None, "شهریه", 0, 300 * M),
    (2, "1404/05/03", 3000, "هزینه های عمومی", 3001, "پذیرایی از همکاران و اولیا ونظافت طی ماه", "پذیرایی", 4 * M, 0),
    (2, "1404/05/03", 3000, "هزینه های عمومی", 3002, "ملزومات مدرسه", "ملزومات", 6 * M, 0),
    (2, "1404/05/03", 1110, "تنخواه گردان", None, None, "تنخواه", 0, 10 * M),
    (3, "1404/05/05", 1110, "تنخواه گردان", None, None, "شارژ تنخواه", 50 * M, 0),
    (3, "1404/05/05", 1100, "بانک دی حساب 0105222028000", None, None, "شارژ تنخواه", 0, 50 * M),
    (4, "1404/05/10", 4000, "هزینه های پرسنلی", 4001, "حقوق و مزایا", "حقوق", 80 * M, 0),
    (4, "1404/05/10", 1600, "بستانکاران", 1603, "حقوق پرداختنی", "حقوق", 0, 70 * M),
    (4, "1404/05/10", 1600, "بستانکاران", 1602, "وزارت دارایی", "مالیات حقوق", 0, 10 * M),
    (5, "1404/05/12", 1600, "بستانکاران", 1603, "حقوق پرداختنی -", "پرداخت حقوق", 70 * M, 0),
    (5, "1404/05/12", 1100, "بانک دی حساب 0105222028000", None, None, "پرداخت حقوق", 0, 70 * M),
    (6, "1404/05/15", 1600, "بستانکاران", 1602, "دارایی", "پرداخت مالیات", 10 * M, 0),
    (6, "1404/05/15", 1100, "بانک دی حساب 0105222028000", None, None, "پرداخت مالیات", 0, 10 * M),
    (7, "1404/05/20", 1310, "هزینه اجاره", 131000, None, "اجاره", 40 * M, 0),
    (7, "1404/05/20", 1100, "بانک دی حساب 0105222028000", None, None, "اجاره", 0, 40 * M),
    (8, "1404/05/25", 3000, "هزینه های عمومی", 3001, "پذیرایی از همکاران و اولیا ونظافت", "پذیرایی", 2 * M, 0),
    (8, "1404/05/25", 1110, "تنخواه گردان", None, None, "پذیرایی", 0, 2 * M),
    (9, "1404/05/30", 5000, "سود و زیان", None, None, "بستن", 1 * M, 0),
    (9, "1404/05/30", 2000, "درآمد شهریه", None, None, "بستن", 0, 1 * M),
]


def _xlsx() -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ردیف", "تاریخ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح",
               "مبلغ بدهکار (ریال)", "مبلغ بستانکار (ریال)"])
    for r in ROWS:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(db, data: bytes, sheet: str | None = None) -> None:
    from app.services import journal_import as ji
    from app.services.locale_service import set_display_calendar
    set_display_calendar(db, "jalali")
    db.commit()
    _sheet, rows, _ = ji.pick_sheet(ji.read_book("school.xlsx", data), sheet)
    parsed = ji.settle_years(db, ji.parse_file(rows), None)
    assert not parsed["needs"], parsed["needs"]
    review = ji.review(db, parsed)
    ji.apply(db, parsed, account_map={a["key"]: a["mapped_to"] for a in review["accounts"]})
    db.commit()


def _statements(db) -> dict:
    from app.services.reporting import iran_statement_service as ir
    span = dict(from_date=date(2025, 3, 21), to_date=date(2026, 3, 20))
    inc = ir.build_iran_income_statement(db, **span).model_dump(mode="json")
    bal = ir.build_iran_balance_sheet(db, as_of=date(2026, 3, 20)).model_dump(mode="json")
    return {"income": [(r.get("key"), r.get("amount_current")) for r in inc["rows"]],
            "balance": [(r.get("key"), r.get("amount_current")) for r in bal["rows"]]}


def _ledger(db) -> dict[str, tuple]:
    from app.api.reports import get_ledger_summary
    out = get_ledger_summary(currency=None, db=db).model_dump(mode="json")
    return {r["account_code"]: (r["account_name"], r["parent_code"], r["debit_balance"], r["credit_balance"])
            for r in out["rows"]}


def _switched(db, cid) -> tuple[dict, dict]:
    from app.services import own_codes
    with _in(cid):
        preview = own_codes.preview(db)
        codes = {a["id"]: a["proposed"] for a in preview["accounts"] if a["proposed"] and not a["merged_into"]}
        codes.update({a["id"]: a["new_code"] for a in preview["aside"]})
        out = own_codes.apply(db, codes=codes, merges=[{"keep": m["keep"], "merge": m["merge"]} for m in preview["merges"]])
        db.commit()
        return preview, out


def test_an3_an5_the_preview_merges_twins_and_puts_the_apps_1110_aside(db):
    from app.models.account import Account
    from app.services import own_codes
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
        p = own_codes.preview(db)
        names = {a["id"]: a["name"] for a in p["accounts"]}
        merged = {m["code"]: sorted([names[m["keep"]]] + [names[i] for i in m["merge"]]) for m in p["merges"]}
        assert merged["1602"] == ["دارایی", "وزارت دارایی"]
        assert sorted(merged) == ["1602", "1603", "3001"]
        assert p["clashes"] == [] and p["ready"] is True
        aside = {a["code"]: a["new_code"] for a in p["aside"]}
        assert aside == {"1110": "11100"}                                      # the app's unused 1110
        ours = db.execute(select(Account).where(Account.code == "1110")).scalar_one()
        assert ours.name == "موجودی نقد و بانک"


def test_an3_an4_the_ledger_reads_the_schools_codes_and_the_statements_do_not_move(db):
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
        before = _statements(db)
        ledger_before = _ledger(db)
    preview, out = _switched(db, cid)
    assert out["merged"] == 3 and out["lines_moved"] >= 3
    with _in(cid):
        after = _statements(db)
        ledger = _ledger(db)
    assert after == before                                                     # AN4
    assert set(ledger) == {"1100", "1110", "1310", "131000", "1602", "1603", "2000", "3001", "3002", "4001", "5000"} \
        or set(ledger) >= {"1100", "1110", "1602", "1603", "2000", "3001", "3002", "4001", "5000"}, sorted(ledger)
    assert ledger["3001"][1] == "3000" and ledger["3002"][1] == "3000"         # under the school's own ledger
    assert ledger["1602"][1] == "1600" and ledger["1603"][1] == "1600"
    assert ledger["4001"][1] == "4000"
    assert ledger["2000"][1] is None and ledger["1100"][1] is None             # a ledger with no sub: its own row
    assert ledger["3001"][2] == 6 * M                                          # both spellings, one account
    assert ledger["1602"][3] == 0 and ledger["1603"][3] == 0                   # paid off
    net = lambda rows: sum(v[2] - v[3] for v in rows.values())                 # noqa: E731
    assert net(ledger) == net(ledger_before) == 0
    # the twins were one account under two spellings: merged, their balances meet (70 paid of 70 owed)
    assert sum(v[2] for v in ledger_before.values()) - sum(v[2] for v in ledger.values()) == 80 * M


def test_an6_everything_that_names_an_account_follows_it(db):
    from app.models.account import Account
    from app.models.app_setting import AppSetting
    from app.models.entity import Entity
    from app.models.recurring import RecurringRule
    from app.services.posting_roles import SETTINGS_KEY
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
        bank = db.execute(select(Entity).where(Entity.type == "bank")).scalar_one()
        old_bank = bank.code
        petty = db.execute(select(Account).where(Account.source_codes == "1110")).scalar_one()
        db.add(RecurringRule(name="اجاره", direction="payment", frequency="monthly", amount=40 * M,
                             start_date=date(2026, 6, 1), next_run_date=date(2026, 6, 1),
                             bank_account_code=old_bank, counter_account_code=petty.code))
        from app.services import posting_roles
        posting_roles.save(db, {"bank": old_bank}, source="owner")
        db.commit()
    _preview, out = _switched(db, cid)
    with _in(cid):
        db.expire_all()
        assert db.execute(select(Entity.code).where(Entity.type == "bank")).scalar_one() == "1100"
        rule = db.execute(select(RecurringRule)).scalar_one()
        assert (rule.bank_account_code, rule.counter_account_code) == ("1100", "1110")
        roles = json.loads(db.execute(select(AppSetting.value).where(AppSetting.key == SETTINGS_KEY)).scalar_one())
        assert roles["bank"]["code"] == "1100"
        from app.services.account_resolver import resolve_account_code
        assert resolve_account_code(db, "bank") == "1100"                      # the role, under its new code
        from app.services.account_resolver import find_account_code
        assert find_account_code(db, "bank", own=False) == "11100"             # the app's own 1110, aside, still found


def test_an7_a_new_account_under_the_schools_ledger(db):
    from app.models.account import Account
    from app.services.chart_codes import class_of
    from app.services.chart_service import create_account
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
    _switched(db, cid)
    with _in(cid):
        acc = create_account(db, name="هزینه آموزش", parent_code="3000")
        db.commit()
        assert acc.code == "3003" and class_of("3003").startswith(class_of("3000"))
        from app.services.reporting.common import EXPENSE, classify_account_code
        assert classify_account_code("3003") == EXPENSE
        assert db.execute(select(func.count()).select_from(Account).where(Account.code == "3003")).scalar() == 1


def test_an3_switched_once(db):
    from fastapi import HTTPException
    from app.services import own_codes
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
    _switched(db, cid)
    with _in(cid), pytest.raises(HTTPException) as e:
        own_codes.preview(db)
    assert e.value.status_code == 409


def test_every_column_that_names_an_account_follows_it():
    """A model column holding an account's code that the switch doesn't move would point at another account."""
    import importlib
    from app.db.base import Base
    from app.services.own_codes import CODE_REFERENCES
    listed = {(cls, attr) for _m, cls, attr in CODE_REFERENCES}
    for mapper in Base.registry.mappers:
        for col in mapper.columns:
            if col.key.endswith("account_code"):
                assert (mapper.class_.__name__, col.key) in listed, (mapper.class_.__name__, col.key)
    for module, cls, attr in CODE_REFERENCES:
        assert hasattr(getattr(importlib.import_module(module), cls), attr)


REAL = ["/data/اسناد و تراز یکساله 1404 مدارس منادی حق وفرهنگ نو.xlsx", "/data/دفتر روزنامه الکترونیکی سال 1405 فرعنگ نو.xlsx"]


@pytest.mark.skipif(not os.path.exists(REAL[1]), reason="فرهنگ نو's own files are only on the owner's machine")
def test_an3_farhang_no_in_its_own_codes(db):
    cid = _company(db)
    with _in(cid):
        for path, sheets in ((REAL[0], ["دفتر روزنامه6ماهه اول فرهنگ نو", " روزنامه 6ماهه دوم فرهنگ نو"]),
                             (REAL[1], ["دفتر روزنامه 3ماهه اول1405"])):
            data = open(path, "rb").read()
            for s in sheets:
                _import(db, data, s)
        before = _statements(db)
    preview, out = _switched(db, cid)
    print("\nPREVIEW", json.dumps({k: preview[k] for k in ("merges", "clashes", "aside", "counts")}, ensure_ascii=False))
    with _in(cid):
        assert _statements(db) == before
        ledger = _ledger(db)
    print("LEDGER", json.dumps(ledger, ensure_ascii=False))
    assert {"3001", "3002", "3003", "4001", "4002", "1602", "1603", "2000", "1100", "1110"} <= set(ledger)
    assert ledger["3001"][1] == "3000" and ledger["4001"][1] == "4000" and ledger["1602"][1] == "1600"


def test_an8_the_next_file_is_in_the_same_codes(db):
    """After the switch, a journal in the school's codes maps by them, nothing to match by hand; a code the
    books don't have yet is made as itself under its own ledger, and is what its ledger is."""
    import openpyxl
    from app.models.account import Account
    from app.services import journal_import as ji
    from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
    _switched(db, cid)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ردیف", "تاریخ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح",
               "مبلغ بدهکار (ریال)", "مبلغ بستانکار (ریال)"])
    for r in [(1, "1404/06/02", 1100, "بانک دی حساب 0105222028000", None, None, "شهریه مهر", 120 * M, 0),
              (1, "1404/06/02", 2000, "درآمد شهریه", None, None, "شهریه مهر", 0, 120 * M),
              (2, "1404/06/05", 3000, "هزینه های عمومی", 3007, "هزینه آموزش ضمن خدمت", "کارگاه", 9 * M, 0),
              (2, "1404/06/05", 3000, "هزینه های عمومی", 3002, "ملزومات مدرسه", "کاغذ", 3 * M, 0),
              (2, "1404/06/05", 1110, "تنخواه گردان", None, None, "کارگاه", 0, 12 * M)]:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    with _in(cid):
        _sheet, rows, _ = ji.pick_sheet(ji.read_book("1405.xlsx", buf.getvalue()), None)
        parsed = ji.settle_years(db, ji.parse_file(rows), None)
        review = ji.review(db, parsed)
        how = {a["code"]: (a["mapped_to"], a["how"]) for a in review["accounts"]}
        # by its code, or by the import's own map, which the switch moved to the new codes
        assert (how["1100"][0], how["2000"][0], how["3002"][0]) == ("1100", "2000", "3002")
        assert {how[c][1] for c in ("1100", "2000", "3002")} <= {"code", "chosen"}
        assert how["3007"][0].startswith("new:")                               # the one new account
        ji.apply(db, parsed, account_map={a["key"]: a["mapped_to"] for a in review["accounts"]})
        db.commit()
        made = db.execute(select(Account).where(Account.code == "3007")).scalar_one()
        assert made.name == "هزینه آموزش ضمن خدمت"
        assert db.get(Account, made.parent_id).code == "3000"                  # under its own ledger
        assert classify_account_code("3007") == EXPENSE and classify_account_code("2000") == REVENUE

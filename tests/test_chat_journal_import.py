"""A school's journals dropped in the chat, and its own codes (scenarios AH1–AH11).

From the owner, in منادی حق's books (2026-10-19): two years of the school's
Excel journal dropped in the chat. An old parser read only the first file, the
date column as the voucher number and the ledger code as the day; six
vouchers went in one-sided, dated 2027-01-30. The import of the books had
given the school's accounts the app's codes (their 1300 «مخارج مدرسه» became
6211 and 6132, two headings), so «باید ۱۳۰۰ میبوده» found nothing. A made-up
school in the same layout, in a company of its own (tests share one database)."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import uuid
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.account import Account
from app.models.ai_accountant import AIChatMessage
from app.models.audit_log import AuditLog
from app.models.transaction import Transaction, TransactionLine
from tests.test_hand_kept_books import BANK, FEES, HEAD, PETTY, SUPPLIES, TUITION, _row
from tests.test_posting_roles import co  # noqa: F401 — the fixture

ROOT = Path(__file__).resolve().parents[1]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SCHOOL = "این فایل های اکسل دو سال گذشته مدرسه هست"


def _book(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "دفتر روزنامه"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _year_1404(by: int = 0) -> bytes:
    """Five vouchers from ۲۹ فروردین to ۲۰ آبان ۱۴۰۴: tuition, supplies and bank fees under «مخارج مدرسه»."""
    return _book([
        _row(1, "1404/01/29", BANK, "واریز شهریه", 200_000_000 + by, 0),
        _row(1, "1404/01/29", TUITION, "واریز شهریه", 0, 200_000_000 + by, number=False, date=False),
        _row(2, "1404/02/03", SUPPLIES, "خرید ملزومات", 5_000_000 + by, 0),
        _row(2, "1404/02/03", PETTY, "خرید ملزومات", 0, 5_000_000 + by, number=False, date=False),
        _row(3, "1404/05/10", FEES, "کارمزد", 10_000 + by, 0),
        _row(3, "1404/05/10", BANK, "کارمزد", 0, 10_000 + by, number=False, date=False),
        _row(4, "1404/07/01", SUPPLIES, "خرید کتاب", 1_000_000 + by, 0),
        _row(4, "1404/07/01", BANK, "خرید کتاب", 0, 1_000_000 + by, number=False, date=False),
        _row(5, "1404/08/20", FEES, "کارمزد", 20_000 + by, 0),
        _row(5, "1404/08/20", BANK, "کارمزد", 0, 20_000 + by, number=False, date=False),
    ])


def _year_1405() -> bytes:
    return _book([
        _row(1, "1405/01/06", BANK, "واریز شهریه", 300_000_000, 0),
        _row(1, "1405/01/06", TUITION, "واریز شهریه", 0, 300_000_000, number=False, date=False),
        _row(2, "1405/02/11", SUPPLIES, "ملزومات", 2_000_000, 0),
        _row(2, "1405/02/11", BANK, "ملزومات", 0, 2_000_000, number=False, date=False),
    ])


def _drop(api, files, message=SCHOOL):
    ids = []
    for name, data in files:
        r = api.post("/transactions/attachments", files={"file": (name, io.BytesIO(data), XLSX)})
        assert r.status_code in (200, 201), r.text
        ids.append(r.json()["id"])
    r = api.post("/ai-accountant/chat", json={"message": message, "attachment_ids": ids})
    assert r.status_code == 200, r.text
    return r.json()


def _apply(api, f, session_id=None):
    r = api.post("/migration/journals/apply", json={"token": f["token"], "file_name": f["file_name"],
                                                   "session_id": session_id}, headers={"X-UI-Language": "fa"})
    assert r.status_code == 200, r.text
    return r.json()


def _search(db, cid, query):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    with use_company(cid):
        return asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u", username="owner"),
                                                SearchAccountsInput(query=query, limit=10)))


# --- AH1, AH11: both years, read right, said once -------------------------------------------------------------

def test_ah1_ah11_both_years_each_its_card_in_the_same_words(co):
    body = _drop(co["api"], [("manadi-1404.xlsx", _year_1404()), ("manadi-1405.xlsx", _year_1405())])
    intake = body["intake"]
    assert intake["kind"] == "journals"
    first, second = intake["files"]
    assert (first["file_name"], first["voucher_count"], first["from"], first["to"]) == \
        ("manadi-1404.xlsx", 5, "2025-04-18", "2025-11-11")                      # ۲۹ فروردین to ۲۰ آبان ۱۴۰۴
    assert (second["file_name"], second["voucher_count"], second["from"]) == ("manadi-1405.xlsx", 2, "2026-03-26")
    assert first["ready"] == 5 and first["unplaced_accounts"] == 0
    # one line said in the chat, the same words on the cards, in Persian with Persian digits
    assert body["text"].startswith("به نظر فهرست سندهاست: \u2068manadi-1404.xlsx\u2069 با ۵ سند از ۲۹ فروردین ۱۴۰۴ تا "
                                   "۲۰ آبان ۱۴۰۴؛ \u2068manadi-1405.xlsx\u2069 با ۲ سند از ۶ فروردین ۱۴۰۵"), body["text"]
    assert first["said"] in body["text"]
    assert any(n.startswith("۵ سند آمادهٔ ورود") for n in first["notes"])
    fa = (ROOT / "app/static/js/i18n/fa.js").read_text(encoding="utf-8")
    assert 'chatIntakeTxnTitle: "به نظر فهرست سندهاست"' in fa


# --- AH2: imported from the card, whole, and said in the chat -------------------------------------------------

def test_ah2_the_card_imports_every_voucher_whole_and_says_so_in_the_chat(co, db):
    body = _drop(co["api"], [("manadi-1404.xlsx", _year_1404(by=7))])
    [f] = body["intake"]["files"]
    out = _apply(co["api"], f, session_id=body["session_id"])
    assert out["posted"] == 5
    assert out["said"] == "۵ سند از \u2068manadi-1404.xlsx\u2069 وارد دفاتر شد، از ۲۹ فروردین ۱۴۰۴ تا ۲۰ آبان ۱۴۰۴."
    cid = str(co["company"].id)
    with use_company(cid):
        db.expire_all()
        txns = db.execute(select(Transaction).where(Transaction.reference.like("IMPORT-%"),
                                                    Transaction.deleted_at.is_(None))).scalars().all()
        assert sorted(t.date for t in txns) == [date(2025, 4, 18), date(2025, 4, 23), date(2025, 8, 1),
                                                date(2025, 9, 23), date(2025, 11, 11)]
        for t in txns:                                                       # whole: every line, balanced
            lines = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == t.id)).scalars().all()
            assert len(lines) == 2 and sum(ln.debit for ln in lines) == sum(ln.credit for ln in lines)
        said = [m.content.get("text") for m in db.execute(select(AIChatMessage).where(
            AIChatMessage.session_id == uuid.UUID(body["session_id"]))).scalars()]
    assert out["said"] in said                                               # the assistant knows (AH2)
    # the same file again: nothing twice
    again = _drop(co["api"], [("manadi-1404.xlsx", _year_1404(by=7))])["intake"]["files"][0]
    assert again["ready"] == 0 and again["counts"] == {"already_imported": 5}


# --- AH3: accounts to match first --------------------------------------------------------------------------

def test_ah3_an_account_it_cant_place_sends_the_card_to_the_journal_page(co):
    odd = (8800, "طرح ویژه", 880001, "طرح ویژه آزمون")
    data = _book([_row(1, "1404/03/01", odd, "طرح", 1_000, 0),
                  _row(1, "1404/03/01", BANK, "طرح", 0, 1_000, number=False, date=False)])
    [f] = _drop(co["api"], [("odd.xlsx", data)])["intake"]["files"]
    assert f["unplaced_accounts"] == 1 and f["ready"] == 0
    assert any("حساب معادل‌سازی‌نشده" in n for n in f["notes"])
    # the journal page reviews the same upload: nothing to choose again
    r = co["api"].post("/migration/journals/review", json={"token": f["token"]})
    assert r.status_code == 200 and r.json()["counts"] == {"unmapped_account": 1}


# --- AH4: never part of a voucher ---------------------------------------------------------------------------

def test_ah4_the_old_excel_import_posts_a_voucher_whole_or_not_at_all(auth_client, db):
    from tests.test_chat_sessions_intake import TXN_CSV
    wb = Workbook()
    for line in TXN_CSV.decode("utf-8").strip().splitlines():
        wb.active.append([int(c) if c.isdigit() else c for c in line.split(",")])
    data = io.BytesIO()
    wb.save(data)
    r = auth_client.post("/transactions/excel-import/preview", files={"file": ("t.xlsx", data.getvalue(), XLSX)})
    assert r.status_code == 200, r.text
    p = r.json()
    one = next(a for a in p["unique_accounts"] if a["title3"] == "اینترنت")   # the bank side left unmatched
    before = db.execute(select(Transaction).where(Transaction.reference == "V1")).scalars().all()
    c = auth_client.post("/transactions/excel-import/confirm", json={
        "file_token": p["file_token"], "jalali_year": p["jalali_year"], "amount_multiplier": 1, "currency": "IRR",
        "account_mappings": [{"title1": one["title1"], "title2": one["title2"], "title3": one["title3"],
                              "account_code": "6112"}]})
    assert c.status_code == 200, c.text
    assert c.json()["imported"] == 0                                          # not the half that matched
    db.expire_all()
    assert len(db.execute(select(Transaction).where(Transaction.reference == "V1")).scalars().all()) == len(before)


# --- AH5: the misread vouchers, listed, removed on the user's word ------------------------------------------

def test_ah5_misread_vouchers_are_listed_and_removed_only_when_confirmed(co, db):
    cid = str(co["company"].id)
    with use_company(cid):
        tuition = db.execute(select(Account).where(Account.code == "4110")).scalars().one()
        bank = db.execute(select(Account).where(Account.code == "1110")).scalars().one()
        bad = Transaction(date=date(2027, 1, 30), reference="V1404/01/29", description="واریز شهریه", currency="IRR")
        good = Transaction(date=date(2025, 4, 23), reference="V1404/02/03", description="درست", currency="IRR")
        db.add_all([bad, good])
        db.flush()
        db.add(TransactionLine(transaction_id=bad.id, account_id=tuition.id, debit=0, credit=200_000_000))   # one-sided
        db.add_all([TransactionLine(transaction_id=good.id, account_id=bank.id, debit=5, credit=0),
                    TransactionLine(transaction_id=good.id, account_id=tuition.id, debit=0, credit=5)])
        db.commit()
        bad_id, good_id = str(bad.id), str(good.id)
    found = co["api"].get("/migration/journals/repairs").json()
    assert [v["id"] for v in found["vouchers"]] == [bad_id]                  # not the one on its own day
    assert found["vouchers"][0] == {"id": bad_id, "reference": "V1404/01/29", "date": "2027-01-30", "debit": 0,
                                    "credit": 200_000_000, "lines": 1, "currency": "IRR"}
    # a list can't reach beyond what was found: the good one stays
    r = co["api"].post("/migration/journals/repairs", json={"vouchers": [bad_id, good_id], "headings": []})
    assert r.status_code == 200 and r.json() == {"removed": 1, "merged": 0}
    with use_company(cid):
        db.expire_all()
        assert db.get(Transaction, uuid.UUID(bad_id)).deleted_at is not None
        assert db.get(Transaction, uuid.UUID(good_id)).deleted_at is None
        audit = db.execute(select(AuditLog).where(AuditLog.entity_id == bad_id, AuditLog.action == "delete")).scalars().one()
        assert json.loads(audit.detail)["reference"] == "V1404/01/29"
    assert co["api"].get("/migration/journals/repairs").json() == {"vouchers": [], "headings": []}


# --- AH6: one heading per ledger ---------------------------------------------------------------------------

def test_ah6_a_ledger_is_one_heading_and_the_twins_an_import_made_are_merged_on_word(co, db):
    [f] = _drop(co["api"], [("manadi-1404.xlsx", _year_1404(by=11))])["intake"]["files"]
    _apply(co["api"], f)
    cid = str(co["company"].id)
    with use_company(cid):
        db.expire_all()
        heads = db.execute(select(Account).where(Account.name == "مخارج مدرسه")).scalars().all()
        assert len(heads) == 1                                                # its bank fees under the same one
        kids = {a.name for a in db.execute(select(Account).where(Account.parent_id == heads[0].id)).scalars()}
        assert kids == {"کارمزد بانکی", "ملزومات اداری"}
        assert heads[0].code.startswith("61")                                 # placed by its own title

        # books imported before: the same ledger as two headings
        from app.services import journal_import as ji
        from app.services.chart_service import create_account
        a = create_account(db, name="هزینه اردو", parent_code="61")
        a_kid = create_account(db, name="اتوبوس", parent_code=a.code)
        b = create_account(db, name="هزینه اردو", parent_code="62")
        b_kid = create_account(db, name="ورودی", parent_code=b.code)
        ji.save_map(db, {"3101|اتوبوس": a_kid.code, "3102|ورودی": b_kid.code})
        a.source_codes, b.source_codes = "3100", "3109"
        db.commit()
        a_code, b_code, b_kid_code = a.code, b.code, b_kid.code
    found = co["api"].get("/migration/journals/repairs").json()["headings"]
    assert found == [{"name": "هزینه اردو", "keep": a_code, "merge": [{"code": b_code, "accounts": [b_kid_code]}]}]
    # a file dropped in the chat says so, and points to where it's merged
    body = _drop(co["api"], [("again.xlsx", _year_1405())])
    notes = body["intake"]["files"][0]["notes"]
    assert any("هزینه اردو" in n and "را دو بار ساخته" in n for n in notes), notes
    assert body["intake"]["repairs"] == 1
    r = co["api"].post("/migration/journals/repairs", json={"vouchers": [], "headings": [b_code]})
    assert r.json() == {"removed": 0, "merged": 1}
    with use_company(cid):
        db.expire_all()
        keep = db.execute(select(Account).where(Account.code == a_code)).scalars().one()
        moved = db.execute(select(Account).where(Account.code == b_kid_code)).scalars().one()
        gone = db.execute(select(Account).where(Account.code == b_code)).scalars().one()
        assert moved.parent_id == keep.id and gone.is_active is False
        assert keep.source_codes == "3100,3109"


# --- AH7, AH8, AH10: my own codes; a heading isn't offered; one code, several accounts ----------------------

def test_ah7_ah8_the_schools_own_codes_find_its_accounts_and_cards_show_both(co, db):
    [f] = _drop(co["api"], [("manadi-1404.xlsx", _year_1404(by=13))])["intake"]["files"]
    _apply(co["api"], f)
    cid = str(co["company"].id)
    with use_company(cid):
        db.expire_all()
        supplies = db.execute(select(Account).where(Account.name == "ملزومات اداری")).scalars().one()
        heading = db.execute(select(Account).where(Account.name == "مخارج مدرسه")).scalars().one()
        assert supplies.source_codes == "1302" and heading.source_codes == "1300"
        supplies_code = supplies.code
    # «۱۳۰۲»: the account it became, first
    hit = _search(db, cid, "1302")["matches"][0]
    assert (hit["code"], hit["old_codes"]) == (supplies_code, ["1302"])
    # «۱۳۰۰» is a heading: its accounts are offered, never it, and the model is told so
    out = _search(db, cid, "1300")
    assert {m["name"] for m in out["matches"][:2]} == {"ملزومات اداری", "کارمزد بانکی"}
    assert all(m.get("under") == "مخارج مدرسه" for m in out["matches"][:2])
    assert "is a heading: it takes no entries" in out["heading_note"] and "1300" in out["heading_note"]
    assert all(m["code"] != heading.code for m in out["matches"])
    # the chart shows and finds them
    tree = co["api"].get("/accounts/tree").json()["accounts"]

    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n["children"])
    assert next(n for n in walk(tree) if n["code"] == supplies_code)["source_codes"] == ["1302"]
    # a card with the user's own code: the account it became, both codes on its line
    from app.services.ai_accountant.blocks import proposal_block
    with use_company(cid):
        block = proposal_block(db, {"preview": {"lines": [{"account_code": supplies_code, "debit": 5, "credit": 0}]},
                                    "summary": "x"}, calendar="jalali", lang="fa")
    assert block["lines"][0]["name"] == "ملزومات اداری (۱۳۰۲)"
    from app.services.account_resolver import account_by_source_code
    with use_company(cid):
        assert account_by_source_code(db, "1302").code == supplies_code
        assert account_by_source_code(db, "1300") is None                     # a heading's: never posted to


def test_ah10_one_code_for_several_accounts_is_said(co):
    shared = [(1300, "مخارج مدرسه", 1305, "قبوض"), (1300, "مخارج مدرسه", 1305, "کارمزد بانکی")]
    data = _book([_row(1, "1404/03/01", shared[0], "قبض", 1_000, 0),
                  _row(1, "1404/03/01", BANK, "قبض", 0, 1_000, number=False, date=False),
                  _row(2, "1404/03/02", shared[1], "کارمزد", 50, 0),
                  _row(2, "1404/03/02", BANK, "کارمزد", 0, 50, number=False, date=False)])
    [f] = _drop(co["api"], [("1305.xlsx", data)])["intake"]["files"]
    assert f["codes_reused"] == [{"code": "1305", "names": ["قبوض", "کارمزد بانکی"]}]
    assert "در فایل، کد ۱۳۰۵ به ۲ حساب داده شده است: قبوض، کارمزد بانکی" in f["notes"]


# --- AH9: options are chips --------------------------------------------------------------------------------

def test_ah9_options_typed_as_a_list_become_chips():
    from app.services.ai_accountant.orchestrator import _choices_as_chips
    calls: list = []
    text = ("برای ثبت، حساب «مخارج مدرسه» با دو کد مشابه پیدا شد. لطفاً یکی را انتخاب کنید:\n"
            "• ۶۲۱۱ «مخارج مدرسه»\n• ۶۱۳۲ «مخارج مدرسه»")
    out = _choices_as_chips(text, calls)
    assert out == "برای ثبت، حساب «مخارج مدرسه» با دو کد مشابه پیدا شد. لطفاً یکی را انتخاب کنید:"
    assert calls[0]["name"] == "ask_with_choices"
    assert calls[0]["result"]["options"] == ["۶۲۱۱ «مخارج مدرسه»", "۶۱۳۲ «مخارج مدرسه»"]
    # a list of facts after a colon stays as it is
    facts = "Your biggest expenses this month:\n- Rent 500\n- Food 300"
    assert _choices_as_chips(facts, []) == facts
    # the model asked with buttons already: nothing added
    asked = [{"name": "ask_with_choices", "result": {"options": ["a", "b"]}}]
    assert _choices_as_chips("Which one?\n- a\n- b", asked) == "Which one?\n- a\n- b" and len(asked) == 1


# --- the codes of books imported before ----------------------------------------------------------------------

def test_migration_080_gives_imported_accounts_their_own_codes_once(co, db):
    cid = str(co["company"].id)
    with use_company(cid):
        from app.services import journal_import as ji
        from app.services.chart_service import create_account
        acc = create_account(db, name="ملزومات قدیمی", parent_code="61")
        ji.save_map(db, {"1302|ملزومات قدیمی": acc.code, "|بی کد": acc.code})
        db.commit()
        code = acc.code
    spec = importlib.util.spec_from_file_location("m080", ROOT / "alembic/versions/080_account_source_codes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    for _ in range(2):                                                       # safe to run twice
        with db.get_bind().begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
    with use_company(cid):
        db.expire_all()
        assert db.execute(select(Account).where(Account.code == code)).scalars().one().source_codes == "1302"

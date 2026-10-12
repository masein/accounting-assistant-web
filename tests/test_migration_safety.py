"""A Sepidar migration keeps the books it made (scenarios O1–O4, 2026-10-12).

A real company's import was right at the trial balance, then fragile: each
bank took the next free 111x code, a ledger code in Sepidar's numbering, so
last year's chart renamed a bank's account «سایر حساب‌ها و اسناد دریافتنی»;
and importing last year's chart to add its accounts put last year's opening
balances over this year's without a word. Made-up companies with the same
shape, in codes no other test uses (the test database is shared)."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.account import Account, AccountLevel
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from app.services import migration_import as mig
from tests.test_migration_import import HEADER_4, HEADER_TAFSILI, _ss_xml, _upload


def _chart(group, kols, moeins, tafsili):
    return [
        ("گروه.xls", _ss_xml([HEADER_4, *group])),
        ("كل.xls", _ss_xml([HEADER_4, *kols])),
        ("معين.xls", _ss_xml([HEADER_4, *moeins])),
        ("تفصيلي.xls", _ss_xml([HEADER_TAFSILI, *tafsili])),
    ]


def _this_year(bank_name="بانك نمونه سپيدار 0101234567001"):
    """Bank sub-ledger 761005 holds 500; deposits held at banks (761401, also
    «بانک» in its name) hold more, and must not be taken for it."""
    return _chart(
        [["76", "دارايي آزمون سپيدار", "0", "0", "1500.0000", "0.0000"],
         ["77", "بدهي آزمون سپيدار", "0", "0", "0.0000", "1500.0000"]],
        [["7610", "موجودي نقد و بانك آزمون", "0", "0", "500.0000", "0.0000"],
         ["7614", "سرمايه گذاري كوتاه مدت آزمون", "0", "0", "1000.0000", "0.0000"],
         ["7711", "ساير پرداختني آزمون", "0", "0", "0.0000", "1500.0000"]],
        [["761005", "موجودي بانكهاي ريالي آزمون", "0", "0", "500.0000", "0.0000"],
         ["761401", "سپرده هاي كوتاه مدت نزد بانكها آزمون", "0", "0", "1000.0000", "0.0000"],
         ["771101", "ساير حسابهاي پرداختني آزمون", "0", "0", "0.0000", "1500.0000"]],
        [["931", bank_name, "حساب بانكي", "0", "0", "500.0000", "0.0000"]],
    )


def _confirm(auth_client, files, **extra):
    r = auth_client.post("/migration/import/preview", files=_upload(files))
    assert r.status_code == 200, r.text
    c = auth_client.post("/migration/import/confirm", json={"token": r.json()["token"], **extra})
    assert c.status_code == 200, c.text
    return r.json(), c.json()["result"]


def _lines_by_code(db, txn_id):
    out: dict[str, int] = {}
    for line in db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txn_id)).scalars():
        code = db.get(Account, line.account_id).code
        out[code] = out.get(code, 0) + line.debit - line.credit
    return out


def test_o1_a_bank_gets_a_detail_account_under_the_bank_sub_ledger(auth_client, db):
    _, result = _confirm(auth_client, _this_year(), opening="replace")

    bank = db.execute(select(Entity).where(Entity.type == "bank", Entity.account_number == "0101234567001")).scalars().one()
    assert bank.code == "761005931"                    # sub-ledger + the bank's detail code
    acc = db.execute(select(Account).where(Account.code == bank.code)).scalars().one()
    assert acc.level == AccountLevel.DETAIL and acc.parent.code == "761005"

    oj = result["opening_journal"]
    assert oj["bank_split_moein"] == "761005"           # cash at the bank, not deposits held at banks
    by_code = _lines_by_code(db, uuid.UUID(oj["transaction_id"]))
    assert by_code["761005931"] == 500 and "761005" not in by_code
    assert by_code["761401"] == 1000


def test_o2_last_years_chart_never_renames_a_banks_account(auth_client, db):
    # A company migrated before the fix: its bank sits on a ledger-shaped code.
    acc = Account(code="7813", name="حساب بانکی بانک قدیمی آزمون", level=AccountLevel.GENERAL)
    db.add(acc)
    db.add(Entity(type="bank", name="بانک قدیمی آزمون", code="7813"))
    db.commit()
    older = _chart(
        [["78", "دارايي سال قبل آزمون", "0", "0", "0.0000", "0.0000"]],
        [["7813", "ساير حسابها و اسناد دريافتني آزمون", "0", "0", "0.0000", "0.0000"]],
        [["781304", "ساير حسابهاي دريافتني آزمون", "0", "0", "0.0000", "0.0000"]],
        [["932", "طرف مقابل سال قبل آزمون", "طرف مقابل", "0", "0", "0.0000", "0.0000"]],
    )
    preview, result = _confirm(auth_client, older, opening="keep")

    s = preview["summary"]
    assert [c["code"] for c in s["conflicts"]] == ["7813"]
    assert any(w.startswith("Code 7813 is the account of the bank «بانک قدیمی آزمون»") for w in s["validation"]["warnings"])
    assert [c["code"] for c in result["chart"]["conflicts"]] == ["7813"]

    db.expire_all()
    kept = db.execute(select(Account).where(Account.code == "7813")).scalars().one()
    assert kept.name == "حساب بانکی بانک قدیمی آزمون"     # not «سایر حساب‌ها و اسناد دریافتنی»
    sub = db.execute(select(Account).where(Account.code == "781304")).scalars().one()
    assert sub.parent.code == "78"                       # under the group, not under the bank


def test_o3_opening_balances_already_in_the_books_are_kept_unless_replaced(auth_client, db):
    this_year, _ = _confirm(auth_client, _this_year("بانك نمونه دوم 0101234567002"), opening="replace")
    live = mig.live_opening(db)
    assert live is not None and mig.opening_total(db, live) == 1500

    last_year = _chart(
        [["79", "دارايي سال گذشته آزمون", "0", "0", "800.0000", "0.0000"],
         ["80", "بدهي سال گذشته آزمون", "0", "0", "0.0000", "800.0000"]],
        [["7911", "حساب جديد سال گذشته آزمون", "0", "0", "800.0000", "0.0000"],
         ["8011", "پرداختني سال گذشته آزمون", "0", "0", "0.0000", "800.0000"]],
        [["791101", "معين جديد سال گذشته آزمون", "0", "0", "800.0000", "0.0000"],
         ["801101", "معين پرداختني سال گذشته آزمون", "0", "0", "0.0000", "800.0000"]],
        [["933", "طرف مقابل سال گذشته آزمون", "طرف مقابل", "0", "0", "800.0000", "0.0000"]],
    )
    r = auth_client.post("/migration/import/preview", files=_upload(last_year))
    opening = r.json()["summary"]["opening"]
    assert opening["existing"] == {"date": live.date.isoformat(), "total": 1500}
    assert opening["suggested"] == "keep"
    assert any(w.startswith("The books already hold opening balances of 1,500") for w in r.json()["summary"]["validation"]["warnings"])

    # The page (or the chat) says nothing: the books' balances stay.
    c = auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    oj = c.json()["result"]["opening_journal"]
    assert oj["kept"] is True and oj["transaction_id"] == str(live.id)
    db.expire_all()
    assert mig.live_opening(db).id == live.id
    assert db.execute(select(Account).where(Account.code == "791101")).scalars().one()   # the chart came in
    assert db.execute(select(Entity).where(Entity.name == "طرف مقابل سال گذشته آزمون")).scalars().one()

    # Asked to replace: replaced, as before.
    r2 = auth_client.post("/migration/import/preview", files=_upload(last_year))
    assert r2.json()["summary"]["opening"]["suggested"] == "keep"
    c2 = auth_client.post("/migration/import/confirm", json={"token": r2.json()["token"], "opening": "replace"})
    oj2 = c2.json()["result"]["opening_journal"]
    assert oj2["replaced_previous"] is True
    db.expire_all()
    replaced = mig.live_opening(db)
    assert str(replaced.id) == oj2["transaction_id"] and mig.opening_total(db, replaced) == 800
    assert db.get(Transaction, live.id).deleted_at is not None

    # The same files again: their own opening, so replacing is the default and nothing doubles.
    r3 = auth_client.post("/migration/import/preview", files=_upload(last_year))
    assert r3.json()["summary"]["opening"]["suggested"] == "replace"
    c3 = auth_client.post("/migration/import/confirm", json={"token": r3.json()["token"]})
    assert "kept" not in c3.json()["result"]["opening_journal"]
    db.expire_all()
    assert len(db.execute(select(Transaction).where(
        Transaction.reference == mig.OPENING_REFERENCE, Transaction.deleted_at.is_(None))).scalars().all()) == 1

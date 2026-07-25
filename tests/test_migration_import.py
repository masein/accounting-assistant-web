"""Migration from another accounting system: parser + preview→confirm + queue.

Uses both synthetic SpreadsheetML fixtures and the real 4-file sample export in
``samples/migration`` (حساب گروه / كل / معين / تفصيلي).
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.account import Account, AccountLevel
from app.models.entity import Entity
from app.models.migration import MigrationBatch, MigrationPendingRecord
from app.models.transaction import Transaction, TransactionLine
from app.services import migration_import as mig

# Real 4-file export (gitignored with the rest of samples/ — real data stays
# out of the repo). Tests that need it skip when it's absent; the synthetic
# fixtures below cover the same paths for CI.
SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "migration"
_HAS_SAMPLES = SAMPLES.is_dir() and len(list(SAMPLES.glob("*.xls"))) == 4
needs_samples = pytest.mark.skipif(not _HAS_SAMPLES, reason="real sample export not present")

SS_HEADER = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet" '
    'xmlns="urn:schemas-microsoft-com:office:spreadsheet">'
)


def _ss_xml(rows: list[list[str]]) -> bytes:
    cells = "".join(
        "<Row>" + "".join(f"<Cell><Data ss:Type=\"String\">{c}</Data></Cell>" for c in row) + "</Row>"
        for row in rows
    )
    xml = f"{SS_HEADER}<Worksheet ss:Name=\"t\"><Table>{cells}</Table></Worksheet></Workbook>"
    return b"\xef\xbb\xbf" + xml.encode("utf-8")


HEADER_4 = ["كد", "عنوان", "گردش بدهكار", "گردش بستانكار", "مانده بدهكار", "مانده بستانكار"]
HEADER_TAFSILI = ["كد", "عنوان", "نوع تفصيلي", "گردش بدهكار", "گردش بستانكار", "مانده بدهكار", "مانده بستانكار"]


def _upload(files: list[tuple[str, bytes]]):
    return [("files", (name, io.BytesIO(data), "application/vnd.ms-excel")) for name, data in files]


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

def test_parse_amount_variants():
    assert mig.parse_amount("42899121632.0000") == 42899121632
    assert mig.parse_amount("۱۲۳۴۵.0000") == 12345
    assert mig.parse_amount("12,345.0000") == 12345
    assert mig.parse_amount("12345.6789.0000") == 123456789  # dots as separators
    assert mig.parse_amount("") == 0
    assert mig.parse_amount(None) == 0
    assert mig.parse_amount("-500.0000") == -500


def test_normalize_fa_unifies_arabic_letterforms():
    assert mig.normalize_fa("حساب بانكي") == "حساب بانکی"
    assert mig.normalize_fa("  داراييهاي   جاري ") == "داراییهای جاری"


def test_extract_rows_spreadsheetml_skips_totals_row():
    data = _ss_xml([
        HEADER_4,
        ["11", "داراييهاي جاري", "1.0000", "0.0000", "100.0000", "0.0000"],
        ["", "", "1.0000", "0.0000", "100.0000", "0.0000"],  # totals row: no code
    ])
    rows = mig.extract_rows("گروه.xls", data)
    assert len(rows) == 1
    assert rows[0]["code"] == "11"
    assert rows[0]["title"] == "داراییهای جاری"
    assert rows[0]["balance_debit"] == 100


def test_extract_rows_csv():
    csv_data = "﻿كد,عنوان,مانده بدهكار,مانده بستانكار\n1110,موجودي نقد,500.0000,0\n".encode("utf-8")
    rows = mig.extract_rows("kol.csv", csv_data)
    assert rows == [{
        "code": "1110", "title": "موجودی نقد", "detail_type": None, "parent_code": None,
        "balance_debit": 500, "balance_credit": 0,
    }]


def test_classify_rows_by_code_shape_and_detail_type():
    assert mig.classify_rows([{"code": "11", "detail_type": None}]) == "group"
    assert mig.classify_rows([{"code": "1110", "detail_type": None}]) == "kol"
    assert mig.classify_rows([{"code": "211109", "detail_type": None}]) == "moein"
    assert mig.classify_rows([{"code": "001", "detail_type": "حساب بانکی"}]) == "tafsili"


def test_bank_account_number_extraction():
    assert mig.bank_account_number("آینده جردن 0101790457004") == "0101790457004"
    assert mig.strip_account_number("آینده جردن 0101790457004") == "آینده جردن"
    assert mig.bank_account_number("بانک بدون شماره") is None


def test_infer_counterparty_type():
    moein = {
        "111201": {"code": "111201", "title": "حسابهای دریافتنی تجاری"},
        "211002": {"code": "211002", "title": "اسناد پرداختنی ریالی"},
    }
    client_row = {"code": "10001", "title": "x", "parent_code": "111201"}
    supplier_row = {"code": "20001", "title": "y", "parent_code": "211002"}
    orphan_row = {"code": "30001", "title": "z", "parent_code": None}
    assert mig.infer_counterparty_type(client_row, moein) == ("client", False)
    assert mig.infer_counterparty_type(supplier_row, moein) == ("supplier", False)
    assert mig.infer_counterparty_type(orphan_row, moein) == ("client", True)


@needs_samples
def test_real_sample_files_parse():
    files = sorted(SAMPLES.glob("*.xls"))
    assert len(files) == 4
    tiers = {}
    for p in files:
        rows = mig.extract_rows(p.name, p.read_bytes())
        tiers[mig.classify_rows(rows)] = rows
    assert set(tiers) == {"group", "kol", "moein", "tafsili"}
    assert len(tiers["group"]) == 6
    assert len(tiers["kol"]) == 11
    assert len(tiers["moein"]) == 26
    banks = [r for r in tiers["tafsili"] if mig.is_bank_row(r)]
    assert len(banks) == 2
    assert sum(r["balance_debit"] for r in tiers["moein"]) == sum(
        r["balance_credit"] for r in tiers["moein"]
    )


# ---------------------------------------------------------------------------
# Preview → confirm flow (real sample files)
# ---------------------------------------------------------------------------

def _sample_uploads():
    return _upload([(p.name, p.read_bytes()) for p in sorted(SAMPLES.glob("*.xls"))])


@needs_samples
def test_preview_real_samples(auth_client):
    resp = auth_client.post("/migration/import/preview", files=_sample_uploads())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    s = body["summary"]
    assert s["tiers"] == {"group": 6, "kol": 11, "moein": 26, "tafsili": 11}
    assert s["tafsili_split"] == {"bank_accounts": 2, "counterparties": 9}
    assert s["opening"]["basis"] == "moein"
    assert s["opening"]["balanced"] is True
    assert s["opening"]["total_debit"] == s["opening"]["total_credit"] == 34484045964
    assert s["validation"]["errors"] == []
    assert body["token"]
    banks = {b["account_number"] for b in s["banks"]}
    assert banks == {"0101790457004", "0106881965003"}


@needs_samples
def test_confirm_applies_chart_entities_and_journal(auth_client, db):
    resp = auth_client.post("/migration/import/preview", files=_sample_uploads())
    token = resp.json()["token"]

    resp = auth_client.post("/migration/import/confirm", json={"token": token})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]

    # Chart: moein tier is new (seed has no 6-digit SUB accounts)
    assert result["chart"]["moein"]["created"] == 26
    moein = db.execute(select(Account).where(Account.code == "211109")).scalars().one()
    assert moein.level == AccountLevel.SUB
    assert moein.parent is not None and moein.parent.code == "2111"
    kol = db.execute(select(Account).where(Account.code == "2111")).scalars().one()
    assert kol.parent is not None and kol.parent.code == "21"

    # Entities: 2 banks with GL cash accounts + extracted account numbers
    bank = db.execute(
        select(Entity).where(Entity.type == "bank", Entity.account_number == "0101790457004")
    ).scalars().one()
    assert bank.code, "bank entity must be linked to a GL cash account"
    gl = db.execute(select(Account).where(Account.code == bank.code)).scalars().first()
    assert gl is not None
    assert result["entities"]["banks_created"] == 2
    assert result["entities"]["counterparties_created"] == 9

    # Counterparties default to client + review flag (sample has no معین link)
    cp = db.execute(select(Entity).where(Entity.name == "ابر اروان")).scalars().one()
    assert cp.type == "client"

    # Opening journal: balanced, dated, bank معین split into per-bank GL lines
    txn_id = uuid.UUID(result["opening_journal"]["transaction_id"])
    lines = db.execute(
        select(TransactionLine).join(Transaction).where(Transaction.id == txn_id)
    ).scalars().all()
    total_debit = sum(l.debit for l in lines)
    total_credit = sum(l.credit for l in lines)
    assert total_debit == total_credit == 34484045964
    assert result["opening_journal"]["bank_split"] is True
    assert result["opening_journal"].get("suspense_amount") is None
    by_code = {}
    for l in lines:
        acc = db.get(Account, l.account_id)
        by_code[acc.code] = by_code.get(acc.code, 0) + l.debit - l.credit
    assert by_code[bank.code] == 282242080          # آینده جردن
    assert "111005" not in by_code                   # fully split into bank GLs

    # Completion queue: every imported entity is missing address/phone or iban
    pending = auth_client.get("/migration/pending").json()
    assert len(pending) >= 11
    flagged = [p for p in pending if "type_ambiguous" in p["review_flags"]]
    assert len(flagged) == 9


@needs_samples
def test_reimport_is_idempotent(auth_client, db):
    files = _sample_uploads()
    r1 = auth_client.post("/migration/import/preview", files=files)
    auth_client.post("/migration/import/confirm", json={"token": r1.json()["token"]})

    counts_before = {
        "accounts": len(db.execute(select(Account)).scalars().all()),
        "entities": len(db.execute(select(Entity)).scalars().all()),
        "open_txns": len(db.execute(
            select(Transaction).where(
                Transaction.reference == mig.OPENING_REFERENCE,
                Transaction.deleted_at.is_(None),
            )
        ).scalars().all()),
    }
    assert counts_before["open_txns"] == 1

    r2 = auth_client.post("/migration/import/preview", files=_sample_uploads())
    assert r2.json()["already_applied"] is True
    r3 = auth_client.post("/migration/import/confirm", json={"token": r2.json()["token"]})
    assert r3.status_code == 200
    assert r3.json()["result"]["opening_journal"]["replaced_previous"] is True

    counts_after = {
        "accounts": len(db.execute(select(Account)).scalars().all()),
        "entities": len(db.execute(select(Entity)).scalars().all()),
        "open_txns": len(db.execute(
            select(Transaction).where(
                Transaction.reference == mig.OPENING_REFERENCE,
                Transaction.deleted_at.is_(None),
            )
        ).scalars().all()),
    }
    assert counts_after == counts_before  # no duplicates anywhere


@needs_samples
def test_confirm_twice_same_batch_is_idempotent(auth_client):
    r1 = auth_client.post("/migration/import/preview", files=_sample_uploads())
    token = r1.json()["token"]
    c1 = auth_client.post("/migration/import/confirm", json={"token": token})
    c2 = auth_client.post("/migration/import/confirm", json={"token": token})
    assert c2.status_code == 200
    assert c2.json()["idempotent"] is True
    assert c2.json()["result"] == c1.json()["result"]


def test_unbalanced_source_routes_to_suspense(auth_client, db):
    kol_file = _ss_xml([
        HEADER_4,
        ["7710", "حساب آزمايشي بدهكار", "0.0000", "0.0000", "1000.0000", "0.0000"],
        ["7720", "حساب آزمايشي بستانكار", "0.0000", "0.0000", "0.0000", "300.0000"],
    ])
    r = auth_client.post("/migration/import/preview", files=_upload([("kol.xls", kol_file)]))
    assert r.status_code == 200
    s = r.json()["summary"]
    assert s["opening"]["balanced"] is False
    assert s["opening"]["suspense_needed"] is True
    assert s["opening"]["difference"] == 700

    c = auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    assert c.status_code == 200, c.text
    oj = c.json()["result"]["opening_journal"]
    assert oj["suspense_amount"] == 700
    lines = db.execute(
        select(TransactionLine).join(Transaction).where(Transaction.id == uuid.UUID(oj["transaction_id"]))
    ).scalars().all()
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 1000


def test_preview_with_blocking_error_cannot_confirm(auth_client):
    dup = _ss_xml([
        HEADER_4,
        ["7730", "الف", "0", "0", "10.0000", "0"],
        ["7730", "ب", "0", "0", "5.0000", "0"],
    ])
    r = auth_client.post("/migration/import/preview", files=_upload([("kol.xls", dup)]))
    assert r.status_code == 200
    assert r.json()["summary"]["validation"]["errors"]
    c = auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    assert c.status_code == 400


def test_pending_resolve_requires_fields_filled(auth_client, db):
    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["40001", "مشتري نمونه رزولو", "طرف مقابل", "0", "0", "50.0000", "0"],
    ])
    kol_file = _ss_xml([
        HEADER_4,
        ["7740", "حساب موازنه رزولو", "0", "0", "0.0000", "50.0000"],
        ["7750", "حساب مقابل رزولو", "0", "0", "50.0000", "0.0000"],
    ])
    r = auth_client.post(
        "/migration/import/preview",
        files=_upload([("tafsili.xls", tafsili), ("kol.xls", kol_file)]),
    )
    auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})

    pending = auth_client.get("/migration/pending").json()
    rec = next(p for p in pending if p["entity_name"] == "مشتری نمونه رزولو")
    assert set(rec["missing_fields"]) == {"address", "phone"}

    # can't resolve while fields are missing
    resp = auth_client.post(f"/migration/pending/{rec['id']}/resolve")
    assert resp.status_code == 400

    # fill via the entity API (the manual path), then resolve
    upd = auth_client.patch(
        f"/entities/{rec['entity_id']}",
        json={"address": "تهران، خیابان ولیعصر ۱۲", "phone": "021-88001122"},
    )
    assert upd.status_code == 200, upd.text
    resp = auth_client.post(f"/migration/pending/{rec['id']}/resolve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"

    still = auth_client.get("/migration/pending").json()
    assert all(p["id"] != rec["id"] for p in still)


def test_pending_dismiss(auth_client):
    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["40002", "مشتري نمونه ديسميس", "طرف مقابل", "0", "0", "60.0000", "0"],
    ])
    kol_file = _ss_xml([
        HEADER_4,
        ["7760", "حساب موازنه ديسميس", "0", "0", "0.0000", "60.0000"],
        ["7770", "حساب مقابل ديسميس", "0", "0", "60.0000", "0.0000"],
    ])
    r = auth_client.post(
        "/migration/import/preview",
        files=_upload([("tafsili.xls", tafsili), ("kol.xls", kol_file)]),
    )
    auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    pending = auth_client.get("/migration/pending").json()
    rec = next(p for p in pending if p["entity_name"] == "مشتری نمونه دیسمیس")
    resp = auth_client.post(f"/migration/pending/{rec['id']}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"


def test_unknown_token_404(auth_client):
    resp = auth_client.post("/migration/import/confirm", json={"token": "nope"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Synthetic 4-file export (CI-safe mirror of the real sample's shape)
# ---------------------------------------------------------------------------

def _synthetic_export():
    group = _ss_xml([
        HEADER_4,
        ["78", "دارايي آزمايشي", "0", "0", "900.0000", "0.0000"],
        ["79", "بدهي آزمايشي", "0", "0", "0.0000", "900.0000"],
        ["", "", "0", "0", "900.0000", "900.0000"],
    ])
    kol = _ss_xml([
        HEADER_4,
        ["7810", "نقد و بانك آزمايشي", "0", "0", "900.0000", "0.0000"],
        ["7910", "پرداختني آزمايشي", "0", "0", "0.0000", "900.0000"],
    ])
    moein = _ss_xml([
        HEADER_4,
        ["781001", "موجودي بانكهاي آزمايشي", "0", "0", "600.0000", "0.0000"],
        ["781002", "صندوق آزمايشي", "0", "0", "300.0000", "0.0000"],
        ["791001", "حسابهاي پرداختني آزمايشي", "0", "0", "0.0000", "900.0000"],
    ])
    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["901", "بانك آزمون سنتتيك 12345678901", "حساب بانكي", "0", "0", "600.0000", "0.0000"],
        ["902", "طرف مقابل سنتتيك", "طرف مقابل", "0", "0", "0.0000", "900.0000"],
    ])
    return [("گروه.xls", group), ("كل.xls", kol), ("معين.xls", moein), ("تفصيلي.xls", tafsili)]


def test_synthetic_full_flow_chart_bank_split_and_hierarchy(auth_client, db):
    r = auth_client.post("/migration/import/preview", files=_upload(_synthetic_export()))
    assert r.status_code == 200, r.text
    s = r.json()["summary"]
    assert s["tiers"] == {"group": 2, "kol": 2, "moein": 3, "tafsili": 2}
    assert s["tafsili_split"] == {"bank_accounts": 1, "counterparties": 1}
    assert s["opening"]["balanced"] is True

    c = auth_client.post("/migration/import/confirm", json={"token": r.json()["token"]})
    assert c.status_code == 200, c.text
    result = c.json()["result"]

    moein = db.execute(select(Account).where(Account.code == "781001")).scalars().one()
    assert moein.level == AccountLevel.SUB
    assert moein.parent is not None and moein.parent.code == "7810"
    assert moein.parent.parent is not None and moein.parent.parent.code == "78"

    bank = db.execute(
        select(Entity).where(Entity.type == "bank", Entity.account_number == "12345678901")
    ).scalars().one()
    assert bank.code

    oj = result["opening_journal"]
    assert oj["bank_split"] is True and oj["bank_split_moein"] == "781001"
    lines = db.execute(
        select(TransactionLine).join(Transaction).where(Transaction.id == uuid.UUID(oj["transaction_id"]))
    ).scalars().all()
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 900
    by_code = {}
    for l in lines:
        acc = db.get(Account, l.account_id)
        by_code[acc.code] = by_code.get(acc.code, 0) + l.debit - l.credit
    assert by_code[bank.code] == 600      # bank معین fully split to the bank GL
    assert "781001" not in by_code

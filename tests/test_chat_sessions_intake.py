"""AI-chat UX: spreadsheet upload + smart intake (Part A) and ChatGPT-style
sessions (Part B).

Intake never writes silently — chart exports stage the idempotent migration
preview, transaction sheets stage the excel-import preview, both confirmed
through their existing gated endpoints. Path-only messages get a deterministic
"attach the file" reply instead of a hallucinated answer.
"""
from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import select

from app.models.ai_accountant import AIChatMessage, AIChatSession
from app.models.transaction import Transaction, TransactionAttachment

from tests.test_migration_import import HEADER_4, HEADER_TAFSILI, _ss_xml

TXN_CSV = (
    "ردیف,شماره سند,تاریخ,Title 1,Title 2,Title 3,شرح,بدهکار,بستانکار\n"
    "1,1,14040101,سود و زیان,هزینه عملیاتی,اینترنت,هزینه اینترنت دفتر,1500000,0\n"
    "2,1,14040101,دارایی,دارایی جاری,بانک,پرداخت از بانک,0,1500000\n"
).encode("utf-8")


def _upload_attachment(auth_client, name: str, data: bytes, content_type: str):
    resp = auth_client.post(
        "/transactions/attachments",
        files={"file": (name, io.BytesIO(data), content_type)},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _chat(auth_client, message: str, attachment_ids=None, session_id=None):
    return auth_client.post("/ai-accountant/chat", json={
        "message": message,
        "session_id": session_id,
        "attachment_ids": attachment_ids or [],
    })


# ---------------------------------------------------------------------------
# Part A — upload types
# ---------------------------------------------------------------------------

def test_spreadsheet_attachment_types_accepted(auth_client):
    for name, data, ct in [
        ("rows.csv", b"a,b\n1,2\n", "text/csv"),
        ("rows.tsv", b"a\tb\n1\t2\n", "text/tab-separated-values"),
        ("sheet.xls", _ss_xml([HEADER_4, ["11", "x", "0", "0", "1.0000", "0"]]),
         "application/vnd.ms-excel"),
    ]:
        att = _upload_attachment(auth_client, name, data, ct)
        assert att["id"]


def test_generic_content_type_inferred_from_extension(auth_client):
    # Browsers send application/octet-stream for .xls — extension wins.
    resp = auth_client.post(
        "/transactions/attachments",
        files={"file": ("حساب گروه.xls",
                        io.BytesIO(_ss_xml([HEADER_4, ["11", "x", "0", "0", "1.0000", "0"]])),
                        "application/octet-stream")},
    )
    assert resp.status_code in (200, 201), resp.text


def test_binary_payload_claiming_csv_rejected(auth_client):
    resp = auth_client.post(
        "/transactions/attachments",
        files={"file": ("evil.csv", io.BytesIO(b"\x00\x01\x02binary"), "text/csv")},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Part A — smart intake routing
# ---------------------------------------------------------------------------

def _chart_files():
    group = _ss_xml([
        HEADER_4,
        ["76", "دارايي چت", "0", "0", "500.0000", "0.0000"],
        ["77", "بدهي چت", "0", "0", "0.0000", "500.0000"],
    ])
    kol = _ss_xml([
        HEADER_4,
        ["7610", "بانك چت", "0", "0", "500.0000", "0.0000"],
        ["7710", "پرداختني چت", "0", "0", "0.0000", "500.0000"],
    ])
    tafsili = _ss_xml([
        HEADER_TAFSILI,
        ["905", "بانك چت تست 55550001", "حساب بانكي", "0", "0", "500.0000", "0"],
    ])
    return [("گروه.xls", group), ("كل.xls", kol), ("تفصيلي.xls", tafsili)]


def test_chart_export_drop_routes_to_migration_preview(auth_client):
    ids = [
        _upload_attachment(auth_client, name, data, "application/vnd.ms-excel")["id"]
        for name, data in _chart_files()
    ]
    resp = _chat(auth_client, "این فایل‌ها از نرم‌افزار قبلی است", attachment_ids=ids)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    intake = body["intake"]
    assert intake["kind"] == "chart_export"
    assert intake["summary"]["tiers"] == {"group": 2, "kol": 2, "tafsili": 1}
    assert "moein" in intake["missing_tiers"]  # partial set is called out
    assert intake["token"]
    assert body["stop_reason"] == "intake"
    assert body["session_id"]

    # Confirm through the normal gated endpoint — idempotent migration apply.
    c = auth_client.post("/migration/import/confirm", json={"token": intake["token"]})
    assert c.status_code == 200, c.text
    assert c.json()["result"]["entities"]["banks_created"] == 1

    # Re-drop the same files → same token, already_applied warning, 0 created.
    ids2 = [
        _upload_attachment(auth_client, name, data, "application/vnd.ms-excel")["id"]
        for name, data in _chart_files()
    ]
    resp2 = _chat(auth_client, "دوباره", attachment_ids=ids2)
    intake2 = resp2.json()["intake"]
    assert intake2["token"] == intake["token"]
    assert intake2["already_applied"] is True
    c2 = auth_client.post("/migration/import/confirm", json={"token": intake2["token"]})
    assert c2.json()["result"]["entities"]["banks_created"] == 0
    assert c2.json()["result"]["entities"]["banks_reused"] == 1


def test_transaction_csv_drop_proposes_entries(auth_client, db):
    att = _upload_attachment(auth_client, "transactions.csv", TXN_CSV, "text/csv")
    resp = _chat(auth_client, "این تراکنش‌ها را ثبت کن", attachment_ids=[att["id"]])
    assert resp.status_code == 200, resp.text
    intake = resp.json()["intake"]
    assert intake["kind"] == "transactions"
    assert intake["total_vouchers"] == 1
    assert intake["balanced_vouchers"] == 1
    assert intake["unmapped_accounts"] == 0
    mappings = intake["account_mappings"]
    assert {m["account_code"] for m in mappings} == {"6112", "1110"}

    n_before = len(db.execute(select(Transaction)).scalars().all())
    c = auth_client.post("/transactions/excel-import/confirm", json={
        "file_token": intake["file_token"],
        "jalali_year": intake["jalali_year"],
        "account_mappings": mappings,
        "amount_multiplier": 1,
        "currency": "IRR",
    })
    assert c.status_code == 200, c.text
    assert c.json()["imported"] == 1
    assert len(db.execute(select(Transaction)).scalars().all()) == n_before + 1


def test_unrecognized_sheet_becomes_qa_context(db, tmp_path):
    from app.services.ai_accountant.file_intake import build_spreadsheet_intake

    p = tmp_path / "inventory.csv"
    p.write_text("کالا,تعداد\nلپتاپ,۵\nمانیتور,۳\n", encoding="utf-8")
    att = TransactionAttachment(
        file_name="inventory.csv", file_path=str(p),
        content_type="text/csv", size_bytes=p.stat().st_size,
    )
    db.add(att)
    db.flush()
    intake = build_spreadsheet_intake(db, [att], can_migrate=True)
    assert intake.kind == "context"
    assert "لپتاپ" in intake.context_text
    assert "Attached for questions" in intake.detected


def test_chart_export_without_migration_role_is_context_only(db, tmp_path):
    from app.services.ai_accountant.file_intake import build_spreadsheet_intake

    p = tmp_path / "گروه.xls"
    p.write_bytes(_ss_xml([HEADER_4, ["78", "دارايي", "0", "0", "100.0000", "0"]]))
    att = TransactionAttachment(
        file_name="گروه.xls", file_path=str(p),
        content_type="application/vnd.ms-excel", size_bytes=1,
    )
    db.add(att)
    db.flush()
    intake = build_spreadsheet_intake(db, [att], can_migrate=False)
    assert intake.kind == "context"
    assert intake.payload.get("denied") == "migration"


def test_path_only_message_asks_for_real_upload(auth_client, db):
    resp = _chat(auth_client, "/Users/masein/Downloads/حساب معين.xls")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stop_reason"] == "intake"
    assert "attach" in body["text"].lower() or "پیوست" in body["text"]
    # persisted as a real turn in the session
    msgs = auth_client.get(f"/ai-accountant/sessions/{body['session_id']}/messages").json()
    assert len(msgs) == 2 and msgs[0]["role"] == "user"


# ---------------------------------------------------------------------------
# Part B — sessions
# ---------------------------------------------------------------------------

def test_session_create_list_rename_archive(auth_client):
    created = auth_client.post("/ai-accountant/sessions", json={"title": "پروژه حقوق"})
    assert created.status_code == 201
    sid = created.json()["id"]

    listed = auth_client.get("/ai-accountant/sessions").json()
    assert any(s["id"] == sid for s in listed)

    renamed = auth_client.patch(f"/ai-accountant/sessions/{sid}", json={"title": "حقوق تیر"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "حقوق تیر"

    deleted = auth_client.delete(f"/ai-accountant/sessions/{sid}")
    assert deleted.status_code == 200
    listed = auth_client.get("/ai-accountant/sessions").json()
    assert all(s["id"] != sid for s in listed)  # archived = hidden, not gone


def test_new_chat_does_not_wipe_old_session(auth_client):
    r1 = _chat(auth_client, "/tmp/only-a-path-1.xls")  # deterministic, no LLM
    old_sid = r1.json()["session_id"]
    r2 = auth_client.post("/ai-accountant/sessions", json={})
    new_sid = r2.json()["id"]
    assert new_sid != old_sid
    old_msgs = auth_client.get(f"/ai-accountant/sessions/{old_sid}/messages").json()
    assert len(old_msgs) == 2


def test_session_autotitle_from_first_message(auth_client):
    r = _chat(auth_client, "/tmp/رسید-بانکی-مرداد.pdf")
    sid = r.json()["session_id"]
    sessions = auth_client.get("/ai-accountant/sessions").json()
    row = next(s for s in sessions if s["id"] == sid)
    assert row["title"]  # auto-titled from the first user message
    assert row["title"].startswith("/tmp/رسید")


def test_session_search_by_message_content(auth_client):
    kw = f"کلیدواژه{uuid.uuid4().hex[:6]}"
    r = _chat(auth_client, f"/tmp/{kw}.xls")
    sid = r.json()["session_id"]
    hits = auth_client.get(f"/ai-accountant/sessions?q={kw}").json()
    assert any(s["id"] == sid for s in hits)
    hit = next(s for s in hits if s["id"] == sid)
    assert hit["match_snippet"] and kw in hit["match_snippet"]
    misses = auth_client.get("/ai-accountant/sessions?q=zzz-not-there-zzz").json()
    assert all(s["id"] != sid for s in misses)


def test_foreign_session_is_forbidden(auth_client, db):
    other = AIChatSession(user_id="someone-else", title="private")
    db.add(other)
    db.commit()
    assert auth_client.patch(f"/ai-accountant/sessions/{other.id}", json={"title": "x"}).status_code == 403
    assert auth_client.delete(f"/ai-accountant/sessions/{other.id}").status_code == 403
    assert auth_client.get(f"/ai-accountant/sessions/{other.id}/messages").status_code == 403
    listed = auth_client.get("/ai-accountant/sessions").json()
    assert all(s["id"] != str(other.id) for s in listed)

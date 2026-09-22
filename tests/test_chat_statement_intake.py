"""QA finding 1: a bank statement PDF dropped into the AI chat must go
through the statement import pipeline and come back as a review card —
never "I can't extract them, please type the rows".
"""
from __future__ import annotations

import asyncio
import io
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.transaction import TransactionAttachment

FAKE_PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"

VISION_ROWS = [
    {"date": "2026-06-27", "description": "کارمزد پل", "amount": 120_000, "balance": 3_563_506_455, "direction": "debit"},
    {"date": "2026-06-27", "description": "پل از همراه بانک", "amount": 600_000_000, "balance": 2_963_506_455, "direction": "debit"},
    {"date": "2026-06-28", "description": "وی-پوز خرید اینترنتی به‌پرداخت ملت", "amount": 48_080_000, "balance": 2_915_426_455, "direction": "debit"},
    {"date": "2026-07-01", "description": "ساتنا از همراه بانک", "amount": 1_500_000_000, "balance": 1_415_426_455, "direction": "debit"},
    {"date": "2026-07-06", "description": "کارت به کارت", "amount": 11_367_200, "balance": 1_403_759_255, "direction": "debit"},
]


def _upload(auth_client, name, data, ctype):
    resp = auth_client.post("/transactions/attachments", files={"file": (name, io.BytesIO(data), ctype)})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _chat(auth_client, message, attachment_ids=None, session_id=None):
    return auth_client.post("/ai-accountant/chat", json={
        "message": message, "session_id": session_id, "attachment_ids": attachment_ids or [],
    })


@pytest.fixture()
def vision_rows(monkeypatch):
    """Stand in for the vision model: the friend's Mellat rows, no network."""
    import app.services.ocr_extract as ocr

    async def fake_rows(path, content_type):
        return list(VISION_ROWS)

    async def fail_receipt(*a, **k):  # the receipt path must not be used for a statement
        raise AssertionError("receipt OCR must not run for a statement")

    monkeypatch.setattr(ocr, "extract_statement_rows", fake_rows)
    monkeypatch.setattr(ocr, "extract_from_attachment", fail_receipt)
    return VISION_ROWS


@pytest.fixture()
def no_llm(monkeypatch):
    import app.api.ai_accountant as api

    async def boom(*a, **k):
        raise AssertionError("the statement turn must be deterministic — no LLM call")

    monkeypatch.setattr(api, "run_chat_turn", boom)


def test_statement_pdf_in_chat_is_imported_and_reviewed(auth_client, db, vision_rows, no_llm):
    att = _upload(auth_client, "mellat-transactions.pdf", FAKE_PDF, "application/pdf")
    resp = _chat(auth_client, 'these are last "Mellat" transactions. add them', attachment_ids=[att["id"]])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    intake = body["intake"]
    assert intake["kind"] == "bank_statement" and intake["status"] == "imported"
    assert intake["bank_name"] == "Mellat"
    assert intake["total_rows"] == len(vision_rows)
    assert intake["counts"]["unrecorded"] == len(vision_rows)   # nothing in the books yet
    assert intake["balance"]["statement_closing"] == 1_403_759_255
    assert body["stop_reason"] == "intake" and body["proposals"] == []
    # The reply is a summary, not a request to type the rows.
    assert "5 rows" in body["text"] and "not in the books" in body["text"]
    assert "type" not in body["text"].lower()

    stmt = db.get(BankStatement, uuid.UUID(intake["statement_id"]))
    assert stmt is not None and stmt.bank_name == "Mellat" and stmt.source_type == "ocr_pdf"
    rows = db.execute(select(BankStatementRow).where(BankStatementRow.statement_id == stmt.id)).scalars().all()
    assert len(rows) == 5 and all(r.debit > 0 for r in rows)
    assert rows[0].tx_date == date(2026, 6, 27)


def test_same_statement_dropped_again_points_at_the_earlier_import(auth_client, db, vision_rows, no_llm):
    data = FAKE_PDF + b"% second-file variant\n"
    att1 = _upload(auth_client, "mellat-transactions.pdf", data, "application/pdf")
    first = _chat(auth_client, "add the mellat transactions", attachment_ids=[att1["id"]]).json()["intake"]
    att2 = _upload(auth_client, "mellat-transactions.pdf", data, "application/pdf")
    second = _chat(auth_client, "add the mellat transactions", attachment_ids=[att2["id"]]).json()
    assert second["intake"]["status"] == "duplicate"
    assert second["intake"]["statement_id"] == first["statement_id"]
    assert "already imported" in second["text"].lower()


def test_receipt_pdf_still_takes_the_ocr_path(auth_client, db, monkeypatch):
    """A receipt is not a statement: no BankStatement is created and the
    ordinary OCR-context turn runs."""
    import app.api.ai_accountant as api

    called = {}

    async def fake_turn(*a, **k):
        called["ocr_context"] = k.get("ocr_context")
        from app.services.ai_accountant.orchestrator import ChatResult
        return ChatResult(session_id=str(uuid.uuid4()), text="ok", proposals=[], tool_calls=[], stop_reason="end_turn", turns=1)

    async def fake_ocr(db_, ids):
        return "Attached document OCR: vendor Refah, total 1250000", [1_250_000]

    monkeypatch.setattr(api, "run_chat_turn", fake_turn)
    monkeypatch.setattr(api, "_build_ocr_context", fake_ocr)
    n_before = len(db.execute(select(BankStatement)).scalars().all())
    att = _upload(auth_client, "receipt.pdf", FAKE_PDF, "application/pdf")
    resp = _chat(auth_client, "record this expense", attachment_ids=[att["id"]])
    assert resp.status_code == 200, resp.text
    assert resp.json()["intake"] is None
    assert "Refah" in (called.get("ocr_context") or "")
    assert len(db.execute(select(BankStatement)).scalars().all()) == n_before


def test_roles_without_books_write_fall_through(db, tmp_path):
    from app.services.ai_accountant.statement_intake import maybe_statement_intake

    p = tmp_path / "mellat-transactions.pdf"
    p.write_bytes(FAKE_PDF)
    att = TransactionAttachment(file_name=p.name, file_path=str(p), content_type="application/pdf", size_bytes=len(FAKE_PDF))
    db.add(att)
    db.flush()
    out = asyncio.run(maybe_statement_intake(db, user_role="viewer", attachments=[att], message="add them", lang="en"))
    assert out is None


def test_unreadable_statement_gets_a_clear_reply(db, tmp_path, monkeypatch):
    import app.services.ocr_extract as ocr
    from app.services.ai_accountant.statement_intake import maybe_statement_intake

    async def nothing(path, content_type):
        return []

    async def empty(path, content_type):
        return {"raw_text": ""}

    monkeypatch.setattr(ocr, "extract_statement_rows", nothing)
    monkeypatch.setattr(ocr, "extract_from_attachment", empty)
    p = tmp_path / "saman-statement.pdf"
    p.write_bytes(FAKE_PDF + b"% unreadable\n")
    att = TransactionAttachment(file_name=p.name, file_path=str(p), content_type="application/pdf", size_bytes=10)
    db.add(att)
    db.flush()
    out = asyncio.run(maybe_statement_intake(db, user_role="owner", attachments=[att], message="", lang="fa"))
    assert out is not None and out.intake["status"] == "failed"
    assert "CSV" in out.text


def test_review_turn_without_a_card_gets_one_built_for_the_first_unrecorded_row(auth_client, db, monkeypatch):
    """gpt-4o-mini describes the row and asks 'shall I record it?' instead of
    calling the proposal tool; the server raises the card itself."""
    import app.api.ai_accountant as api
    from app.models.ai_accountant import AIProposal
    from app.models.bank_statement import BankStatement, BankStatementRow
    from app.services.ai_accountant.orchestrator import ChatResult

    s = BankStatement(bank_name="Net Bank", source_type="csv", source_filename="net.csv", currency="IRR",
                      from_date=date(2026, 5, 1), to_date=date(2026, 5, 31), status="parsed", total_rows=2)
    db.add(s); db.flush()
    r1 = BankStatementRow(statement_id=s.id, row_index=1, tx_date=date(2026, 5, 3), description="کارمزد بانکی",
                          debit=120_000, credit=0, confidence=1.0, recon_status="unmatched", suggested_account_code="6112")
    r2 = BankStatementRow(statement_id=s.id, row_index=2, tx_date=date(2026, 5, 4), description="واریز مشتری",
                          debit=0, credit=900_000, confidence=1.0, recon_status="unmatched")
    db.add_all([r1, r2]); db.commit()

    stable_session = str(uuid.uuid4())

    async def described_but_no_card(*a, **k):
        return ChatResult(session_id=stable_session, text="Row 1 is a bank fee of 120,000. Shall I record it?",
                          proposals=[], tool_calls=[{"name": "review_bank_statement", "input": {"statement_id": str(s.id)}}],
                          stop_reason="end_turn", turns=2)

    monkeypatch.setattr(api, "run_chat_turn", described_but_no_card)
    resp = _chat(auth_client, f"Review bank statement {s.id} step by step")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["proposals"]) == 1
    assert "Click Confirm" in body["text"]
    prop = db.execute(select(AIProposal).where(
        AIProposal.confirmation_token == uuid.UUID(body["proposals"][0]["confirmation_token"]))).scalar_one()
    assert prop.tool_input["bank_statement_row_id"] == str(r1.id)
    legs = {(l["account_code"], l["debit"], l["credit"]) for l in prop.tool_input["lines"]}
    assert legs == {("6112", 120_000, 0), ("1110", 0, 120_000)}   # money out: Dr expense / Cr bank

    # The same turn again in the same session (card still pending) moves on
    # to the next row; the text names no amount, so the first free row wins.
    resp2 = _chat(auth_client, "next", session_id=body["session_id"])
    prop2 = db.execute(select(AIProposal).where(
        AIProposal.confirmation_token == uuid.UUID(resp2.json()["proposals"][0]["confirmation_token"]))).scalar_one()
    assert prop2.tool_input["bank_statement_row_id"] == str(r2.id)
    legs2 = {(l["account_code"], l["debit"], l["credit"]) for l in prop2.tool_input["lines"]}
    assert ("1110", 900_000, 0) in legs2                              # money in: Dr bank

    # Confirming posts the row and the statement remembers it.
    ex = auth_client.post("/ai-accountant/execute", json={"confirmation_token": str(prop.confirmation_token)})
    assert ex.status_code == 200, ex.text
    db.expire_all()
    assert str(db.get(BankStatementRow, r1.id).created_transaction_id) == ex.json()["transaction_id"]


def test_safety_net_stays_quiet_when_the_model_did_its_job(auth_client, db, monkeypatch):
    import app.api.ai_accountant as api
    from app.services.ai_accountant.orchestrator import ChatResult

    async def with_card(*a, **k):
        return ChatResult(session_id=str(uuid.uuid4()), text="Proposed.", tool_calls=[{"name": "review_bank_statement", "input": {}}],
                          proposals=[{"confirmation_token": str(uuid.uuid4()), "tool_name": "propose_create_transaction",
                                      "summary": "x", "preview": {}}], stop_reason="end_turn", turns=2)

    monkeypatch.setattr(api, "run_chat_turn", with_card)
    body = _chat(auth_client, "review").json()
    assert len(body["proposals"]) == 1 and "Click Confirm" not in body["text"]


def test_safety_net_builds_the_card_for_the_row_the_model_described(db):
    """When the model narrates row 2 (its amount is in the text), the card must
    be for row 2 even though row 1 is also unrecorded."""
    from app.models.ai_accountant import AIProposal
    from app.models.bank_statement import BankStatement, BankStatementRow
    from app.services.ai_accountant.statement_intake import ensure_statement_row_proposal

    s = BankStatement(bank_name="Match Bank", source_type="csv", source_filename="m.csv", currency="IRR",
                      from_date=date(2026, 4, 1), to_date=date(2026, 4, 30), status="parsed", total_rows=2)
    db.add(s); db.flush()
    r1 = BankStatementRow(statement_id=s.id, row_index=1, tx_date=date(2026, 4, 3), description="اول", debit=120_000, credit=0, confidence=1.0, recon_status="unmatched")
    r2 = BankStatementRow(statement_id=s.id, row_index=2, tx_date=date(2026, 4, 4), description="دوم", debit=600_000_000, credit=0, confidence=1.0, recon_status="unmatched")
    db.add_all([r1, r2]); db.commit()

    out = asyncio.run(ensure_statement_row_proposal(
        db, user_id="u-match", username="t", session_id=None, user_message="next",
        tool_calls=[{"name": "review_bank_statement", "input": {"statement_id": str(s.id)}}],
        assistant_text="Next: 2026-04-04, ۶۰۰٬۰۰۰٬۰۰۰ ریال left the account — دوم. Shall I record it?",
    ))
    prop = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(out["confirmation_token"]))).scalar_one()
    assert prop.tool_input["bank_statement_row_id"] == str(r2.id)
    assert prop.tool_input["description"] == "دوم" and prop.tool_input["date"] == "2026-04-04"

    # No amount in the text → first unrecorded row without a pending card (r1).
    out2 = asyncio.run(ensure_statement_row_proposal(
        db, user_id="u-match", username="t", session_id=None, user_message="next",
        tool_calls=[{"name": "review_bank_statement", "input": {"statement_id": str(s.id)}}],
        assistant_text="Shall we continue?",
    ))
    prop2 = db.execute(select(AIProposal).where(AIProposal.confirmation_token == uuid.UUID(out2["confirmation_token"]))).scalar_one()
    assert prop2.tool_input["bank_statement_row_id"] == str(r1.id)
    # Both rows now carry a pending card → nothing left to build.
    assert asyncio.run(ensure_statement_row_proposal(
        db, user_id="u-match", username="t", session_id=None, user_message="next",
        tool_calls=[{"name": "review_bank_statement", "input": {"statement_id": str(s.id)}}],
    )) is None


def test_safety_net_also_fires_when_the_model_tried_a_posted_row(auth_client, db, monkeypatch, make_transaction):
    """'next' after a confirm: the model re-tries the posted row (the tool
    refuses), describes the next one and asks — the card is built for the one
    it described."""
    import app.api.ai_accountant as api
    from app.models.ai_accountant import AIProposal
    from app.models.bank_statement import BankStatement, BankStatementRow
    from app.services.ai_accountant.orchestrator import ChatResult

    s = BankStatement(bank_name="Retry Bank", source_type="csv", source_filename="r.csv", currency="IRR",
                      from_date=date(2026, 3, 1), to_date=date(2026, 3, 31), status="parsed", total_rows=2)
    db.add(s); db.flush()
    posted = make_transaction([("6112", 120_000, 0), ("1110", 0, 120_000)], tx_date=date(2026, 3, 3), description="posted")
    r1 = BankStatementRow(statement_id=s.id, row_index=1, tx_date=date(2026, 3, 3), description="posted", debit=120_000, credit=0,
                          confidence=1.0, recon_status="matched", user_approved=True, created_transaction_id=posted.id)
    r2 = BankStatementRow(statement_id=s.id, row_index=2, tx_date=date(2026, 3, 4), description="open", debit=600_000_000, credit=0,
                          confidence=1.0, recon_status="unmatched")
    db.add_all([r1, r2]); db.commit()

    async def retried_posted_row(*a, **k):
        return ChatResult(session_id=str(uuid.uuid4()),
                          text="That one is already recorded. Next: 600,000,000 IRR left the account on 2026-03-04. Shall I record it?",
                          proposals=[], tool_calls=[{"name": "propose_create_transaction",
                                                     "input": {"bank_statement_row_id": str(r1.id)}}],
                          stop_reason="end_turn", turns=3)

    monkeypatch.setattr(api, "run_chat_turn", retried_posted_row)
    body = _chat(auth_client, "next").json()
    assert len(body["proposals"]) == 1
    prop = db.execute(select(AIProposal).where(
        AIProposal.confirmation_token == uuid.UUID(body["proposals"][0]["confirmation_token"]))).scalar_one()
    assert prop.tool_input["bank_statement_row_id"] == str(r2.id)

"""Tomans, wherever they are written (chat review 2026-11-10, scenarios BS1–BS5).

From the production chats, in rial books: an invoice in tomans typed in English was carded at a tenth of its
rials; a fee in tomans was carded at a thousand times its rials and refused three times with only the rough figure
beside it; an invoice printed in tomans was read as rials, and its figure beat the cheque of ten times it that the
user typed. The figures here are made up.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction, ProposeCreateTransactionInput, _toman_amounts)
from tests.test_statement_export import _company


@pytest.fixture()
def ir(client, db):
    from tests.test_admin_audit import _purge_company
    _api, cid = _company(client, db, "ir", "IRR")
    yield cid
    client.cookies.clear()
    _purge_company(db, cid)


def _card(db, cid, amount, message, *, documents=(), dr="6210", cr="1110"):
    from app.services.ai_accountant.orchestrator import _numbers_in_text
    args = ProposeCreateTransactionInput(date="2026-08-02", description="test", currency="IRR", lines=[
        {"account_code": dr, "debit": amount, "credit": 0}, {"account_code": cr, "debit": 0, "credit": amount}])
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u-toman", user_message=message,
                          source_amounts=[*documents, *_numbers_in_text(message)], document_amounts=list(documents))
        return asyncio.run(ProposeCreateTransaction().run(ctx, args))


def test_bs1_tomans_written_in_english(db, ir):
    assert _toman_amounts("invoice total 640M Toman, 40% of it for the software") == {640_000_000}
    assert _toman_amounts("I transferred 180million Toman between our banks") == {180_000_000}
    assert _toman_amounts("42000 toman was the transaction fee") == {42_000}
    assert _toman_amounts("2.5m tomans") == {2_500_000}
    assert _toman_amounts("۳ میلیارد و ۴۰۰ میلیون تومان زدیم") == {3_400_000_000}
    assert _toman_amounts("قسط ۲۰ میلیون تومان و ۱۵۰ هزار تومن") == {20_000_000, 150_000}
    assert _toman_amounts("tomanstan 5") == set()
    with pytest.raises(ToolError) as e:
        _card(db, ir, 640_000_000, "invoice total 640M Toman, 40% of it for the software", dr="1112", cr="4110")
    assert e.value.code == "toman_not_converted" and "6,400,000,000" in e.value.message
    assert _card(db, ir, 6_400_000_000, "invoice total 640M Toman, 40% of it for the software",
                 dr="1112", cr="4110")["confirmation_token"]


def test_bs2_a_refusal_says_the_rial_figure(db, ir):
    with pytest.raises(ToolError) as e:
        _card(db, ir, 420_000_000, "42000 toman was the transaction fee")
    assert e.value.code == "amount_mismatch"
    assert "The user said 42,000 tomans, which is 420,000 rials." in e.value.message
    assert _card(db, ir, 420_000, "42000 toman was the transaction fee")["confirmation_token"]


def test_bs3_a_document_printed_in_tomans(monkeypatch, db, ir):
    from app.services.ocr_extract import _extract_fields_from_text, _normalize_extracted
    out = _normalize_extracted({"currency": "IRT", "total": 175_000_000, "vendor_name": "landlord",
                                "line_items": [{"description": "rent", "amount": "175,000,000"}]})
    assert out["currency"] == "IRR" and out["toman"] is True
    assert out["amount"] == out["total"] == 1_750_000_000 and out["line_items"][0]["amount"] == 1_750_000_000
    plain_rials = _normalize_extracted({"currency": "IRR", "total": 175_000_000})
    assert plain_rials["amount"] == 175_000_000 and plain_rials["toman"] is False
    text = _extract_fields_from_text("فاکتور اجاره\nجمع کل: 175,000,000 تومان\n")
    assert text["amount"] == 1_750_000_000 and text["toman"] is True
    assert _extract_fields_from_text("جمع کل: 175,000,000 ریال\n")["amount"] == 175_000_000

    # what the chat is told about the document
    from app.api import ai_accountant as api_mod
    from app.models.transaction import TransactionAttachment

    async def fake_extract(path, ctype):
        return out
    monkeypatch.setattr("app.services.ocr_extract.extract_from_attachment", fake_extract)
    with use_company(ir):
        att = TransactionAttachment(id=uuid.uuid4(), file_name="rent.png", file_path=f"/nowhere/{uuid.uuid4().hex}.png",
                                    content_type="image/png")
        db.add(att)
        db.commit()
        text, amounts = asyncio.run(api_mod._build_ocr_context(db, [str(att.id)]))
        db.delete(att)
        db.commit()
    assert "total amount: 1750000000 IRR (printed in tomans as 175000000; the books keep rials)" in text
    assert amounts == [1_750_000_000]


def test_bs4_the_figure_the_user_typed_beats_the_documents(db, ir):
    said = "paid our rent from the bank, here is the detail: cheque: 1,750,000,000- ref 5521"
    with pytest.raises(ToolError) as e:
        _card(db, ir, 175_000_000, said, documents=[175_000_000], dr="6112")
    assert e.value.code == "toman_not_converted" and "Propose 1,750,000,000" in e.value.message
    assert _card(db, ir, 1_750_000_000, said, documents=[175_000_000], dr="6112")["confirmation_token"]
    # a tenth of what was typed, with no document behind it, is the user's own sum
    assert _card(db, ir, 100_000, "split 1,000,000 between 10 of us")["confirmation_token"]


def test_bs5_the_assistant_converts_and_says_so():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    assert '"640M Toman" → 6,400,000,000 IRR' in SYSTEM_PROMPT
    assert "is in tomans as well: convert it and say the conversion" in SYSTEM_PROMPT

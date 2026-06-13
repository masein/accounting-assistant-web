"""Unit tests for OCR field coercion (Persian digits, totals, Jalali dates)
and the proposal amount-sanity guard. All offline — no network/vision calls.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
    _guard_amount,
)
from app.services.ocr_extract import (
    MAX_SANE_AMOUNT,
    _amount_from_total_line,
    _extract_fields_from_text,
    _normalize_date,
    coerce_amount,
    normalize_digits,
)


# ---------------------------------------------------------------------------
# Persian/Arabic digit + amount coercion
# ---------------------------------------------------------------------------


class TestCoerceAmount:
    def test_persian_digits_normalize(self):
        assert normalize_digits("۳۶۹۰۷۲۰") == "3690720"
        assert normalize_digits("٣٦٩٠") == "3690"

    def test_persian_grouped_total(self):
        # ۳٬۶۹۰٬۷۲۰ ریال → 3690720, not concatenated garbage.
        assert coerce_amount("۳٬۶۹۰٬۷۲۰ ریال") == 3_690_720

    def test_plain_and_grouped(self):
        assert coerce_amount("3,690,720") == 3_690_720
        assert coerce_amount(3690720) == 3_690_720
        assert coerce_amount("3690720.0") == 3_690_720

    def test_insane_magnitude_rejected(self):
        assert coerce_amount("845110000381004681") is None  # the Asiatech bug
        assert coerce_amount(MAX_SANE_AMOUNT + 1) is None
        assert coerce_amount(MAX_SANE_AMOUNT) == MAX_SANE_AMOUNT

    def test_garbage_returns_none(self):
        assert coerce_amount("") is None
        assert coerce_amount(None) is None
        assert coerce_amount("abc") is None


class TestTotalLineExtraction:
    def test_targets_labelled_total_not_max_digit(self):
        text = (
            "کد اقتصادی 10102583274\n"
            "شناسه ملی 14013316857\n"
            "جمع کل صورتحساب: ۳٬۶۹۰٬۷۲۰ ریال\n"
            "کد پستی 1234567890\n"
        )
        # The economic/national codes are larger digit runs than the total,
        # but we must pick the labelled total, never the max digit string.
        assert _amount_from_total_line(text) == 3_690_720

    def test_text_fallback_uses_total(self):
        text = "Acme Co\nمبلغ کل: 3,355,200\nمبلغ کل بعلاوه مالیات: 3,690,720\n"
        out = _extract_fields_from_text(text)
        assert out["amount"] == 3_690_720


# ---------------------------------------------------------------------------
# Jalali invoice date → Gregorian
# ---------------------------------------------------------------------------


class TestVisionRouting:
    def test_parse_json_blob_strips_fences(self):
        from app.services.ocr_extract import _parse_json_blob

        out = _parse_json_blob('```json\n{"total": 3690720}\n```')
        assert out["total"] == 3690720

    def test_parse_json_blob_rejects_non_object(self):
        from app.services.ocr_extract import OCRExtractError, _parse_json_blob

        with pytest.raises(OCRExtractError):
            _parse_json_blob("not json at all")

    def test_gemini_enabled_only_for_gemini_models(self, monkeypatch):
        from app.services import ocr_extract

        monkeypatch.setattr(
            ocr_extract, "resolve_active_ai_backend", lambda: {"api_key": "k"}
        )
        assert ocr_extract._gemini_enabled("gemini-2.5-pro") is True
        assert ocr_extract._gemini_enabled("gpt-4o") is False

    def test_gemini_disabled_without_key(self, monkeypatch):
        from app.services import ocr_extract

        monkeypatch.setattr(
            ocr_extract, "resolve_active_ai_backend", lambda: {"api_key": ""}
        )
        assert ocr_extract._gemini_enabled("gemini-2.5-pro") is False

    def test_normalize_extracted_prefers_grand_total(self):
        from app.services.ocr_extract import _normalize_extracted

        out = _normalize_extracted({
            "subtotal": 3355200, "tax": 335520, "total": 3690720,
            "date": "1404/10/15", "currency": "irr",
        })
        assert out["amount"] == 3690720
        assert out["total"] == 3690720
        assert out["currency"] == "IRR"
        assert out["date"].startswith("2026-01")

    def test_normalize_extracted_computes_total_from_parts(self):
        from app.services.ocr_extract import _normalize_extracted

        # No explicit total → subtotal + tax.
        out = _normalize_extracted({"subtotal": 3355200, "tax": 335520})
        assert out["amount"] == 3690720


class TestDateNormalize:
    def test_jalali_to_gregorian(self):
        # 1404/10/15 (Jalali) → 2026-01-05 (Gregorian).
        iso = _normalize_date("1404/10/15")
        assert iso is not None
        assert iso.startswith("2026-01")

    def test_persian_digit_jalali(self):
        assert _normalize_date("۱۴۰۴/۱۰/۱۵") == _normalize_date("1404/10/15")

    def test_gregorian_passthrough(self):
        assert _normalize_date("2026-05-20") == "2026-05-20"

    def test_none_for_garbage(self):
        assert _normalize_date("not a date") is None
        assert _normalize_date(None) is None


# ---------------------------------------------------------------------------
# Amount sanity guard
# ---------------------------------------------------------------------------


class TestAmountGuard:
    def test_impossible_magnitude_blocked(self):
        ctx = ToolContext(db=None, user_id="u")
        with pytest.raises(ToolError) as ei:
            _guard_amount(ctx, MAX_SANE_AMOUNT + 5)
        assert ei.value.code == "amount_out_of_range"

    def test_mismatch_blocked(self):
        # "300 GBP" mis-scaled to 30,000 → 100× the source.
        ctx = ToolContext(db=None, user_id="u", source_amounts=[300])
        with pytest.raises(ToolError) as ei:
            _guard_amount(ctx, 30_000)
        assert ei.value.code == "amount_mismatch"

    def test_match_within_tolerance_ok(self):
        ctx = ToolContext(db=None, user_id="u", source_amounts=[300])
        _guard_amount(ctx, 300)  # exact
        _guard_amount(ctx, 900)  # 3× — within 10×

    def test_aggregation_passes(self):
        # "5 invoices of 200" → 1000; closest source 200 is 5× → allowed.
        ctx = ToolContext(db=None, user_id="u", source_amounts=[5, 200])
        _guard_amount(ctx, 1000)

    def test_no_sources_only_caps(self):
        ctx = ToolContext(db=None, user_id="u")
        _guard_amount(ctx, 5_000_000)  # no sources → only the absolute cap


class TestGuardInProposeTool:
    @pytest.fixture(autouse=True)
    def _isolate(self, db: Session):
        yield
        db.execute(delete(AIProposal))
        db.execute(delete(TransactionLine))
        db.execute(delete(Transaction))
        db.commit()

    def test_propose_blocks_mismatch(self, db: Session):
        ctx = ToolContext(
            db=db, user_id="u", username="t",
            user_message="paid 300", source_amounts=[300],
        )
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20", description="x", currency="GBP",
            lines=[
                {"account_code": "6110", "debit": 30_000, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 30_000},
            ],
        )
        with pytest.raises(ToolError) as ei:
            asyncio.run(tool.run(ctx, payload))
        assert ei.value.code == "amount_mismatch"

    def test_propose_allows_matching(self, db: Session):
        ctx = ToolContext(
            db=db, user_id="u", username="t",
            user_message="paid 300", source_amounts=[300],
        )
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20", description="x", currency="GBP",
            lines=[
                {"account_code": "6110", "debit": 300, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 300},
            ],
        )
        out = asyncio.run(tool.run(ctx, payload))
        assert out["confirmation_token"]

    def test_gbp_300_stored_as_whole_units(self, db: Session):
        """'300 GBP' must persist as 300 (whole pounds), not 30000 minor
        units, and keep the GBP label — the P1 ×100 regression guard."""
        from app.models.ai_accountant import AIProposal
        from sqlalchemy import select as _select

        ctx = ToolContext(db=db, user_id="u", username="t", user_message="300 GBP supplies")
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20", description="office supplies", currency="GBP",
            lines=[
                {"account_code": "6110", "debit": 300, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 300},
            ],
        )
        out = asyncio.run(tool.run(ctx, payload))
        row = db.execute(
            _select(AIProposal).where(
                AIProposal.confirmation_token == uuid.UUID(out["confirmation_token"])
            )
        ).scalar_one()
        debit_total = sum(ln["debit"] for ln in row.tool_input["lines"])
        assert debit_total == 300  # NOT 30000
        assert row.tool_input["currency"] == "GBP"  # not relabelled to IRR

    def test_absurd_magnitude_rejected(self, db: Session):
        """> 10^15 is blocked outright even with no source to compare to."""
        ctx = ToolContext(db=db, user_id="u", username="t", user_message="x")
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2026-05-20", description="x", currency="IRR",
            lines=[
                {"account_code": "6110", "debit": 10**15 + 1, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 10**15 + 1},
            ],
        )
        with pytest.raises(ToolError) as ei:
            asyncio.run(tool.run(ctx, payload))
        assert ei.value.code == "amount_out_of_range"


class TestOcrEngineHealth:
    def test_ocr_engine_available_returns_bool(self):
        from app.services.ocr_extract import ocr_engine_available

        assert isinstance(ocr_engine_available(), bool)

    def test_missing_engine_error_is_distinct(self):
        # OCREngineMissing must be catchable as OCRExtractError (so existing
        # fallback paths still work) yet distinguishable for clear logging.
        from app.services.ocr_extract import OCREngineMissing, OCRExtractError

        assert issubclass(OCREngineMissing, OCRExtractError)

    def test_rasterize_raises_engine_missing_for_pdf(self, monkeypatch, tmp_path):
        import builtins

        from app.services import ocr_extract

        real_import = builtins.__import__

        def _no_fitz(name, *a, **k):
            if name == "fitz":
                raise ImportError("no fitz")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_fitz)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with pytest.raises(ocr_extract.OCREngineMissing):
            ocr_extract._rasterize_pages(pdf, "application/pdf")

    def test_extract_falls_back_when_engine_missing(self, monkeypatch, tmp_path):
        # When the engine is missing, extract_from_attachment must not crash —
        # it logs distinctly and degrades (here: empty fields, no exception).
        import builtins

        from app.services import ocr_extract

        real_import = builtins.__import__

        def _no_fitz(name, *a, **k):
            if name == "fitz":
                raise ImportError("no fitz")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_fitz)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        result = asyncio.run(ocr_extract.extract_from_attachment(str(pdf), "application/pdf"))
        assert isinstance(result, dict)
        assert result.get("amount") is None

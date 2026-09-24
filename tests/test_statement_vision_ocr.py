"""Bank-statement PDFs/images now go through the same vision pipeline as
invoices, asking for row-structured JSON, because the free-text regex parser
can't read dense Persian RTL tables. Tests:

* parse_vision_rows maps normalized vision rows → ParseResult (debit/credit,
  dates, period) — pure, no network.
* extract_statement_rows normalizes the model's array (Persian digits, Jalali
  → Gregorian, direction) — vision call is monkeypatched.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app.services import ocr_extract
from app.services.bank_statement_parser import parse_vision_rows


class TestParseVisionRows:
    def test_maps_direction_to_debit_credit(self):
        rows = [
            {"date": "2026-04-26", "description": "card to card", "amount": 133425600,
             "balance": 251660334, "direction": "credit"},
            {"date": "2026-05-02", "description": "withdrawal", "amount": 322500,
             "balance": 251337834, "direction": "debit"},
        ]
        result = parse_vision_rows(rows, bank_name="Mellat")
        assert len(result.rows) == 2
        assert result.rows[0].credit == 133425600 and result.rows[0].debit == 0
        assert result.rows[1].debit == 322500 and result.rows[1].credit == 0
        assert result.rows[0].balance == 251660334
        assert result.from_date == date(2026, 4, 26)
        assert result.to_date == date(2026, 5, 2)
        assert result.source_type == "ocr_vision"

    def test_skips_dateless_and_zero_rows(self):
        rows = [
            {"date": None, "description": "no date", "amount": 100, "direction": "credit"},
            {"date": "2026-04-26", "description": "zero", "amount": 0, "direction": "credit"},
            {"date": "2026-04-27", "description": "ok", "amount": 500, "direction": "credit"},
        ]
        result = parse_vision_rows(rows)
        assert len(result.rows) == 1
        assert result.rows[0].credit == 500


class TestExtractStatementRows:
    def test_normalizes_digits_jalali_and_direction(self, monkeypatch, tmp_path):
        # Model returns Persian digits, a Jalali date, comma separators.
        model_json = json.dumps([
            {"date": "1405/02/06", "description": "کارت به کارت", "amount": "۱۳۳٬۴۲۵٬۶۰۰",
             "balance": "۲۵۱٬۶۶۰٬۳۳۴", "direction": "credit"},
            {"date": "1405/02/12", "description": "برداشت", "amount": "322,500",
             "balance": None, "direction": "debit"},
        ], ensure_ascii=False)

        async def _fake_raw(pages, prompt):
            return model_json

        monkeypatch.setattr(ocr_extract, "_rasterize_pages", lambda p, c, **k: [("image/png", "x")])
        monkeypatch.setattr(ocr_extract, "_vision_raw", _fake_raw)

        pdf = tmp_path / "s.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        rows = asyncio.run(ocr_extract.extract_statement_rows(str(pdf), "application/pdf"))
        assert len(rows) == 2
        # Persian digits parsed to ints.
        assert rows[0]["amount"] == 133425600
        assert rows[0]["balance"] == 251660334
        assert rows[0]["direction"] == "credit"
        # Jalali 1405/02/06 → Gregorian ISO.
        assert rows[0]["date"] and rows[0]["date"].startswith("2026-04")
        assert rows[1]["amount"] == 322500 and rows[1]["direction"] == "debit"

    def test_empty_array_yields_no_rows(self, monkeypatch, tmp_path):
        async def _fake_raw(pages, prompt):
            return "[]"

        monkeypatch.setattr(ocr_extract, "_rasterize_pages", lambda p, c, **k: [("image/png", "x")])
        monkeypatch.setattr(ocr_extract, "_vision_raw", _fake_raw)
        pdf = tmp_path / "s.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        rows = asyncio.run(ocr_extract.extract_statement_rows(str(pdf), "application/pdf"))
        assert rows == []

    def test_bad_json_raises_ocr_error(self, monkeypatch, tmp_path):
        async def _fake_raw(pages, prompt):
            return "not json"

        monkeypatch.setattr(ocr_extract, "_rasterize_pages", lambda p, c, **k: [("image/png", "x")])
        monkeypatch.setattr(ocr_extract, "_vision_raw", _fake_raw)
        pdf = tmp_path / "s.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        with pytest.raises(ocr_extract.OCRExtractError):
            asyncio.run(ocr_extract.extract_statement_rows(str(pdf), "application/pdf"))



class TestVisionFallbackChain:
    """Primary Gemini → secondary Gemini → OpenAI-compatible model."""

    def _arm(self, monkeypatch, fail: set[str]):
        from app.services import ocr_extract
        calls: list[str] = []

        async def fake_gemini(pages, model, prompt):
            calls.append(f"gemini:{model}")
            if model in fail:
                raise RuntimeError(f"{model} down")
            return f"[from {model}]"

        async def fake_openai(pages, model, prompt):
            calls.append(f"openai:{model}")
            if model in fail:
                raise RuntimeError(f"{model} down")
            return f"[from {model}]"

        monkeypatch.setattr(ocr_extract, "_gemini_raw", fake_gemini)
        monkeypatch.setattr(ocr_extract, "_openai_vision_raw", fake_openai)
        monkeypatch.setattr(ocr_extract, "_gemini_enabled", lambda m: True)
        monkeypatch.setattr(ocr_extract, "_resolve_ocr_base_model", lambda: ("https://x", "gemini-3.7-flash"))
        monkeypatch.setattr(ocr_extract.settings, "ocr_gemini_fallback_model", "gemini-2.5-pro")
        monkeypatch.setattr(ocr_extract.settings, "ocr_fallback_model", "gpt-4o")
        return calls

    def test_primary_wins_when_healthy(self, monkeypatch):
        import asyncio
        from app.services import ocr_extract
        calls = self._arm(monkeypatch, fail=set())
        assert asyncio.run(ocr_extract._vision_raw([("image/png", "x")], "p")) == "[from gemini-3.7-flash]"
        assert calls == ["gemini:gemini-3.7-flash"]

    def test_second_gemini_then_openai(self, monkeypatch):
        import asyncio
        from app.services import ocr_extract
        calls = self._arm(monkeypatch, fail={"gemini-3.7-flash"})
        assert asyncio.run(ocr_extract._vision_raw([("image/png", "x")], "p")) == "[from gemini-2.5-pro]"
        assert calls == ["gemini:gemini-3.7-flash", "gemini:gemini-2.5-pro"]

        calls = self._arm(monkeypatch, fail={"gemini-3.7-flash", "gemini-2.5-pro"})
        assert asyncio.run(ocr_extract._vision_raw([("image/png", "x")], "p")) == "[from gpt-4o]"
        assert calls == ["gemini:gemini-3.7-flash", "gemini:gemini-2.5-pro", "openai:gpt-4o"]

    def test_everything_down_is_a_clean_ocr_error(self, monkeypatch):
        import asyncio
        import pytest
        from app.services import ocr_extract
        self._arm(monkeypatch, fail={"gemini-3.7-flash", "gemini-2.5-pro", "gpt-4o"})
        with pytest.raises(ocr_extract.OCRExtractError):
            asyncio.run(ocr_extract._vision_raw([("image/png", "x")], "p"))

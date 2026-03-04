"""Tests for post-voucher date correction in chat.

When a voucher has just been suggested, the user should be able to correct
the date by saying things like 'the date was 4th of Esfand' or 'date is 1404/12/04'.
"""
from __future__ import annotations

import pytest

from app.utils.jalali import try_parse_jalali, jalali_to_gregorian


class TestPostVoucherDateParsing:
    """Verify the date parsing that feeds into the chat date-correction handler."""

    def test_jalali_numeric(self):
        result = try_parse_jalali("1404/12/04")
        assert result == jalali_to_gregorian(1404, 12, 4)

    def test_english_ordinal_month_name(self):
        result = try_parse_jalali("4th of Esfand")
        assert result is not None

    def test_persian_month_day(self):
        result = try_parse_jalali("4 اسفند")
        assert result is not None

    def test_iso_date(self):
        from datetime import date

        result = try_parse_jalali("2026-02-23")
        assert result is None  # ISO dates should not match Jalali parser

    def test_non_date_text(self):
        result = try_parse_jalali("the amount was 5M")
        assert result is None

    def test_date_in_sentence(self):
        result = try_parse_jalali("the date was 4th of Esfand")
        assert result is not None


class TestChatDateCorrectionEndpoint:
    """Test the chat endpoint's post-voucher date correction via API."""

    def _send_chat(self, auth_client, messages):
        resp = auth_client.post("/transactions/chat", json={
            "messages": messages,
        })
        return resp

    def test_date_correction_after_voucher(self, auth_client):
        messages = [
            {"role": "user", "content": "paid 5M from melli bank for rent"},
            {
                "role": "assistant",
                "content": "Here's the voucher I prepared for you.",
            },
            {"role": "user", "content": "the date was 1404/12/04"},
        ]
        resp = self._send_chat(auth_client, messages)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data.get("form_updates") and data["form_updates"].get("date"):
            assert "2026-02-23" in data["form_updates"]["date"]

    def test_non_date_after_voucher_not_correction(self, auth_client):
        messages = [
            {"role": "user", "content": "paid 5M for rent"},
            {
                "role": "assistant",
                "content": "Here's the voucher ready for recording.",
            },
            {"role": "user", "content": "the amount was actually 6M"},
        ]
        resp = self._send_chat(auth_client, messages)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data.get("form_updates"):
            assert data["form_updates"].get("date") is None

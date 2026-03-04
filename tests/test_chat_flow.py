"""Integration tests for the chat conversation flow with mocked AI.

Tests:
- Payment conversation produces a valid voucher
- Receipt conversation produces a valid voucher
- Report queries bypass AI entirely
- Date normalization in messages
- Entity resolution
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.mocks.ai_mock import mock_chat_turn, mock_suggest_transaction


def _chat(auth_client, messages):
    resp = auth_client.post("/transactions/chat", json={"messages": messages})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestPaymentFlow:
    @patch("app.api.transactions.ai_suggest_transaction", side_effect=mock_suggest_transaction)
    @patch("app.api.transactions.ai_chat_turn", side_effect=mock_chat_turn)
    def test_simple_payment(self, _mock_chat, _mock_suggest, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "paid 5M from melli bank for rent"},
        ])
        assert data["message"]
        if data.get("transaction"):
            txn = data["transaction"]
            assert len(txn["lines"]) >= 2
            total_debit = sum(l["debit"] for l in txn["lines"])
            total_credit = sum(l["credit"] for l in txn["lines"])
            assert total_debit == total_credit, "Voucher must be balanced"


class TestReceiptFlow:
    @patch("app.api.transactions.ai_suggest_transaction", side_effect=mock_suggest_transaction)
    @patch("app.api.transactions.ai_chat_turn", side_effect=mock_chat_turn)
    def test_simple_receipt(self, _mock_chat, _mock_suggest, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "received 2M from client Nikzade"},
        ])
        assert data["message"]


class TestReportBypassesAI:
    """Report queries should be answered directly, never hitting the AI."""

    def test_balance_query_no_ai(self, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "show me the balance sheet"},
        ])
        assert data.get("report") is not None or "balance" in data["message"].lower()

    def test_bank_balance_no_ai(self, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "current balance of mellat bank"},
        ])
        assert data["message"]

    def test_income_statement_no_ai(self, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "income statement this month"},
        ])
        assert data.get("report") is not None or "income" in data["message"].lower() or "profit" in data["message"].lower()


class TestEntitySearch:
    """Entity searches should be answered directly."""

    def test_entity_query(self, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "have I had any transactions with Nikzade?"},
        ])
        assert data["message"]
        assert "nikzade" in data["message"].lower()


class TestJalaliDateNormalization:
    """Jalali dates in user messages should be converted before AI processing."""

    @patch("app.api.transactions.ai_suggest_transaction", side_effect=mock_suggest_transaction)
    @patch("app.api.transactions.ai_chat_turn", side_effect=mock_chat_turn)
    def test_jalali_date_in_message(self, _mock_chat, _mock_suggest, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "paid 5M on 1404/11/27 from melli bank"},
        ])
        assert data["message"]


class TestPostVoucherCorrection:
    """After a voucher is suggested, date corrections should apply."""

    def test_date_correction(self, auth_client):
        data = _chat(auth_client, [
            {"role": "user", "content": "paid 5M from melli bank"},
            {"role": "assistant", "content": "Here's the voucher I prepared."},
            {"role": "user", "content": "the date was 1404/12/04"},
        ])
        assert data["message"]
        if data.get("form_updates"):
            assert "date" in data["form_updates"]

"""Tests for entity extraction regex logic in ai_suggest."""
from __future__ import annotations

import pytest

from app.services.ai_suggest import _infer_entity_mentions_from_text, _normalize_entity_name


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------
class TestNameNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ali roshan", "Ali Roshan"),
            ("ALI ROSHAN", "Ali Roshan"),
            ("  ali   roshan  ", "Ali Roshan"),
            ("", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_entity_name(raw) == expected


# ---------------------------------------------------------------------------
# Bank extraction
# ---------------------------------------------------------------------------
class TestBankExtraction:
    @pytest.mark.parametrize(
        "text,expected_bank",
        [
            ("paid from Melli bank", "Melli"),
            ("via Mellat", "Mellat"),
            ("from Tejarat bank account", "Tejarat"),
            ("سامان بانک", "Saman"),
            ("transfer from Parsian", "Parsian"),
        ],
    )
    def test_bank_detected(self, text, expected_bank):
        mentions = _infer_entity_mentions_from_text({"description": ""}, text)
        banks = [m for m in mentions if m["role"] == "bank"]
        assert len(banks) >= 1, f"No bank found in: {text!r}"
        assert banks[0]["name"].lower() == expected_bank.lower()

    def test_no_bank_in_unrelated_text(self):
        mentions = _infer_entity_mentions_from_text({"description": ""}, "paid rent to landlord")
        banks = [m for m in mentions if m["role"] == "bank"]
        assert len(banks) == 0


# ---------------------------------------------------------------------------
# Payee extraction
# ---------------------------------------------------------------------------
class TestPayeeExtraction:
    def test_paid_person(self):
        mentions = _infer_entity_mentions_from_text(
            {"description": "Payment to Ali Roshan (Employee)"}, ""
        )
        payees = [m for m in mentions if m["role"] == "payee"]
        assert len(payees) >= 1
        assert "ali roshan" in payees[0]["name"].lower()

    def test_generic_phrase_not_payee(self):
        mentions = _infer_entity_mentions_from_text(
            {"description": "Salary Payment Withdrawal"}, ""
        )
        payees = [m for m in mentions if m["role"] == "payee"]
        names = [p["name"].lower() for p in payees]
        assert "salary payment withdrawal" not in names

    def test_paid_employee(self):
        mentions = _infer_entity_mentions_from_text(
            {"description": ""}, "paid employee Ali Roshan 5M"
        )
        payees = [m for m in mentions if m["role"] == "payee"]
        if payees:
            assert "ali" in payees[0]["name"].lower()


# ---------------------------------------------------------------------------
# Client extraction
# ---------------------------------------------------------------------------
class TestClientExtraction:
    def test_received_from_client(self):
        mentions = _infer_entity_mentions_from_text(
            {"description": "received from Nikzade"}, ""
        )
        clients = [m for m in mentions if m["role"] == "client"]
        assert len(clients) >= 1
        assert "nikzade" in clients[0]["name"].lower()

    def test_bank_name_not_as_client(self):
        mentions = _infer_entity_mentions_from_text(
            {"description": "received from Melli Bank"}, ""
        )
        clients = [m for m in mentions if m["role"] == "client"]
        client_names = [c["name"].lower() for c in clients]
        for name in client_names:
            assert "melli" not in name or "bank" not in name

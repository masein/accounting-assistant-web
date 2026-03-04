"""Tests for entity transaction search patterns."""
from __future__ import annotations

import pytest

from app.api.transactions import _parse_entity_transaction_query


class TestEntityQueryParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("transactions with Nikzade", "nikzade"),
            ("have I had any transactions with Ali Roshan?", "ali roshan"),
            ("show me dealings with supplier X", "supplier x"),
            ("any payments from Ardeshir?", "ardeshir"),
            ("find transactions involving client Hosseini", "client hosseini"),
        ],
    )
    def test_entity_detected(self, text, expected):
        result = _parse_entity_transaction_query(text)
        assert result is not None, f"Failed to parse: {text!r}"
        assert result.lower() == expected

    @pytest.mark.parametrize(
        "text",
        [
            "transactions with Melli bank",
            "transactions with Mellat bank",
            "show me Parsian bank transactions",
            "paid 5M from melli bank",
            "hello",
            "",
        ],
    )
    def test_bank_and_irrelevant_excluded(self, text):
        result = _parse_entity_transaction_query(text)
        assert result is None, f"Should not match: {text!r} -> {result}"

    def test_case_insensitive(self):
        r1 = _parse_entity_transaction_query("transactions with NIKZADE")
        r2 = _parse_entity_transaction_query("transactions with nikzade")
        assert r1 == r2

    def test_question_mark_stripped(self):
        result = _parse_entity_transaction_query("any transactions with Ali?")
        assert result is not None
        assert "?" not in result

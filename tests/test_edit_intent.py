"""Tests for transaction edit intent parsing (fallback regex path)."""
from __future__ import annotations

import pytest

from app.services.ai_suggest import _fallback_edit_intent


def _parse(text: str) -> dict:
    return _fallback_edit_intent([{"role": "user", "content": text}])


# ---------------------------------------------------------------------------
# Date changes with Jalali
# ---------------------------------------------------------------------------
class TestJalaliDateChanges:
    def test_change_date_to_jalali(self):
        r = _parse("change date of the transaction to 1404/11/27")
        assert r["intent"] == "edit_transaction"
        assert r["changes"].get("date") == "2026-02-16"

    def test_update_date_jalali(self):
        r = _parse("update date to 1404/12/05")
        assert r["intent"] == "edit_transaction"
        assert r["changes"].get("date") == "2026-02-24"

    def test_set_date_jalali(self):
        r = _parse("set date to 1404/12/03")
        assert r["intent"] == "edit_transaction"
        assert r["changes"].get("date") == "2026-02-22"

    def test_persian_digits(self):
        r = _parse("change date to ۱۴۰۴/۱۱/۲۷")
        assert r["intent"] == "edit_transaction"
        assert r["changes"].get("date") == "2026-02-16"


class TestISODateChanges:
    def test_change_date_iso(self):
        r = _parse("change date to 2026-02-16")
        assert r["intent"] == "edit_transaction"
        assert r["changes"].get("date") == "2026-02-16"


# ---------------------------------------------------------------------------
# Search by Jalali date
# ---------------------------------------------------------------------------
class TestSearchByDate:
    def test_edit_with_jalali_search(self):
        r = _parse("edit transaction on 1404/11/27 set bank to Melli")
        assert r["intent"] == "edit_transaction"
        assert r["search"].get("date") == "2026-02-16"
        assert len(r["entity_updates"]) >= 1

    def test_relative_date_search(self):
        for phrase in ["yesterday", "today", "last week", "last month"]:
            r = _parse(f"edit transaction from {phrase}")
            assert r["intent"] == "edit_transaction"


# ---------------------------------------------------------------------------
# Entity / reference / description updates
# ---------------------------------------------------------------------------
class TestFieldUpdates:
    def test_set_bank(self):
        r = _parse("edit this entry set bank to Melli")
        assert r["intent"] == "edit_transaction"
        updates = {u["role"]: u["name"] for u in r["entity_updates"]}
        assert updates.get("bank") == "Melli"

    def test_set_reference(self):
        r = _parse("change reference to INV-001")
        assert r["intent"] == "edit_transaction"
        assert r["changes"].get("reference") == "INV-001"

    def test_set_description(self):
        r = _parse("update description to Rent payment for January")
        assert r["intent"] == "edit_transaction"
        assert "Rent" in r["changes"].get("description", "")


# ---------------------------------------------------------------------------
# Non-edit messages
# ---------------------------------------------------------------------------
class TestNonEdit:
    def test_payment_not_edit(self):
        r = _parse("paid 5M from melli bank")
        assert r["intent"] == "other"

    def test_greeting_not_edit(self):
        r = _parse("hello")
        assert r["intent"] == "other"

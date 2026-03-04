"""Integration tests for transaction CRUD endpoints."""
from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class TestCreateTransaction:
    def test_create_balanced(self, auth_client):
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "Test balanced entry",
            "lines": [
                {"account_code": "1110", "debit": 500000, "credit": 0},
                {"account_code": "4110", "debit": 0, "credit": 500000},
            ],
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert len(data["lines"]) == 2
        assert data["description"] == "Test balanced entry"

    def test_reject_unbalanced(self, auth_client):
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "Unbalanced",
            "lines": [
                {"account_code": "1110", "debit": 500000, "credit": 0},
                {"account_code": "4110", "debit": 0, "credit": 300000},
            ],
        })
        assert resp.status_code == 400
        assert "must equal" in resp.json()["detail"].lower()

    def test_reject_zero_transaction(self, auth_client):
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "Zero amounts",
            "lines": [
                {"account_code": "1110", "debit": 0, "credit": 0},
                {"account_code": "4110", "debit": 0, "credit": 0},
            ],
        })
        assert resp.status_code == 400
        assert "non-zero" in resp.json()["detail"].lower()

    def test_reject_negative_amounts(self, auth_client):
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "Negative amounts",
            "lines": [
                {"account_code": "1110", "debit": -500000, "credit": 0},
                {"account_code": "4110", "debit": 0, "credit": -500000},
            ],
        })
        assert resp.status_code == 422

    def test_reject_unknown_account_code(self, auth_client):
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "Bad account",
            "lines": [
                {"account_code": "9999", "debit": 500000, "credit": 0},
                {"account_code": "4110", "debit": 0, "credit": 500000},
            ],
        })
        assert resp.status_code in (400, 404)

    def test_single_line_rejected(self, auth_client):
        """At least 2 lines required for double-entry."""
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "lines": [],
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------
class TestListTransactions:
    def test_list_returns_array(self, auth_client):
        resp = auth_client.get("/transactions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_pagination(self, auth_client):
        resp = auth_client.get("/transactions?skip=0&limit=1")
        assert resp.status_code == 200
        assert len(resp.json()) <= 1


class TestGetTransaction:
    def test_get_nonexistent(self, auth_client):
        resp = auth_client.get(f"/transactions/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
class TestUpdateTransaction:
    def _create(self, auth_client) -> str:
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "To update",
            "lines": [
                {"account_code": "1110", "debit": 100000, "credit": 0},
                {"account_code": "6112", "debit": 0, "credit": 100000},
            ],
        })
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_update_date(self, auth_client):
        tid = self._create(auth_client)
        resp = auth_client.patch(f"/transactions/{tid}", json={"date": "2026-02-20"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["date"] == "2026-02-20"

    def test_update_description(self, auth_client):
        tid = self._create(auth_client)
        resp = auth_client.patch(f"/transactions/{tid}", json={"description": "Updated desc"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated desc"

    def test_update_lines_unbalanced_rejected(self, auth_client):
        tid = self._create(auth_client)
        resp = auth_client.patch(f"/transactions/{tid}", json={
            "lines": [
                {"account_code": "1110", "debit": 200000, "credit": 0},
                {"account_code": "6112", "debit": 0, "credit": 100000},
            ],
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
class TestDeleteTransaction:
    def test_delete(self, auth_client):
        resp = auth_client.post("/transactions", json={
            "date": "2026-01-15",
            "description": "To delete",
            "lines": [
                {"account_code": "1110", "debit": 50000, "credit": 0},
                {"account_code": "6112", "debit": 0, "credit": 50000},
            ],
        })
        assert resp.status_code == 201
        tid = resp.json()["id"]
        resp = auth_client.delete(f"/transactions/{tid}")
        assert resp.status_code == 204

    def test_delete_nonexistent(self, auth_client):
        resp = auth_client.delete(f"/transactions/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
class TestImportTransactions:
    def test_import_batch(self, auth_client):
        resp = auth_client.post("/transactions/import", json={
            "transactions": [
                {
                    "date": "2026-01-10",
                    "description": "Import 1",
                    "lines": [
                        {"account_code": "1110", "debit": 100000, "credit": 0},
                        {"account_code": "4110", "debit": 0, "credit": 100000},
                    ],
                },
                {
                    "date": "2026-01-11",
                    "description": "Import 2",
                    "lines": [
                        {"account_code": "6112", "debit": 200000, "credit": 0},
                        {"account_code": "1110", "debit": 0, "credit": 200000},
                    ],
                },
            ]
        })
        assert resp.status_code == 200 or resp.status_code == 201
        data = resp.json()
        assert data["imported"] == 2
        assert len(data["ids"]) == 2

    def test_import_rejects_negative(self, auth_client):
        resp = auth_client.post("/transactions/import", json={
            "transactions": [
                {
                    "date": "2026-01-10",
                    "lines": [
                        {"account_code": "1110", "debit": -100000, "credit": 0},
                        {"account_code": "4110", "debit": 0, "credit": -100000},
                    ],
                },
            ]
        })
        assert resp.status_code == 422

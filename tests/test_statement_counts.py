"""A statement's rows posted from it, apart from rows found already in the books (chat review 2026-11-10, CF1).

From production: a school's statement showed 140 rows "matched" when 138 of them had been posted from the
statement itself, and none were left unposted by the count's own reckoning. Figures made up.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.db.tenant import use_company
from app.models.bank_statement import BankStatement, BankStatementRow
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_mobile_chat import phone  # noqa: F401  (fixture)


def test_cf1_posted_found_and_open(client, db, co, phone):
    def entry(amount):
        r = client.post("/transactions", headers=phone, json={"date": "2026-08-02", "description": "entry", "lines": [
            {"account_code": "1110", "debit": amount, "credit": 0}, {"account_code": "4110", "debit": 0, "credit": amount}]})
        assert r.status_code == 201, r.text
        return uuid.UUID(r.json()["id"])
    posted, found = entry(100_000), entry(200_000)
    with use_company(co["cid"]):
        s = BankStatement(bank_name="Test", source_type="csv", source_filename=f"{uuid.uuid4().hex[:6]}.csv",
                          currency="IRR", from_date=date(2026, 8, 1), to_date=date(2026, 8, 3), status="reviewing",
                          total_rows=3, matched_rows=2, new_rows=1)
        db.add(s)
        db.flush()
        for i, (credit, status, made, matched) in enumerate([(100_000, "matched", posted, posted),
                                                             (200_000, "matched", None, found),
                                                             (300_000, "unmatched", None, None)], start=1):
            db.add(BankStatementRow(statement_id=s.id, row_index=i, tx_date=date(2026, 8, i), description=f"row {i}",
                                    debit=0, credit=credit, confidence=1.0, recon_status=status,
                                    created_transaction_id=made, matched_transaction_id=matched))
        db.commit()
        sid = str(s.id)
    one = client.get(f"/brain/bank-statements/{sid}", headers=phone).json()
    assert (one["posted_rows"], one["found_rows"], one["matched_rows"]) == (1, 1, 2)
    listed = next(x for x in client.get("/brain/bank-statements", headers=phone).json() if x["id"] == sid)
    assert (listed["posted_rows"], listed["found_rows"]) == (1, 1)

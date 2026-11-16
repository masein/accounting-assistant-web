"""A statement's rows in the order they happened, whichever way it lists them (scenarios CK1, CK2).

A bank's PDF statement lists the newest row first. The review took the row with the highest number on the last
date as the closing one — on such a statement the *earliest* of that day — and reported a gap that wasn't there.
The running balances say the order; without them, the dates.
Figures made up.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.db.tenant import use_company
from app.models.bank_statement import BankStatement, BankStatementRow
from app.services.statement_checks import in_time_order, row_order
from tests.test_ai_guardrails import co  # noqa: F401  (fixture)
from tests.test_statement_step_by_step import _day_bank

D1, D2 = date(2026, 8, 3), date(2026, 8, 4)
# what happened, oldest first: (date, debit, credit, balance after) — from an opening balance of 1,000,000
HAPPENED = [(D1, 0, 500_000, 1_500_000), (D2, 1_000, 0, 1_499_000), (D2, 1_000, 0, 1_498_000),
            (D2, 0, 200_000, 1_698_000), (D2, 0, 300_000, 1_998_000)]


class _Row:
    def __init__(self, i, d, debit, credit, balance):
        self.row_index, self.tx_date, self.debit, self.credit, self.balance = i, d, debit, credit, balance


def _listed(newest_first: bool, *, balances: bool = True) -> list[_Row]:
    seq = list(reversed(HAPPENED)) if newest_first else HAPPENED
    return [_Row(i, d, dr, cr, bal if balances else None) for i, (d, dr, cr, bal) in enumerate(seq, start=1)]


def test_ck1_the_order_from_the_running_balances():
    newest, oldest = _listed(True), _listed(False)
    assert row_order(newest) == "newest_first" and row_order(oldest) == "oldest_first"
    assert [r.balance for r in in_time_order(newest)] == [h[3] for h in HAPPENED]
    assert [r.balance for r in in_time_order(oldest)] == [h[3] for h in HAPPENED]
    assert in_time_order(newest)[-1].row_index == 1 and in_time_order(oldest)[-1].row_index == 5


def test_ck1_without_balances_the_dates_say_it():
    assert row_order(_listed(True, balances=False)) == "newest_first"
    assert row_order(_listed(False, balances=False)) == "oldest_first"
    one_day = [_Row(1, D2, 0, 5, None), _Row(2, D2, 0, 6, None)]
    assert row_order(one_day) == "oldest_first"                     # nothing to tell by: as listed


def _statement(db, co, newest_first: bool) -> BankStatement:
    with use_company(co["cid"]):
        s = BankStatement(bank_name="Day", source_type="pdf", source_filename=f"{uuid.uuid4().hex[:6]}.pdf",
                          currency="IRR", from_date=D1, to_date=D2, status="parsed", total_rows=len(HAPPENED))
        db.add(s)
        db.flush()
        for r in _listed(newest_first):
            db.add(BankStatementRow(statement_id=s.id, row_index=r.row_index, tx_date=r.tx_date,
                                    description=f"CK row {r.row_index}", debit=r.debit, credit=r.credit,
                                    balance=r.balance, confidence=1.0, recon_status="unmatched", category="misc"))
        db.commit()
        return s


def test_ck1_the_review_closes_on_the_last_row_that_happened(db, co):
    from app.services.statement_review import build_statement_review
    _day_bank(db, co)
    for newest_first in (True, False):
        s = _statement(db, co, newest_first)
        with use_company(co["cid"]):
            bal = build_statement_review(db, s).balance
        assert bal is not None and bal.statement_closing == 1_998_000, (newest_first, bal)


def test_ck2_a_day_voucher_lists_its_rows_as_they_happened(db, co):
    from app.services.ai_accountant.statement_posting import plan
    bank = _day_bank(db, co)
    for newest_first in (True, False):
        s = _statement(db, co, newest_first)
        with use_company(co["cid"]):
            out = plan(db, s, bank=bank, money_in="4110", money_out="6112", rules=[], per="day")
        assert [v["date"] for v in out["vouchers"]] == [D1.isoformat(), D2.isoformat()]
        day2 = [ln for ln in out["vouchers"][1]["lines"] if ln[0] == bank]
        # the bank's side of each row on 4 Aug, in the order they happened: −1,000, −1,000, +200,000, +300,000
        assert [(dr, cr) for _c, dr, cr, _d in day2] == [(0, 1_000), (0, 1_000), (200_000, 0), (300_000, 0)], newest_first

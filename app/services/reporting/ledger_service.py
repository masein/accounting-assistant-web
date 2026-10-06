from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.schemas.manager_report import (
    AccountLedgerResponse,
    CashBankStatementResponse,
    CashBankStatementRow,
    JournalEntryRead,
    JournalLineRead,
    LedgerAccountSummary,
    LedgerDetailRow,
    PaginatedJournalResponse,
    ReportPeriod,
    TrialBalanceResponse,
    TrialBalanceRow,
)
from app.services.reporting.common import ASSET, EXPENSE, OTHER, balance_from_turnovers, classify_account_code, default_period
from app.services.reporting.repository import (
    opening_balance_before,
    paged_account_lines,
    paged_journal_entries,
    trial_balance_rows,
)
from app.services.reporting.repository import line_dr_cr, resolve_currency_view
from app.services.book_text import bt


def _to_journal_item(txn: Transaction, currency: str | None = None) -> JournalEntryRead:
    lines = [
        JournalLineRead(
            account_code=ln.account.code,
            account_name=ln.account.name,
            debit=line_dr_cr(ln, currency)[0],
            credit=line_dr_cr(ln, currency)[1],
            line_description=ln.line_description,
        )
        for ln in txn.lines
    ]
    total_debit = int(sum(ln.debit for ln in lines))
    total_credit = int(sum(ln.credit for ln in lines))
    return JournalEntryRead(
        transaction_id=txn.id,
        date=txn.date,
        reference=txn.reference,
        description=txn.description,
        lines=lines,
        total_debit=total_debit,
        total_credit=total_credit,
    )



def reversed_already(db: Session, transaction_id) -> bool:
    """True when an entry has been reversed (or undone) already: an "undo" audit
    row names it. Every reversal path writes one."""
    from app.models.audit_log import AuditLog
    return db.execute(select(AuditLog.id).where(
        AuditLog.action == "undo", AuditLog.entity_type == "transaction",
        AuditLog.entity_id == str(transaction_id))).first() is not None

class LedgerService:
    def __init__(self, db: Session):
        self.db = db

    def general_journal(self, from_date: date | None, to_date: date | None, page: int = 1, page_size: int = 50, currency: str | None = None) -> PaginatedJournalResponse:
        period = default_period(from_date, to_date)
        total, items = paged_journal_entries(self.db, period.from_date, period.to_date, page, page_size, currency=currency)
        return PaginatedJournalResponse(
            report_type="general_journal",
            page=page,
            page_size=page_size,
            total=total,
            items=[_to_journal_item(t, currency) for t in items],
        )

    def account_ledger(self, account_code: str, from_date: date | None, to_date: date | None, page: int = 1, page_size: int = 100, currency: str | None = None) -> AccountLedgerResponse:
        period = default_period(from_date, to_date)
        acc, total, rows = paged_account_lines(self.db, account_code, period.from_date, period.to_date, page, page_size, currency=currency)
        if not acc:
            raise HTTPException(status_code=404, detail=f"Account not found: {account_code}")

        opening_debit, opening_credit = opening_balance_before(self.db, acc.id, period.from_date, currency=currency)
        acc_type = classify_account_code(acc.code)
        running = balance_from_turnovers(acc_type, opening_debit, opening_credit)
        out_rows: list[LedgerDetailRow] = []
        debit_turnover = 0
        credit_turnover = 0
        for line, txn in rows:
            debit, credit = line_dr_cr(line, currency)
            debit_turnover += debit
            credit_turnover += credit
            if acc_type in (ASSET, EXPENSE, OTHER):
                running += debit - credit
            else:
                running += credit - debit
            out_rows.append(
                LedgerDetailRow(
                    date=txn.date,
                    transaction_id=txn.id,
                    reference=txn.reference,
                    description=txn.description,
                    debit=debit,
                    credit=credit,
                    running_balance=running,
                    line_description=line.line_description,
                )
            )

        return AccountLedgerResponse(
            report_type="account_ledger",
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            account=LedgerAccountSummary(
                account_code=acc.code,
                account_name=acc.name,
                debit_turnover=debit_turnover,
                credit_turnover=credit_turnover,
                balance=running,
            ),
            page=page,
            page_size=page_size,
            total=total,
            items=out_rows,
        )

    def general_ledger(self, from_date: date | None, to_date: date | None, page: int = 1, page_size: int = 200, currency: str | None = None) -> TrialBalanceResponse:
        return self.trial_balance(from_date=from_date, to_date=to_date, page=page, page_size=page_size, report_type="general_ledger", currency=currency)

    def trial_balance(
        self,
        from_date: date | None,
        to_date: date | None,
        page: int = 1,
        page_size: int = 200,
        report_type: str = "trial_balance",
        currency: str | None = None,
    ) -> TrialBalanceResponse:
        if from_date is None and to_date is not None:
            # "As at" a date: every posting up to it. It used to start on the
            # 1st of that month, so a trial balance as at 31 December showed
            # only December — empty when nothing was posted then (deep browser
            # test, 2026-10-02, finding #30).
            from sqlalchemy import func
            first = self.db.execute(select(func.min(Transaction.date)).where(Transaction.deleted_at.is_(None))).scalar()
            from_date = min(first, to_date) if first else to_date
        period = default_period(from_date, to_date)
        # Single-currency view: the reporting currency unless asked otherwise;
        # other currencies in the period are listed, never summed in.
        currency, other_currencies = resolve_currency_view(self.db, currency, period.from_date, period.to_date)
        rows = trial_balance_rows(self.db, period.from_date, period.to_date, currency=currency)
        total = len(rows)
        offset = max(0, (page - 1) * page_size)
        window = rows[offset : offset + page_size]
        out_rows: list[TrialBalanceRow] = []
        td_turn = tc_turn = td_bal = tc_bal = 0
        for code, name, d_turn, c_turn in window:
            net = d_turn - c_turn
            dbal = net if net > 0 else 0
            cbal = -net if net < 0 else 0
            td_turn += d_turn
            tc_turn += c_turn
            td_bal += dbal
            tc_bal += cbal
            out_rows.append(
                TrialBalanceRow(
                    account_code=code,
                    account_name=name,
                    debit_turnover=d_turn,
                    credit_turnover=c_turn,
                    debit_balance=dbal,
                    credit_balance=cbal,
                )
            )
        return TrialBalanceResponse(
            report_type=report_type,
            currency=currency,
            other_currencies=other_currencies,
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            page=page,
            page_size=page_size,
            total=total,
            rows=out_rows,
            totals={
                "debit_turnover": td_turn,
                "credit_turnover": tc_turn,
                "debit_balance": td_bal,
                "credit_balance": tc_bal,
            },
        )

    def cash_bank_statement(
        self,
        account_code: str = "1110",
        from_date: date | None = None,
        to_date: date | None = None,
        page: int = 1,
        page_size: int = 100,
        currency: str | None = None,
    ) -> CashBankStatementResponse:
        report = self.account_ledger(account_code=account_code, from_date=from_date, to_date=to_date, page=page, page_size=page_size, currency=currency)
        rows = [
            CashBankStatementRow(
                date=r.date,
                transaction_id=r.transaction_id,
                reference=r.reference,
                description=r.description,
                debit=r.debit,
                credit=r.credit,
                running_balance=r.running_balance,
            )
            for r in report.items
        ]
        return CashBankStatementResponse(
            report_type="cash_bank_statement",
            period=report.period,
            account=report.account,
            page=report.page,
            page_size=report.page_size,
            total=report.total,
            rows=rows,
        )

    def reverse_journal_entry(
        self,
        transaction_id: UUID,
        reverse_date: date | None = None,
        reference: str | None = None,
        description: str | None = None,
        *,
        mark_undo: bool = True,
    ) -> JournalEntryRead:
        """Post the opposite of an entry. One reversal per entry: a second one is
        refused. The ledger's reverse route posted another each time it was called,
        and a void afterwards reversed the entry yet again (security review,
        2026-10-06). The "undo" audit row is the mark every reversal path checks;
        ``mark_undo=False`` is for callers that write their own, with more detail."""
        if reversed_already(self.db, transaction_id):
            raise HTTPException(status_code=409, detail="This entry has already been reversed.")
        src = self.db.execute(
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        ).scalars().one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail="Transaction not found")
        from app.services.period_service import assert_period_open
        rev_date = reverse_date or date.today()
        assert_period_open(self.db, rev_date)  # the reversal is a new posting (review H7)
        # At the original rate and base values (roadmap §4.6): a reversal
        # undoes the entry in the base currency too, realised FX included.
        rev = Transaction(
            date=rev_date,
            reference=(reference or (f"REV-{src.reference}" if src.reference else f"REV-{src.id.hex[:8]}"))[:128],
            description=(description or bt(self.db, "reversal_of", ref=src.reference or str(src.id)[:8])),
            currency=src.currency,
            fx_rate=src.fx_rate,
            fx_role=src.fx_role,
        )
        self.db.add(rev)
        self.db.flush()
        for line in src.lines:
            self.db.add(
                TransactionLine(
                    transaction_id=rev.id,
                    account_id=line.account_id,
                    debit=int(line.credit or 0),
                    credit=int(line.debit or 0),
                    base_debit=line.base_credit,
                    base_credit=line.base_debit,
                    line_description=(line.line_description or bt(self.db, "reversal")),
                )
            )
        if mark_undo:
            from app.services.audit_service import log_audit_event
            log_audit_event(self.db, action="undo", entity_type="transaction", entity_id=str(transaction_id),
                            detail=f"Reversed by {rev.reference}")
        self.db.commit()
        self.db.refresh(rev)
        loaded = self.db.execute(
            select(Transaction).where(Transaction.id == rev.id).options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        ).scalars().one()
        return _to_journal_item(loaded)

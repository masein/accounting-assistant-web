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


def _to_journal_item(txn: Transaction) -> JournalEntryRead:
    lines = [
        JournalLineRead(
            account_code=ln.account.code,
            account_name=ln.account.name,
            debit=int(ln.debit or 0),
            credit=int(ln.credit or 0),
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


class LedgerService:
    def __init__(self, db: Session):
        self.db = db

    def general_journal(self, from_date: date | None, to_date: date | None, page: int = 1, page_size: int = 50) -> PaginatedJournalResponse:
        period = default_period(from_date, to_date)
        total, items = paged_journal_entries(self.db, period.from_date, period.to_date, page, page_size)
        return PaginatedJournalResponse(
            report_type="general_journal",
            page=page,
            page_size=page_size,
            total=total,
            items=[_to_journal_item(t) for t in items],
        )

    def account_ledger(self, account_code: str, from_date: date | None, to_date: date | None, page: int = 1, page_size: int = 100) -> AccountLedgerResponse:
        period = default_period(from_date, to_date)
        acc, total, rows = paged_account_lines(self.db, account_code, period.from_date, period.to_date, page, page_size)
        if not acc:
            raise HTTPException(status_code=404, detail=f"Account not found: {account_code}")

        opening_debit, opening_credit = opening_balance_before(self.db, acc.id, period.from_date)
        acc_type = classify_account_code(acc.code)
        running = balance_from_turnovers(acc_type, opening_debit, opening_credit)
        out_rows: list[LedgerDetailRow] = []
        debit_turnover = 0
        credit_turnover = 0
        for line, txn in rows:
            debit = int(line.debit or 0)
            credit = int(line.credit or 0)
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

    def general_ledger(self, from_date: date | None, to_date: date | None, page: int = 1, page_size: int = 200) -> TrialBalanceResponse:
        return self.trial_balance(from_date=from_date, to_date=to_date, page=page, page_size=page_size, report_type="general_ledger")

    def trial_balance(
        self,
        from_date: date | None,
        to_date: date | None,
        page: int = 1,
        page_size: int = 200,
        report_type: str = "trial_balance",
    ) -> TrialBalanceResponse:
        period = default_period(from_date, to_date)
        rows = trial_balance_rows(self.db, period.from_date, period.to_date)
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
    ) -> CashBankStatementResponse:
        report = self.account_ledger(account_code=account_code, from_date=from_date, to_date=to_date, page=page, page_size=page_size)
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
    ) -> JournalEntryRead:
        src = self.db.execute(
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        ).scalars().one_or_none()
        if not src:
            raise HTTPException(status_code=404, detail="Transaction not found")
        rev = Transaction(
            date=reverse_date or date.today(),
            reference=(reference or (f"REV-{src.reference}" if src.reference else f"REV-{src.id.hex[:8]}"))[:128],
            description=(description or f"Reversal of {src.id}"),
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
                    line_description=(line.line_description or "Reversal"),
                )
            )
        self.db.commit()
        self.db.refresh(rev)
        loaded = self.db.execute(
            select(Transaction).where(Transaction.id == rev.id).options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
        ).scalars().one()
        return _to_journal_item(loaded)

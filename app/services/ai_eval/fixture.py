"""The books every eval scenario runs against: a small Iranian company.

Figures are chosen so answers can be checked: cash and bank stand at
2,070,000,000 IRR, INV-1001 (Behsaz, 30,000,000) is open, a 12,000,000 cheque
from Behsaz falls due in five days, and the "Test Bank" statement has one fee
the books don't have yet. All dates are relative to the day of the run.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.bank_statement import BankStatement, BankStatementRow
from app.models.commitment import CHEQUE, PENDING, RECEIVE, Commitment
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from app.models.invoice import Invoice
from app.models.transaction import Transaction, TransactionLine

CASH_ON_HAND = 2_070_000_000
OPEN_INVOICE = "INV-1001"
OPEN_INVOICE_AMOUNT = 30_000_000
CHEQUE_AMOUNT = 12_000_000
BANK_FEE = 250_000


@dataclass
class EvalContext:
    company_id: str
    user_id: str
    today: date
    entities: dict[str, str] = field(default_factory=dict)      # name → id
    statement_id: str = ""
    rows: dict[str, str] = field(default_factory=dict)          # "fee" / "rent" → statement row id
    cheque_id: str = ""

    def placeholders(self) -> dict[str, str]:
        """What a scenario may write as ``{name}`` in its message or replay."""
        out = {"statement_id": self.statement_id, "cheque_id": self.cheque_id,
               "today": self.today.isoformat()}
        for n in range(0, 61):
            out[f"today-{n}"] = (self.today - timedelta(days=n)).isoformat()
            out[f"today+{n}"] = (self.today + timedelta(days=n)).isoformat()
        out.update({f"row:{k}": v for k, v in self.rows.items()})
        out.update({f"entity:{k}": v for k, v in self.entities.items()})
        return out


def _journal(db: Session, acc: dict, on: date, description: str, lines, *, reference: str | None = None,
             party: Entity | None = None, role: str = "client") -> Transaction:
    t = Transaction(id=uuid.uuid4(), date=on, description=description, reference=reference, currency="IRR")
    db.add(t)
    db.flush()                                  # as every writer does before adding lines
    for code, debit, credit in lines:
        db.add(TransactionLine(transaction_id=t.id, account_id=acc[code].id, debit=debit, credit=credit,
                               line_description=description))
    if party is not None:
        db.add(TransactionEntity(transaction_id=t.id, entity_id=party.id, role=role))
    return t


def seed_eval_company(db: Session, *, today: date | None = None, slug: str | None = None) -> EvalContext:
    """Create the eval company and its books; returns what scenarios refer to."""
    today = today or date.today()
    company = Company(id=uuid.uuid4(), name="AI eval books", slug=slug or f"ai-eval-{uuid.uuid4().hex[:8]}",
                      locale="ir", base_currency="IRR", status="active", token_version=0)
    db.add(company)
    db.commit()
    ctx = EvalContext(company_id=str(company.id), user_id=f"ai-eval-{uuid.uuid4().hex[:8]}", today=today)
    with use_company(company.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        parties = {name: Entity(id=uuid.uuid4(), name=name, type=kind, company_id=company.id) for name, kind in (
            ("Aria Trading", "client"), ("Behsaz", "client"), ("Delta Supplies", "supplier"),
            ("Test Bank", "bank"), ("Sara Ahmadi", "employee"))}
        db.add_all(parties.values())
        db.flush()
        ctx.entities = {n: str(e.id) for n, e in parties.items()}

        d = lambda n: today - timedelta(days=n)  # noqa: E731
        _journal(db, acc, d(60), "آورده سرمایه", [("1110", 2_000_000_000, 0), ("3110", 0, 2_000_000_000)],
                 reference="OPEN-1")
        _journal(db, acc, d(40), "Consulting for Aria Trading", [("1112", 300_000_000, 0), ("4110", 0, 300_000_000)],
                 reference="INV-0999", party=parties["Aria Trading"])
        _journal(db, acc, d(20), "Aria Trading paid INV-0999", [("1110", 300_000_000, 0), ("1112", 0, 300_000_000)],
                 reference="RCPT-0999", party=parties["Aria Trading"])
        _journal(db, acc, d(15), "اجاره دفتر", [("6112", 80_000_000, 0), ("1110", 0, 80_000_000)], reference="RENT-1")
        _journal(db, acc, d(10), "حقوق کارکنان", [("6110", 150_000_000, 0), ("1110", 0, 150_000_000)],
                 reference="PAY-1", party=parties["Sara Ahmadi"], role="payee")
        _journal(db, acc, d(5), "Office supplies from Delta Supplies", [("6112", 45_000_000, 0), ("2110", 0, 45_000_000)],
                 reference="BILL-77", party=parties["Delta Supplies"], role="supplier")
        _journal(db, acc, d(20), "Invoice INV-1001 to Behsaz", [("1112", OPEN_INVOICE_AMOUNT, 0),
                                                                ("4110", 0, OPEN_INVOICE_AMOUNT)],
                 reference=OPEN_INVOICE, party=parties["Behsaz"])
        db.add(Invoice(id=uuid.uuid4(), number=OPEN_INVOICE, kind="sales", status="issued", issue_date=d(20),
                       due_date=today + timedelta(days=10), amount=OPEN_INVOICE_AMOUNT, currency="IRR",
                       entity_id=parties["Behsaz"].id, description="Consulting, September"))
        cheque = Commitment(id=uuid.uuid4(), kind=CHEQUE, direction=RECEIVE, title="Behsaz cheque",
                            amount=CHEQUE_AMOUNT, due_date=today + timedelta(days=5), status=PENDING,
                            counterparty="Behsaz", entity_id=parties["Behsaz"].id, reference="CHQ-5501")
        db.add(cheque)
        stmt = BankStatement(bank_name="Test Bank", source_type="csv", source_filename="test-bank.csv",
                             currency="IRR", from_date=d(20), to_date=d(2), status="parsed", total_rows=2)
        db.add(stmt)
        db.flush()
        rent = BankStatementRow(statement_id=stmt.id, row_index=1, tx_date=d(15), description="اجاره دفتر",
                                debit=80_000_000, credit=0, confidence=1.0, recon_status="unmatched")
        fee = BankStatementRow(statement_id=stmt.id, row_index=2, tx_date=d(3), description="کارمزد بانکی",
                               debit=BANK_FEE, credit=0, confidence=1.0, recon_status="unmatched",
                               suggested_account_code="6210", category="bank_fees")
        db.add_all([rent, fee])
        db.commit()
        ctx.statement_id, ctx.rows, ctx.cheque_id = str(stmt.id), {"rent": str(rent.id), "fee": str(fee.id)}, str(cheque.id)
    return ctx

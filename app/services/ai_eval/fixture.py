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
RENT = 80_000_000                          # the monthly rent rule
OPEN_PO = "PO-0007"                        # an order to Delta Supplies, not yet received
SARA_PAY = 150_000_000                     # the PAY-1 wages, as Sara's pay run shows them
BANK_FEE = 250_000


@dataclass
class EvalContext:
    company_id: str
    user_id: str
    today: date
    mode: str = "default"                    # "personal" for a person's books
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
             party: Entity | None = None, role: str = "client", currency: str = "IRR") -> Transaction:
    t = Transaction(id=uuid.uuid4(), date=on, description=description, reference=reference, currency=currency)
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
        wages = _journal(db, acc, d(10), "حقوق کارکنان", [("6110", SARA_PAY, 0), ("1110", 0, SARA_PAY)],
                         reference="PAY-1", party=parties["Sara Ahmadi"], role="payee")
        from app.models.employee_pay import EmployeePayProfile
        from app.models.pay_run import PayRun, PayRunLine
        db.add(EmployeePayProfile(entity_id=parties["Sara Ahmadi"].id, base_salary=SARA_PAY, currency="IRR"))
        run = PayRun(id=uuid.uuid4(), period_start=d(40), period_end=d(10), pay_date=d(10), currency="IRR",
                     status="paid", total_gross=SARA_PAY, total_net=SARA_PAY, pay_transaction_id=wages.id)
        db.add(run)
        db.flush()
        db.add(PayRunLine(run_id=run.id, entity_id=parties["Sara Ahmadi"].id, employee_name="Sara Ahmadi",
                          gross=SARA_PAY, taxable_base=SARA_PAY, net_pay=SARA_PAY))
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
        from app.models.purchase_order import PurchaseOrder, PurchaseOrderLine
        from app.models.recurring import RecurringRule
        db.add(RecurringRule(name="Office rent", direction="payment", frequency="monthly", amount=RENT,
                             start_date=d(15), next_run_date=today + timedelta(days=15), counter_account_code="6112",
                             bank_account_code="1110", auto_post=True, status="active"))
        po = PurchaseOrder(id=uuid.uuid4(), number=OPEN_PO, entity_id=parties["Delta Supplies"].id, order_date=d(3),
                           status="issued", currency="IRR", description="Paper for the office")
        db.add(po)
        db.flush()
        db.add(PurchaseOrderLine(order_id=po.id, description="A4 paper", ordered_qty=10, unit_price=1_500_000,
                                 line_total=15_000_000))
        # every movement of the bank account in the statement's days, so the fee is its one difference: with
        # only the rent and the fee, Aria's receipt and the wages read as missing from the bank and the review
        # rightly opened with them (scenario AY6)
        stmt = BankStatement(bank_name="Test Bank", source_type="csv", source_filename="test-bank.csv",
                             currency="IRR", from_date=d(20), to_date=d(2), status="parsed", total_rows=4)
        db.add(stmt)
        db.flush()
        receipt = BankStatementRow(statement_id=stmt.id, row_index=1, tx_date=d(20), description="واریز آریا ترید",
                                   debit=0, credit=300_000_000, confidence=1.0, recon_status="unmatched")
        rent = BankStatementRow(statement_id=stmt.id, row_index=2, tx_date=d(15), description="اجاره دفتر",
                                debit=80_000_000, credit=0, confidence=1.0, recon_status="unmatched")
        pay = BankStatementRow(statement_id=stmt.id, row_index=3, tx_date=d(10), description="حقوق کارکنان",
                               debit=SARA_PAY, credit=0, confidence=1.0, recon_status="unmatched")
        fee = BankStatementRow(statement_id=stmt.id, row_index=4, tx_date=d(3), description="کارمزد بانکی",
                               debit=BANK_FEE, credit=0, confidence=1.0, recon_status="unmatched",
                               suggested_account_code="6210", category="bank_fees")
        db.add_all([receipt, rent, pay, fee])
        db.commit()
        ctx.statement_id, ctx.rows, ctx.cheque_id = str(stmt.id), {"rent": str(rent.id), "fee": str(fee.id)}, str(cheque.id)
    return ctx


# --- a person's books (2026-10-22): the owner's own shape -----------------------------------------------------
# Books in Iranian rials; Omani rials in two wallets and rials at Bank Mellat, each kept as it is; last week's
# spending in both; Amir paid a Muscat Airbnb in dollars.
GREEN_WALLET, PRETTY_WALLET, MELLAT = "112001", "112002", "111001"
AMIR_AIRBNB = "333.77"


def seed_personal_books(db: Session, *, today: date | None = None, slug: str | None = None) -> EvalContext:
    from decimal import Decimal
    today = today or date.today()
    company = Company(id=uuid.uuid4(), name="AI eval person", slug=slug or f"ai-eval-p-{uuid.uuid4().hex[:8]}",
                      locale="default", base_currency="IRR", status="active", token_version=0, kind="personal")
    db.add(company)
    db.commit()
    ctx = EvalContext(company_id=str(company.id), user_id=f"ai-eval-p-{uuid.uuid4().hex[:8]}", today=today,
                      mode="personal")
    with use_company(company.id):
        seed_chart_if_empty(db, locale="default", chart="personal")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        for code, name, parent in ((GREEN_WALLET, "کیف پول سبز", "1120"), (PRETTY_WALLET, "کیف پول قشنگم", "1120"),
                                   (MELLAT, "حساب ملت", "1110")):
            p = acc[parent]
            acc[code] = Account(id=uuid.uuid4(), code=code, name=name, parent_id=p.id, level=p.level)
            db.add(acc[code])
        amir = Entity(id=uuid.uuid4(), name="امیر", type="supplier", company_id=company.id)
        db.add(amir)
        db.flush()
        ctx.entities = {"امیر": str(amir.id)}
        d = lambda n: today - timedelta(days=n)  # noqa: E731
        omr = lambda v: Decimal(v)  # noqa: E731
        _journal(db, acc, d(30), "موجودی اولیه کیف پول سبز", [(GREEN_WALLET, omr("100"), 0), ("3110", 0, omr("100"))],
                 currency="OMR")
        _journal(db, acc, d(30), "موجودی اولیه کیف پول قشنگم", [(PRETTY_WALLET, omr("60"), 0), ("3110", 0, omr("60"))],
                 currency="OMR")
        _journal(db, acc, d(30), "موجودی اولیه حساب ملت", [(MELLAT, 50_000_000, 0), ("3110", 0, 50_000_000)])
        _journal(db, acc, d(6), "رستوران", [("6180", omr("12.500"), 0), (GREEN_WALLET, 0, omr("12.500"))], currency="OMR")
        _journal(db, acc, d(5), "لولو", [("6110", omr("20.250"), 0), (PRETTY_WALLET, 0, omr("20.250"))], currency="OMR")
        _journal(db, acc, d(5), "تاکسی", [("6130", omr("3.750"), 0), (GREEN_WALLET, 0, omr("3.750"))], currency="OMR")
        _journal(db, acc, d(4), "خرید ماهانه", [("6110", 3_000_000, 0), (MELLAT, 0, 3_000_000)])
        _journal(db, acc, d(8), "Muscat Airbnb — paid by Amir", [("6185", omr(AMIR_AIRBNB), 0), ("2130", 0, omr(AMIR_AIRBNB))],
                 currency="USD", party=amir, role="supplier")
        db.commit()
    return ctx

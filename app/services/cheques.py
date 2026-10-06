"""A cheque's life, in the books (roadmap 2026-09 §3.4).

Received (چک دریافتی)::

    in hand ──deposit──▶ in collection ──clear──▶ cleared
       │  ╲                  │
       │   ╲──bounce──┐      └──bounce──▶ bounced ──deposit again──▶ in collection
       │              ▼                      │
       ├──endorse──▶ endorsed               └──return──▶ returned
       └──return───▶ returned

Issued (چک پرداختی): issued ──clear──▶ cleared, ──bounce──▶ bounced ──return──▶
returned, or ──return──▶ returned while still unpresented.

In an Iranian company's books (``ledger_mode = "notes"``) every step posts:

==================  =====================================  ==================================
step                received cheque                        issued cheque
==================  =====================================  ==================================
received / issued   Dr notes receivable  Cr the customer   Dr the supplier  Cr notes payable
deposit             Dr in collection     Cr notes recv.    —
clear               Dr bank              Cr where it sits  Dr notes payable  Cr bank
bounce              Dr the customer      Cr where it sits  Dr notes payable  Cr the supplier
deposit again       Dr in collection     Cr the customer   —
return (unused)     Dr the customer      Cr notes recv.    Dr notes payable  Cr the supplier
endorse             Dr the endorsee      Cr notes recv.    —
==================  =====================================  ==================================

A cheque given for an invoice or bill *is* its payment: receiving it records
the invoice payment into notes receivable (issuing one, out of notes
payable), a bounce or a return takes that payment off so the invoice reopens,
and depositing again pays it once more. Endorsing a cheque to pay a bill
records that bill's payment out of notes receivable.

Everywhere else (UK and personal books, and cheques recorded before this —
``"direct"``) nothing posts until the cheque clears, then bank ↔ the counter
account (or the invoice's payment into the bank), as before.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commitment import (
    BOUNCED,
    CHEQUE,
    DEPOSITED,
    DIRECT,
    ENDORSED,
    NOTES,
    PAY,
    PENDING,
    RECEIVE,
    RETURNED,
    SETTLED,
    Commitment,
    CommitmentEvent,
)

SAYAD_RE = re.compile(r"^\d{16}$")

# the history's actions
RECEIVED, ISSUED, DEPOSIT, REDEPOSIT, CLEARED, BOUNCE, RETURN, ENDORSE, SAYAD, PRINTED = (
    "received", "issued", "deposited", "redeposited", "cleared", "bounced", "returned", "endorsed",
    "sayad_registered", "printed")


def _refuse(detail: str, status: int = 409):
    raise HTTPException(status_code=status, detail=detail)


# --- the company's books ---------------------------------------------------------------------------------

def _locale(db: Session) -> str:
    from app.services.locale_service import get_reporting_locale
    return (get_reporting_locale(db) or "default").strip().lower()


def _personal(db: Session) -> bool:
    try:
        from app.services.fx_service import _current_company_row
        row = _current_company_row(db)
        return (getattr(row, "kind", None) or "business") == "personal"
    except Exception:  # noqa: BLE001
        return False


def notes_books(db: Session) -> bool:
    """Iranian business books keep cheques in the notes accounts; UK and
    personal books bank them when they clear."""
    return _locale(db) in ("ir", "default") and not _personal(db)


def _code(db: Session, category: str) -> str:
    from app.services.account_resolver import AccountResolutionError, resolve_account_code
    try:
        return resolve_account_code(db, category)
    except AccountResolutionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _bank(db: Session, code: str | None) -> str:
    from app.models.account import Account
    c = (code or "").strip()
    if c:
        if db.execute(select(Account.id).where(Account.code == c)).first() is None:
            _refuse(f"No account {c} in the chart.", 400)
        return c
    return _code(db, "bank")


def _label(db: Session, row: Commitment, what: str) -> str:
    no = row.reference or row.sayad_id
    fa = _locale(db) == "ir"
    words = {
        RECEIVED: ("دریافت چک", "Cheque received"), ISSUED: ("صدور چک", "Cheque issued"),
        DEPOSIT: ("واگذاری چک به بانک", "Cheque deposited"), REDEPOSIT: ("واگذاری مجدد چک", "Cheque deposited again"),
        CLEARED: ("وصول چک", "Cheque cleared") if row.direction == RECEIVE else ("پاس شدن چک", "Cheque cleared"),
        BOUNCE: ("برگشت چک", "Cheque bounced"), RETURN: ("عودت چک", "Cheque returned"),
        ENDORSE: ("خرج چک", "Cheque endorsed"),
    }[what][0 if fa else 1]
    return f"{words}{' ' + no if no else ''} — {row.title}"


def _post(db: Session, row: Commitment, what: str, on: date, debit: str, credit: str):
    from app.schemas.entity import EntityLink
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ledger_posting import create_transaction_from_payload
    links = []
    if row.entity_id:
        links = [EntityLink(entity_id=row.entity_id, role="client" if row.direction == RECEIVE else "supplier")]
    payload = TransactionCreate(
        date=on, reference=(row.reference or row.sayad_id or None), description=_label(db, row, what),
        lines=[TransactionLineCreate(account_code=debit, debit=row.amount, credit=0),
               TransactionLineCreate(account_code=credit, debit=0, credit=row.amount)],
        entity_links=links,
    )
    return create_transaction_from_payload(db, payload)


def _post_lines(db: Session, row: Commitment, what: str, on: date, lines: list):
    """Like _post, with several lines: (account, debit, credit); empty ones dropped."""
    from app.schemas.entity import EntityLink
    from app.schemas.transaction import TransactionCreate, TransactionLineCreate
    from app.services.ledger_posting import create_transaction_from_payload
    links = []
    if row.entity_id:
        links = [EntityLink(entity_id=row.entity_id, role="client" if row.direction == RECEIVE else "supplier")]
    payload = TransactionCreate(
        date=on, reference=(row.reference or row.sayad_id or None), description=_label(db, row, what),
        lines=[TransactionLineCreate(account_code=code, debit=int(dr), credit=int(cr))
               for code, dr, cr in lines if code and (dr or cr)],
        entity_links=links,
    )
    return create_transaction_from_payload(db, payload)


def _event(db: Session, row: Commitment, action: str, on: date, txn=None, note: str | None = None) -> None:
    # stamped here: the database's now() is one instant for a whole transaction
    db.add(CommitmentEvent(commitment_id=row.id, action=action, happened_on=on,
                           transaction_id=getattr(txn, "id", None), note=note,
                           created_at=datetime.now(timezone.utc)))
    db.flush()


# --- invoices -------------------------------------------------------------------------------------------------

def _invoice(db: Session, invoice_id, direction: str):
    from app.models.invoice import Invoice
    from app.services.fx_base import base_currency
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        _refuse("Invoice not found.", 404)
    want = "sales" if direction == RECEIVE else "purchase"
    if inv.kind != want:
        _refuse("A received cheque pays a sales invoice, an issued one pays a bill." if direction == RECEIVE
                else "An issued cheque pays a bill, a received one a sales invoice.", 400)
    if inv.status in ("draft", "canceled", "voided"):
        _refuse(f"Cannot pay a {inv.status} invoice.")
    if (inv.currency or "").upper() != base_currency(db).upper():
        _refuse(f"A cheque is in {base_currency(db)}; this invoice is in {inv.currency}.", 400)
    return inv


def _pay_invoice(db: Session, row: Commitment, inv, on: date, account: str, action: str):
    """The invoice's payment by this cheque, into (or out of) ``account``."""
    from app.api.invoices import _apply_payment
    payment, _credit = _apply_payment(db, inv, amount=int(row.amount), on=on, method="cheque",
                                      bank_code=account, reference=row.reference or row.sayad_id or inv.number,
                                      description=_label(db, row, action))
    db.flush()
    return payment


def _unpay_invoice(db: Session, row: Commitment) -> None:
    from app.api.invoices import detach_payment
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    if not row.payment_id:
        return
    payment = db.get(Payment, row.payment_id)
    row.payment_id = None
    if payment is not None:
        detach_payment(db, db.get(Invoice, payment.invoice_id), payment)


def _counter(db: Session, row: Commitment) -> str:
    if row.counter_account_code:
        return row.counter_account_code
    _refuse("This cheque has no account to post against.", 400)


def _party(db: Session, row: Commitment) -> str:
    """Whom the cheque's money is owed by (or to) once the cheque no longer
    pays: its counter account when that is a receivable or payable, else the
    trade receivables / payables — a bounced rent cheque does not undo the
    rent, the landlord is still owed."""
    from app.services.reporting.common import ASSET, LIABILITY, classify_account_code
    code = _counter(db, row)
    if classify_account_code(code) in (ASSET, LIABILITY):
        return code
    return _code(db, "ar" if row.direction == RECEIVE else "ap")


# --- the steps -----------------------------------------------------------------------------------------------

def validate_sayad(value: str | None) -> str | None:
    v = (value or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    v = re.sub(r"[\s-]", "", v)
    if not v:
        return None
    if not SAYAD_RE.match(v):
        _refuse("A Sayad id is the 16 digits printed on the cheque.", 422)
    return v


def _unique_sayad(db: Session, sayad: str | None, exclude=None) -> None:
    if not sayad:
        return
    q = select(Commitment.id).where(Commitment.sayad_id == sayad)
    if exclude is not None:
        q = q.where(Commitment.id != exclude)
    if db.execute(q).first() is not None:
        _refuse(f"A cheque with Sayad id {sayad} is already recorded.")


def record(db: Session, *, title: str, amount: int, due_date: date, direction: str = PAY,
           reference: str | None = None, bank_name: str | None = None, counterparty: str | None = None,
           counter_account_code: str | None = None, note: str | None = None, sayad_id: str | None = None,
           sayad_registered_on: date | None = None, invoice_id=None, entity_id=None,
           on: date | None = None) -> Commitment:
    """A cheque received or issued. In notes books with an account or invoice
    to post against, the receipt/issue is booked now."""
    if amount <= 0:
        _refuse("Cheque amount must be positive", 400)
    if direction not in (PAY, RECEIVE):
        _refuse("direction must be pay or receive", 400)
    when = on or date.today()
    sayad = validate_sayad(sayad_id)
    _unique_sayad(db, sayad)
    inv = _invoice(db, invoice_id, direction) if invoice_id else None
    row = Commitment(
        kind=CHEQUE, direction=direction, title=title, amount=int(amount), due_date=due_date, status=PENDING,
        reference=reference or None, bank_name=bank_name or None, counterparty=counterparty or None,
        counter_account_code=counter_account_code or None, note=note, sayad_id=sayad,
        sayad_registered_on=sayad_registered_on, invoice_id=getattr(inv, "id", None),
        entity_id=entity_id or getattr(inv, "entity_id", None), ledger_mode=DIRECT,
    )
    if inv is not None:
        row.counter_account_code = _code(db, "ar" if direction == RECEIVE else "ap")
    if not row.counterparty and row.entity_id:
        # the party's name: shown in the list and written on a printed cheque
        from app.models.entity import Entity
        party = db.get(Entity, row.entity_id)
        row.counterparty = getattr(party, "name", None)
    db.add(row)
    db.flush()
    action = RECEIVED if direction == RECEIVE else ISSUED
    txn = None
    if notes_books(db) and (row.counter_account_code or inv is not None):
        row.ledger_mode = NOTES
        holding = _code(db, "notes_receivable" if direction == RECEIVE else "notes_payable")
        if inv is not None:
            payment = _pay_invoice(db, row, inv, when, holding, action)
            row.payment_id = payment.id
            from app.models.transaction import Transaction
            txn = db.get(Transaction, payment.transaction_id)
        elif direction == RECEIVE:
            txn = _post(db, row, action, when, holding, row.counter_account_code)
        else:
            txn = _post(db, row, action, when, row.counter_account_code, holding)
        row.holding_account_code = holding
    _event(db, row, action, when, txn)
    if sayad_registered_on:
        _event(db, row, SAYAD, sayad_registered_on)
    return row


def _cheque(row: Commitment) -> None:
    if row.kind != CHEQUE:
        _refuse("Only a cheque has this step.", 400)


def deposit(db: Session, row: Commitment, *, on: date | None = None, bank_code: str | None = None) -> Commitment:
    """A received cheque handed to the bank for collection — again, after a bounce."""
    _cheque(row)
    if row.direction != RECEIVE:
        _refuse("Only a received cheque is deposited; an issued one clears or bounces.", 400)
    if row.status not in (PENDING, BOUNCED):
        _refuse(f"A {row.status} cheque cannot be deposited.")
    when = on or date.today()
    again = row.status == BOUNCED
    action = REDEPOSIT if again else DEPOSIT
    row.deposit_account_code = _bank(db, bank_code)
    txn = None
    if row.ledger_mode == NOTES:
        collection = _code(db, "cheques_in_collection")
        if again and row.invoice_id:
            from app.models.transaction import Transaction
            payment = _pay_invoice(db, row, _invoice(db, row.invoice_id, RECEIVE), when, collection, action)
            row.payment_id = payment.id
            txn = db.get(Transaction, payment.transaction_id)
        else:
            txn = _post(db, row, action, when, collection, _party(db, row) if again else row.holding_account_code)
        row.holding_account_code = collection
    row.status = DEPOSITED
    row.deposited_on = when
    _event(db, row, action, when, txn)
    return row


def clear(db: Session, row: Commitment, *, on: date | None = None, post: bool = True,
          bank_code: str | None = None) -> Commitment:
    """The cheque cleared: the money is in (or out of) the bank."""
    _cheque(row)
    if row.status == SETTLED:
        _refuse("Already settled", 400)
    # a bounced cheque presented again and paid clears too
    if row.status not in (PENDING, DEPOSITED, BOUNCED):
        _refuse(f"A {row.status} cheque cannot clear.")
    when = on or date.today()
    bank = _bank(db, bank_code or row.deposit_account_code)
    txn = None
    if row.ledger_mode == NOTES and row.holding_account_code:
        if row.direction == RECEIVE:
            txn = _post(db, row, CLEARED, when, bank, row.holding_account_code)
        else:
            txn = _post(db, row, CLEARED, when, row.holding_account_code, bank)
        row.holding_account_code = None
    elif row.ledger_mode == NOTES and not row.invoice_id:
        # bounced, so the claim sits with the party: the bank pays it now
        party = _party(db, row)
        txn = (_post(db, row, CLEARED, when, bank, party) if row.direction == RECEIVE
               else _post(db, row, CLEARED, when, party, bank))
    elif post and row.invoice_id and not row.payment_id:
        from app.models.transaction import Transaction
        payment = _pay_invoice(db, row, _invoice(db, row.invoice_id, row.direction), when, bank, CLEARED)
        row.payment_id = payment.id
        txn = db.get(Transaction, payment.transaction_id)
    elif post and row.counter_account_code:
        if row.direction == RECEIVE:
            txn = _post(db, row, CLEARED, when, bank, row.counter_account_code)
        else:
            txn = _post(db, row, CLEARED, when, row.counter_account_code, bank)
    row.status = SETTLED
    row.settled_on = when
    row.settled_transaction_id = getattr(txn, "id", None)
    _event(db, row, CLEARED, when, txn)
    return row


def _payment_extra(db: Session, row: Commitment) -> tuple[int, list]:
    """What the cheque paid beyond its invoice: the credit its payment left (the
    customer's credit, or what the supplier owes us), with what is left of it.
    Refused when some of it was refunded or used on another invoice — that money
    moved, and it is reversed first."""
    if not row.payment_id:
        return 0, []
    from app.models.credit_note import CreditNote
    from app.models.payment import Payment
    from app.services import credits
    payment = db.get(Payment, row.payment_id)
    if payment is None or payment.transaction_id is None:
        return 0, []
    rows = list(db.execute(select(CreditNote).where(
        CreditNote.note_type == "credit", CreditNote.transaction_id == payment.transaction_id).with_for_update()).scalars())
    if not rows:
        return 0, []
    if sum(credits.used(db, [c.id for c in rows], kinds=credits.MOVED).values()) > 0:
        _refuse("The extra this cheque paid was refunded or used on another invoice. Reverse that first.")
    pairs = credits.remaining(db, rows)
    return credits.total(pairs), pairs


def _back_to_party(db: Session, row: Commitment, action: str, when: date):
    """The cheque no longer pays: the customer owes it (or we owe the
    supplier) again, and an invoice it paid reopens. A bounce moves the claim
    to the party; handing back an unused cheque undoes its receipt/issue.

    A cheque that paid more than its invoice left the extra as the party's
    credit. That extra goes back out of the credit, not onto the receivable: it
    stayed refundable after a bounce, the receivable came out too high, and
    depositing the cheque again made a second credit (security review,
    2026-10-06)."""
    if row.ledger_mode != NOTES or not row.holding_account_code:
        return None
    party = _party(db, row) if action == BOUNCE else _counter(db, row)
    extra, pairs = _payment_extra(db, row)
    if row.direction == RECEIVE:
        lines = [(party, row.amount - extra, 0), (_code(db, "customer_credit") if extra else None, extra, 0),
                 (row.holding_account_code, 0, row.amount)]
    else:
        lines = [(row.holding_account_code, row.amount, 0), (party, 0, row.amount - extra),
                 (_code(db, "supplier_advance") if extra else None, 0, extra)]
    txn = _post_lines(db, row, action, when, lines)
    if pairs:
        from app.models.credit_note import CreditNote
        for credit, left in pairs:
            db.add(CreditNote(invoice_id=credit.invoice_id, entity_id=credit.entity_id, kind=credit.kind, date=when,
                              amount=left, currency=credit.currency, note_type="withdrawn", credit_id=credit.id,
                              transaction_id=txn.id))
        db.flush()
    row.holding_account_code = None
    _unpay_invoice(db, row)
    return txn


def bounce(db: Session, row: Commitment, *, on: date | None = None, note: str | None = None) -> Commitment:
    """برگشت: the bank refused it. Still owed — never settled."""
    _cheque(row)
    allowed = (PENDING, DEPOSITED) if row.direction == RECEIVE else (PENDING,)
    if row.status not in allowed:
        _refuse(f"A {row.status} cheque cannot bounce.")
    when = on or date.today()
    txn = _back_to_party(db, row, BOUNCE, when)
    row.status = BOUNCED
    _event(db, row, BOUNCE, when, txn, note)
    return row


def return_cheque(db: Session, row: Commitment, *, on: date | None = None, note: str | None = None) -> Commitment:
    """عودت: the cheque is handed back — a bounced one to its drawer, or an
    unpresented one (the party paid another way, or gave ours back)."""
    _cheque(row)
    if row.status not in (PENDING, BOUNCED):
        _refuse(f"A {row.status} cheque cannot be returned." + (
            " It is at the bank: it clears or bounces." if row.status == DEPOSITED else ""))
    when = on or date.today()
    txn = _back_to_party(db, row, RETURN, when) if row.status == PENDING else None
    row.status = RETURNED
    _event(db, row, RETURN, when, txn, note)
    return row


def endorse(db: Session, row: Commitment, *, to: str, on: date | None = None, account_code: str | None = None,
            invoice_id=None) -> Commitment:
    """خرج چک: a received cheque passed on — to pay a supplier's bill, or to
    whoever ``account_code`` is."""
    _cheque(row)
    if row.direction != RECEIVE or row.status != PENDING:
        _refuse("Only a received cheque still in hand can be passed on.")
    to = (to or "").strip()
    if not to and not invoice_id:
        _refuse("Say who the cheque was passed to.", 422)
    when = on or date.today()
    bill = _invoice(db, invoice_id, PAY) if invoice_id else None
    txn = None
    if row.ledger_mode == NOTES:
        if bill is not None:
            from app.models.transaction import Transaction
            payment = _pay_invoice(db, row, bill, when, row.holding_account_code, ENDORSE)
            txn = db.get(Transaction, payment.transaction_id)
        else:
            if not account_code:
                _refuse("Choose the account of the one it was passed to (e.g. the supplier).", 422)
            txn = _post(db, row, ENDORSE, when, _bank(db, account_code), row.holding_account_code)
        row.holding_account_code = None
    else:
        if row.invoice_id:
            _refuse("This cheque is for an invoice and is not in the books yet; clear it instead.")
        if bill is not None:
            _refuse("Paying a bill with a received cheque needs the cheque in the books (Iranian books).", 400)
        if account_code and row.counter_account_code:
            txn = _post(db, row, ENDORSE, when, _bank(db, account_code), row.counter_account_code)
    if bill is not None and not to:
        from app.models.entity import Entity
        party = db.get(Entity, bill.entity_id) if bill.entity_id else None
        to = getattr(party, "name", None) or bill.number
    row.status = ENDORSED
    row.endorsed_to = to
    row.settled_on = when
    row.settled_transaction_id = getattr(txn, "id", None)
    _event(db, row, ENDORSE, when, txn, to)
    return row


def register_sayad(db: Session, row: Commitment, *, sayad_id: str | None = None,
                   on: date | None = None) -> Commitment:
    """Registered in Sayad (the issuer) or confirmed there (the receiver)."""
    _cheque(row)
    sayad = validate_sayad(sayad_id) if sayad_id else row.sayad_id
    if not sayad:
        _refuse("Enter the cheque's 16-digit Sayad id.", 422)
    _unique_sayad(db, sayad, exclude=row.id)
    row.sayad_id = sayad
    row.sayad_registered_on = on or date.today()
    _event(db, row, SAYAD, row.sayad_registered_on)
    return row


def record_print(db: Session, row: Commitment, *, payee: str | None = None) -> None:
    """The leaf was printed (cheque_print): who it was made out to, in the history."""
    _event(db, row, PRINTED, date.today(), note=payee)


def history(db: Session, row: Commitment) -> list[dict]:
    rows = db.execute(select(CommitmentEvent).where(CommitmentEvent.commitment_id == row.id)
                      .order_by(CommitmentEvent.happened_on, CommitmentEvent.created_at)).scalars().all()
    return [{"action": e.action, "on": e.happened_on.isoformat(), "note": e.note,
             "transaction_id": str(e.transaction_id) if e.transaction_id else None} for e in rows]


def needs_sayad(db: Session, row: Commitment, *, locale: str | None = None) -> bool:
    """An Iranian cheque still in play but not registered (issuer) or
    confirmed (receiver) in Sayad. ``locale``: the company's, when the caller
    checks many cheques and has read it once."""
    return (row.kind == CHEQUE and row.status in (PENDING, DEPOSITED) and row.sayad_registered_on is None
            and (locale or _locale(db)) in ("ir", "default"))

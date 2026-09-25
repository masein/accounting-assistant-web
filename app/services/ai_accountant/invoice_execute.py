"""Execute handlers for the confirm-gated invoice and commitment proposals.
Each goes through the same code the UI uses (app.api.invoices helpers,
commitment_service) and writes one audit row (actor_source='ai-assistant')."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.time_execute import _audit


def execute_invoice_proposal(
    db: Session, proposal: AIProposal, *, actor_user_id: str,
    actor_username: str | None, ip_address: str | None,
) -> tuple[str | None, str]:
    p = dict(proposal.tool_input or {})
    name = proposal.tool_name
    common = dict(actor_user_id=actor_user_id, actor_username=actor_username, ip_address=ip_address)

    if name == "propose_record_invoice_payment":
        from app.api.invoices import add_payment
        from app.schemas.invoice import PaymentCreate
        read = add_payment(UUID(p["invoice_id"]), PaymentCreate(
            amount=int(p["amount"]), date=date.fromisoformat(p["date"]), method=p.get("method") or "bank",
            bank_account_code=p.get("bank_account_code"), reference=p.get("reference"),
            description=f"Payment for invoice {p.get('invoice_number')}",
        ), db)
        txn_id = str(read.transaction_id) if getattr(read, "transaction_id", None) else None
        audit_id = _audit(db, proposal, entity_type="payment", entity_id=str(read.id),
                          detail={"invoice": p.get("invoice_number"), "amount": p["amount"], "transaction_id": txn_id}, **common)
        return txn_id, audit_id

    if name == "propose_create_invoice":
        from app.api.invoices import create_invoice
        from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate
        items = [InvoiceItemCreate(product_name=l["description"], quantity=l["quantity"], unit_price=int(l["unit_price"]),
                                   tax_rate=float(l.get("tax_rate") or 0), description=l.get("description")) for l in p["lines"]]
        read = create_invoice(InvoiceCreate(
            number=p["number"], kind=p["kind"], status="issued", issue_date=date.fromisoformat(p["issue_date"]),
            due_date=date.fromisoformat(p["due_date"]), amount=0, currency=p["currency"], description=p.get("description"),
            entity_id=UUID(p["entity_id"]), items=items,
        ), db)
        txn_id = str(read.transaction_id) if getattr(read, "transaction_id", None) else None
        audit_id = _audit(db, proposal, entity_type="invoice", entity_id=str(read.id),
                          detail={"number": read.number, "kind": read.kind, "amount": read.amount, "party": p.get("party")}, **common)
        return txn_id, audit_id

    if name in ("propose_settle_commitment", "propose_bounce_cheque", "propose_create_cheque"):
        from app.models.commitment import Commitment
        from app.services import commitment_service as cs
        if name == "propose_create_cheque":
            row = cs.create_cheque(db, title=p["title"], amount=int(p["amount"]), due_date=date.fromisoformat(p["due_date"]),
                                   direction=p["direction"], reference=p.get("reference"), bank_name=p.get("bank_name"),
                                   counterparty=p.get("counterparty"), counter_account_code=p.get("counter_account_code"))
            db.flush()
            audit_id = _audit(db, proposal, entity_type="commitment", entity_id=str(row.id),
                              detail={"kind": "cheque", "title": row.title, "amount": row.amount, "due": row.due_date.isoformat()}, **common)
            return None, audit_id
        row = db.get(Commitment, UUID(p["commitment_id"]))
        if row is None:
            raise ValueError("commitment no longer exists")
        if name == "propose_settle_commitment":
            cs.settle(db, row, on=date.fromisoformat(p["date"]), post=True)
            txn_id = str(row.settled_transaction_id) if row.settled_transaction_id else None
            audit_id = _audit(db, proposal, entity_type="commitment", entity_id=str(row.id),
                              detail={"action": "settle", "title": row.title, "amount": row.amount, "on": p["date"], "transaction_id": txn_id}, **common)
            return txn_id, audit_id
        cs.mark_bounced(db, row)
        audit_id = _audit(db, proposal, entity_type="commitment", entity_id=str(row.id),
                          detail={"action": "bounce", "title": row.title, "amount": row.amount}, **common)
        return None, audit_id

    raise ValueError(f"no invoice/commitment executor for {name}")

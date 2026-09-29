"""Bank and identity numbers are for roles with bank:read (owner, CFO,
accountant, a personal user's own books). A viewer reads the reports — and the
documents behind them — without them: account numbers, IBANs, sort codes,
account holders and national ids are blanked in JSON reads, in the PDFs a
viewer can print (statement, invoice, quote), in the TTMS figures, and the
close pack leaves out the bank statement lines.
"""
from __future__ import annotations

IDENTITY_FIELDS = ("account_number", "iban", "sort_code", "account_holder", "national_id")


def may_see_identity() -> bool:
    """The signed-in caller may see bank and identity numbers (no caller: a job or a test — yes)."""
    from app.core.permissions import Perm, role_can
    from app.core.request_context import get_current_actor
    actor = get_current_actor()
    if actor is None or getattr(actor, "is_superadmin", False):
        return True
    return role_can(getattr(actor, "role", None), Perm.BANK_READ)


def redacted_entity(entity):
    """The entity as this caller may see it on a document: itself when allowed,
    otherwise a detached copy without the identity fields (the saved row is
    never touched)."""
    if entity is None or may_see_identity():
        return entity
    from types import SimpleNamespace
    copy = SimpleNamespace(**{c.key: getattr(entity, c.key) for c in type(entity).__table__.columns})
    for f in IDENTITY_FIELDS:
        if hasattr(copy, f):
            setattr(copy, f, None)
    return copy

"""What an account is, by the app's own code for it (docs/PLAN_OWN_CODES.md, scenarios AN1–AN2).

The app reads what an account is from its code's digits — 1 assets, 11 cash, 2 liabilities, 4 revenue,
6 expenses — in about 95 places. A company that keeps its own codes (a school's 2000 «درآمد شهریه»,
1600 «بستانکاران») stores the app's code for what each account is in ``accounts.class_code``, and every
one of those rules reads ``class_of(code)`` instead of the code: the account's class when it has one,
else the code itself, so nothing changes for a chart made here.

The map is the company's own (``class_code`` set), read once from the database session this context last
queried with — each request and each job has its own — and dropped when an account is written or the
session rolls back. Outside a company's context there is none."""
from __future__ import annotations

import contextvars
import weakref

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

_KEY = "chart_class_codes"
_session: contextvars.ContextVar = contextvars.ContextVar("chart_codes_session", default=None)


@event.listens_for(Session, "do_orm_execute")
def _remember(execute_state) -> None:
    _session.set(weakref.ref(execute_state.session))


@event.listens_for(Session, "after_flush")
def _forget_written(session: Session, _flush_context) -> None:
    from app.models.account import Account
    if any(isinstance(o, Account) for o in (*session.new, *session.dirty, *session.deleted)):
        session.info.pop(_KEY, None)


@event.listens_for(Session, "after_rollback")
def _forget_rolled_back(session: Session) -> None:
    session.info.pop(_KEY, None)


def _classes() -> dict[str, str]:
    from app.db.tenant import get_current_company
    cid = get_current_company()
    if cid is None:
        return {}
    ref = _session.get()
    session = ref() if ref is not None else None
    if session is None:
        return {}
    cache = session.info.setdefault(_KEY, {})
    found = cache.get(cid)
    if found is None:
        from app.models.account import Account
        with session.no_autoflush:
            rows = session.execute(select(Account.code, Account.class_code)
                                   .where(Account.class_code.isnot(None))).all()
        found = cache[cid] = {code: cls for code, cls in rows if cls}
    return found


def class_of(code: str | None) -> str | None:
    """The app's code for what the account ``code`` is: its class, or the code itself."""
    if not code:
        return code
    classes = _classes()
    return classes.get(str(code).strip(), code) if classes else code


def own_code(cls: str) -> str:
    """The company's own code for the account of class ``cls`` (the app's 6210 is a school's 3007), or ``cls``
    itself — for the places that name an account by the app's code with no session at hand."""
    classes = _classes()
    if not classes or not cls:
        return cls
    return next((code for code, c in classes.items() if c == cls), cls)


def own_codes_in_use() -> bool:
    """This company keeps codes of its own (some account has a class)."""
    return bool(_classes())


def class_expr():
    """``class_of`` in SQL: ``coalesce(accounts.class_code, accounts.code)``, for a ``LIKE '4%'`` filter."""
    from app.models.account import Account
    return func.coalesce(Account.class_code, Account.code)


def code_for_class(db: Session, cls: str) -> str | None:
    """The company's own code for the account whose class is ``cls`` (the app's 1110), or ``cls`` itself
    when no account has that class and an account has that code with no class of its own."""
    from app.models.account import Account
    own = db.execute(select(Account.code).where(Account.class_code == cls)).scalars().first()
    if own:
        return own
    plain = db.execute(select(Account.code).where(Account.code == cls, Account.class_code.is_(None))).scalars().first()
    return plain

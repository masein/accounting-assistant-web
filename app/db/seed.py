"""
Seed a minimal chart of accounts if the database has no accounts.
Based on common Persian/iranian chart structure (groups + general accounts).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.models.account import Account, AccountLevel
from app.models.transaction_fee import PaymentMethod
from app.models.user import User
from app.core.auth import hash_password

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (code, name, level) — order matters: parents before children
SEED_ACCOUNTS = [
    # Groups (2-digit)
    ("11", "دارایی‌های جاری", AccountLevel.GROUP),
    ("12", "دارایی‌های غیرجاری", AccountLevel.GROUP),
    ("21", "بدهی‌های جاری", AccountLevel.GROUP),
    ("31", "حقوق مالکانه", AccountLevel.GROUP),
    ("41", "فروش و درآمدها", AccountLevel.GROUP),
    ("61", "هزینه‌های عملیاتی", AccountLevel.GROUP),
    ("62", "سایر هزینه‌ها و درآمدهای غیرعملیاتی", AccountLevel.GROUP),
    ("91", "حساب‌های انتظامی", AccountLevel.GROUP),
    # General (4-digit) — will link to parent by code prefix
    ("1110", "موجودی نقد و بانک", AccountLevel.GENERAL),
    ("1112", "حساب‌ها و اسناد دریافتنی تجاری", AccountLevel.GENERAL),
    ("1210", "دارایی‌های ثابت مشهود", AccountLevel.GENERAL),
    ("2110", "حساب‌ها و اسناد پرداختنی تجاری", AccountLevel.GENERAL),
    ("3110", "سرمایه", AccountLevel.GENERAL),
    ("4110", "فروش", AccountLevel.GENERAL),
    ("6110", "هزینه‌های حقوق و دستمزد", AccountLevel.GENERAL),
    ("6112", "سایر هزینه‌های عملیاتی", AccountLevel.GENERAL),
    ("6210", "هزینه‌های مالی", AccountLevel.GENERAL),
]


def _parent_code(code: str) -> str | None:
    """Return parent code for hierarchy: 1110 -> 11, 6112 -> 61."""
    if len(code) <= 2:
        return None
    return code[:2]


def seed_chart_if_empty(session: "Session") -> int:
    """
    Insert seed accounts if the chart is empty. Returns number of accounts created.
    """
    from sqlalchemy import func, select

    count = session.execute(select(func.count(Account.id))).scalar()
    if count > 0:
        return 0
    code_to_id: dict[str, uuid.UUID] = {}
    for code, name, level in SEED_ACCOUNTS:
        parent_id = None
        parent_code = _parent_code(code)
        if parent_code and parent_code in code_to_id:
            parent_id = code_to_id[parent_code]
        acc = Account(code=code, name=name, level=level, parent_id=parent_id)
        session.add(acc)
        session.flush()
        code_to_id[code] = acc.id
    session.commit()
    return len(SEED_ACCOUNTS)


def seed_payment_methods_if_empty(session: "Session") -> int:
    """
    Insert default payment methods if none exist.
    """
    from sqlalchemy import func, select

    count = session.execute(select(func.count(PaymentMethod.id))).scalar()
    if count > 0:
        return 0
    defaults = [
        ("paya", "Paya"),
        ("card_to_card", "Card-to-Card"),
        ("zaba", "Zaba"),
        ("satna", "Satna"),
        ("internal_transfer", "Internal Transfer"),
    ]
    for key, name in defaults:
        session.add(PaymentMethod(key=key, name=name, is_active=True))
    session.commit()
    return len(defaults)


def seed_admin_user_if_missing(session: "Session") -> int:
    """
    Ensure the default admin user exists for first login.
    """
    from sqlalchemy import func, select

    existing = (
        session.execute(select(User).where(func.lower(User.username) == "admin"))
        .scalars()
        .first()
    )
    if existing:
        return 0
    password_hash, password_salt = hash_password("admin")
    session.add(
        User(
            username="admin",
            password_hash=password_hash,
            password_salt=password_salt,
            is_admin=True,
            is_active=True,
        )
    )
    session.commit()
    return 1

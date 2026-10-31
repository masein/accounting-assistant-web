"""Net worth is «دارایی خالص» in a person's chart too: the seed names the group
31 and the opening account 3110 that way, and migration 086 renames them in
books kept from before — only where the name is still the seed's own
(scenario BD12)."""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db.tenant import use_company

ROOT = Path(__file__).resolve().parents[1]


def _names(db, cid) -> dict[str, str]:
    from app.models.account import Account
    with use_company(cid):
        return dict(db.execute(select(Account.code, Account.name).where(Account.code.in_(("31", "3110")))).all())


def _set(db, cid, code, name):
    from app.models.account import Account
    with use_company(cid):
        db.execute(select(Account).where(Account.code == code)).scalar_one().name = name
        db.commit()


def _migrate(db, direction="upgrade", times=1):
    spec = importlib.util.spec_from_file_location("m086", ROOT / "alembic/versions/086_net_worth_names.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    for _ in range(times):
        with db.get_bind().begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(mod, direction)()


def test_bd12_new_books_and_books_from_before_say_it_the_same_way(db):
    from app.services.company_service import provision_company
    from tests.test_admin_audit import _purge_company
    made = {}
    for key, locale, kind in (("new", "default", "personal"), ("before", "default", "personal"),
                              ("renamed", "default", "personal"), ("english", "uk", "personal")):
        company, _user = provision_company(db, name=f"net worth {key} {uuid.uuid4().hex[:4]}", locale=locale,
                                           base_currency="IRR", username=f"nw-{uuid.uuid4().hex[:8]}",
                                           password="Str0ng-personal-pass!", kind=kind)
        made[key] = str(company.id)
    db.commit()
    try:
        assert _names(db, made["new"]) == {"31": "دارایی خالص", "3110": "دارایی خالص اولیه"}
        assert _names(db, made["english"]) == {"31": "Net worth", "3110": "Opening net worth"}
        # books kept from before carry the seed's old names; one person renamed their opening account
        for key in ("before", "renamed"):
            _set(db, made[key], "31", "خالص دارایی")
            _set(db, made[key], "3110", "خالص دارایی اولیه")
        _set(db, made["renamed"], "3110", "پول اول کار")
        _migrate(db, times=2)                                       # safe to run twice
        db.expire_all()
        assert _names(db, made["before"]) == {"31": "دارایی خالص", "3110": "دارایی خالص اولیه"}
        assert _names(db, made["renamed"]) == {"31": "دارایی خالص", "3110": "پول اول کار"}   # theirs stays
        assert _names(db, made["english"]) == {"31": "Net worth", "3110": "Opening net worth"}
        _migrate(db, "downgrade")
        db.expire_all()
        assert _names(db, made["before"]) == {"31": "خالص دارایی", "3110": "خالص دارایی اولیه"}
        assert _names(db, made["renamed"])["3110"] == "پول اول کار"
    finally:
        for cid in made.values():
            _purge_company(db, cid)

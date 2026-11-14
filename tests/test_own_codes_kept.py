"""The own-codes choice kept across a reset and made again on the next import (chat review 2026-11-10,
scenario BZ1; the owner chose this, 2026-11-14).

A school's books were reset six times and imported again; each time the accounts came back on the app's codes and
the school switched to its own by hand — once the reset came between the import and the switch, and its
instruction («کل و معین») in the chat went unanswered.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from tests.test_own_codes_engine import _MADE, _company, _in, _purge_made  # noqa: F401 — fixture
from tests.test_own_codes_switch import _import, _switched, _xlsx


def _codes(db) -> set[str]:
    from app.models.account import Account
    return set(db.execute(select(Account.code).where(Account.class_code.isnot(None))).scalars())


def test_bz1_the_choice_survives_a_reset_and_is_made_again(db):
    from app.services import books_reset, own_codes
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
        assert own_codes.chosen(db) is False and own_codes.reapply(db) is None     # never chosen: nothing
    _switched(db, cid)
    with _in(cid):
        assert own_codes.chosen(db) is True
        first = _codes(db)
        assert {"3001", "1602", "2000"} <= first
    books_reset.reset(db, uuid.UUID(cid), actor_user_id=None, actor_username="test")
    with _in(cid):
        assert own_codes.chosen(db) is True and not _codes(db)                     # kept; the books are the app's
        _import(db, _xlsx())
        again = own_codes.reapply(db)
        db.commit()
        assert again == {"applied": True, "recoded": again["recoded"], "merged": 3} and again["recoded"] > 0
        assert _codes(db) == first                                                 # the same codes as before
        assert own_codes.reapply(db) is None                                       # on its own codes: nothing


def test_bz1_codes_that_need_a_choice_are_asked_for(db, monkeypatch):
    from app.api.migration import _journal_said
    from app.services import books_reset, own_codes
    cid = _company(db)
    with _in(cid):
        _import(db, _xlsx())
    _switched(db, cid)
    books_reset.reset(db, uuid.UUID(cid), actor_user_id=None, actor_username="test")
    with _in(cid):
        _import(db, _xlsx())
        real = own_codes.preview
        monkeypatch.setattr(own_codes, "preview", lambda d: {**real(d), "ready": False,
                                                            "counts": {**real(d)["counts"], "clashes": 2}})
        asked = own_codes.reapply(db)
        assert asked == {"applied": False, "clashes": 2} and not _codes(db)
        parsed = {"vouchers": []}
        said = _journal_said(db, "fa", {"posted": 0, "own_codes": asked}, parsed, "school.xlsx")
        assert "۲ کد باید انتخاب شود" in said and "«کدینگ حساب‌ها» ← «کدهای حساب خودتان»" in said
        said = _journal_said(db, "en", {"posted": 0, "own_codes": {"applied": True, "recoded": 9, "merged": 3}},
                             parsed, "school.xlsx")
        assert "on your own codes again (9 accounts)" in said

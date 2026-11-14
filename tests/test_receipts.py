"""Receipts, one entry each (chat review 2026-11-10, scenario CB1).

From the owner's phone: two shops' receipts sent together became one entry of their sum, with no reference, both
under one guessed category; another receipt from a hypermarket was guessed as groceries when it was a vacuum
cleaner. Figures made up.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
from tests.test_statement_export import _company


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    _api, cid = _company(client, db, "ir", "IRR")
    yield cid
    client.cookies.clear()
    _purge_company(db, cid)


def _files(db, cid, n):
    from app.models.transaction import TransactionAttachment
    with use_company(cid):
        rows = [TransactionAttachment(id=uuid.uuid4(), file_name=f"r{i}.jpg", file_path=f"/nowhere/{uuid.uuid4().hex}.jpg",
                                      content_type="image/jpeg") for i in range(n)]
        db.add_all(rows)
        db.commit()
        return [str(r.id) for r in rows]


def _card(db, cid, amount, files, *, attach=None, documents=(350_530, 529_000)):
    args = ProposeCreateTransactionInput(date="2026-11-09", description="receipt", currency="IRR",
                                         attachment_ids=attach or [], lines=[
        {"account_code": "6112", "debit": amount, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": amount}])
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u-receipts", user_message="", attachment_ids=list(files),
                          source_amounts=list(documents), document_amounts=list(documents))
        return asyncio.run(ProposeCreateTransaction().run(ctx, args))


def test_cb1_two_receipts_are_two_cards(db, co):
    files = _files(db, co, 2)
    with pytest.raises(ToolError) as e:
        _card(db, co, 879_530, files)                                            # the two receipts' sum
    assert e.value.code == "one_card_per_receipt" and "one card per receipt" in e.value.message
    first = _card(db, co, 350_530, files, attach=[files[0]])
    second = _card(db, co, 529_000, files, attach=[files[1]])
    assert first["preview"]["attachment_ids"] == [files[0]]                       # each card its own receipt
    assert second["preview"]["attachment_ids"] == [files[1]]
    assert sorted(_card(db, co, 529_000, files)["preview"]["attachment_ids"]) == sorted(files)   # none named: all
    one = _files(db, co, 1)
    assert _card(db, co, 529_000, one, attach=[one[0]], documents=(529_000,))["preview"]["attachment_ids"] == one


def test_cb1_the_assistant_is_told():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT, _CORRECTABLE_REFUSALS
    assert "one card per receipt or invoice" in SYSTEM_PROMPT
    assert "never guess from the shop's name" in SYSTEM_PROMPT
    assert "one_card_per_receipt" in _CORRECTABLE_REFUSALS

"""The opening entry kept, and a move between categories made once (chat review 2026-11-10, scenarios BU1–BU4).

From production: a company's whole opening voucher went with the plain "delete this transaction?", one of six
entries removed within a minute; and three purchases moved from one category to another one day were moved a
second time the next, so the books counted the move twice. The figures here are made up.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from tests.test_statement_export import _company

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, dr, cr, amount, desc, ref=None, day="2026-10-16"):
    r = api.post("/transactions", json={"date": day, "description": desc, "currency": "IRR", "reference": ref,
                                        "lines": [{"account_code": dr, "debit": amount, "credit": 0},
                                                  {"account_code": cr, "debit": 0, "credit": amount}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_bu1_the_opening_entry_needs_its_word(co):
    api, _cid = co
    opening = _post(api, "1110", "3110", 9_000_000, "opening", ref="MIGRATION-OPENING", day="2026-03-21")
    other = _post(api, "6112", "1110", 100_000, "stationery")
    r = api.delete(f"/transactions/{opening}")
    assert r.status_code == 409 and "opening balances entry" in r.json()["detail"]
    assert api.get(f"/transactions/{opening}").status_code == 200                # still there
    r = api.delete(f"/transactions/{opening}", headers={"Accept-Language": "fa"})
    assert r.status_code == 409 and "سند افتتاحیه" in r.json()["detail"]
    assert api.delete(f"/transactions/{other}").status_code == 204             # any other entry as before
    assert api.delete(f"/transactions/{opening}?opening=confirm").status_code == 204
    assert api.get(f"/transactions/{opening}").status_code == 404


def test_bu2_the_chat_reverses_the_opening_only_when_named(db, co):
    from app.services.ai_accountant.proposal_tools import ProposeReverseTransaction, ProposeReverseTransactionInput
    api, cid = co
    _post(api, "1110", "3110", 9_000_000, "opening", ref="OPENING-BALANCES", day="2026-03-21")

    def reverse(message):
        with use_company(cid):
            return asyncio.run(ProposeReverseTransaction().run(
                ToolContext(db=db, user_id="u-open", user_message=message),
                ProposeReverseTransactionInput(reference="OPENING-BALANCES")))
    with pytest.raises(ToolError) as e:
        reverse("همهٔ سندهای امروز رو برگردون")
    assert e.value.code == "opening_journal"
    assert reverse("سند افتتاحیه رو برگردون، اشتباه بود")["confirmation_token"]


def test_bu3_a_move_between_categories_is_made_once(db, co):
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    api, cid = co

    def move(amount, **kw):
        args = ProposeCreateTransactionInput(date="2026-10-22", description="move to bank charges", currency="IRR",
                                             lines=[{"account_code": "6210", "debit": amount, "credit": 0},
                                                    {"account_code": "6112", "debit": 0, "credit": amount}], **kw)
        with use_company(cid):
            return asyncio.run(ProposeCreateTransaction().run(
                ToolContext(db=db, user_id="u-open", user_message=f"move {amount} from general expenses to bank charges"),
                args))
    for amount in (200_000, 300_000, 100_000):                  # three moves made the day before
        _post(api, "6210", "6112", amount, "correction")
    assert move(700_000)["confirmation_token"]                    # more than was moved: not the same move
    with pytest.raises(ToolError) as e:
        move(600_000)
    assert e.value.code == "move_already_made"
    assert "600,000" in e.value.message and "2026-10-16" in e.value.message
    assert move(600_000, again=True)["confirmation_token"]       # the user says to make it again
    _post(api, "6112", "6210", 600_000, "moved back")             # moved back: nothing left moved
    assert move(600_000)["confirmation_token"]


def test_bu4_the_page_asks_for_the_word():
    js = (ROOT / "app/static/js/08-entities-invoices.js").read_text(encoding="utf-8")
    assert "?opening=confirm" in js and "openingDeleteWord" in js
    for f in ("02-i18n.js", "i18n/fa.js", "i18n/es.js", "i18n/ar.js"):
        text = (ROOT / "app/static/js" / f).read_text(encoding="utf-8")
        for key in ("openingDeleteTitle", "openingDeletePrompt", "openingDeleteWord", "openingDeleteMismatch"):
            assert f"{key}:" in text, (f, key)

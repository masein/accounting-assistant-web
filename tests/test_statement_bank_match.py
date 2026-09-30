"""Which bank account a statement belongs to (its review, its postings).

It used to be the bank entity named EXACTLY like the statement, else the
chart's one bank account — so an SMS statement ("Saman (SMS 1405/07)"), an
e-mailed one ("سامان" from the sender rule) or an upload typed "Saman" in a
company with several banks was checked against, and posted to, the default
bank. Now: the exact name, else the account number, else the one bank of
that name (ملت = Mellat = بانک ملت), else the default."""
from __future__ import annotations

import pytest

from tests.test_bank_sms import SAMAN
from tests.test_statement_export import _company


@pytest.fixture()
def banks(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    made = {}
    for name, acct in (("بانک ملت", "0123456789"), ("بانک سامان", "84980012341"), ("Bank Tejarat", None)):
        r = api.post("/entities", json={"type": "bank", "name": name, **({"account_number": acct} if acct else {})})
        assert r.status_code == 201, r.text
        made[name] = r.json()["code"]
    yield api, cid, made
    client.cookies.clear()
    _purge_company(db, cid)


def _account_for(db, cid, **fields):
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatement
    from app.services.statement_import import bank_account_for_statement
    with use_company(cid):
        stmt = BankStatement(source_type="csv", source_filename="x.csv", currency="IRR", **fields)
        return bank_account_for_statement(db, stmt)


def test_an_sms_statement_belongs_to_its_bank(banks, db):
    api, cid, made = banks
    r = api.post("/bank-sms", json={"text": SAMAN})
    assert r.status_code == 200 and r.json()["added"] == 1, r.text
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatement
    from app.services.statement_import import bank_account_for_statement
    with use_company(cid):
        stmt = db.query(BankStatement).filter(BankStatement.source_type == "sms").one()
        assert stmt.bank_name.startswith("Saman (SMS")
        assert bank_account_for_statement(db, stmt) == made["بانک سامان"]


@pytest.mark.parametrize("name, bank", [
    ("بانک ملت", "بانک ملت"),                      # exact, as before
    ("ملت", "بانک ملت"), ("Mellat", "بانک ملت"), ("MELLAT (SMS 1405/07)", "بانک ملت"),
    ("بانك ملت", "بانک ملت"),                      # Arabic kaf
    ("سامان", "بانک سامان"), ("Saman", "بانک سامان"),
    ("tejarat", "Bank Tejarat"), ("تجارت", "Bank Tejarat"),
])
def test_the_bank_by_its_name(banks, db, name, bank):
    _api, cid, made = banks
    assert _account_for(db, cid, bank_name=name) == made[bank]


def test_the_account_number_decides(banks, db):
    _api, cid, made = banks
    assert _account_for(db, cid, bank_name="Unknown", account_number="84980012341") == made["بانک سامان"]
    assert _account_for(db, cid, bank_name="Unknown", account_number="849-800-1234-1") == made["بانک سامان"]
    assert _account_for(db, cid, bank_name="Unknown", account_number="01***789") == made["بانک ملت"]    # masked
    # the number beats a name that says otherwise
    assert _account_for(db, cid, bank_name="Mellat", account_number="84980012341") == made["بانک سامان"]


def test_the_default_when_nothing_decides(banks, db):
    from app.db.tenant import use_company
    from app.services.account_resolver import resolve_account_code
    _api, cid, made = banks
    with use_company(cid):
        default = resolve_account_code(db, "bank")
    assert _account_for(db, cid, bank_name="Unknown") == default
    assert _account_for(db, cid, bank_name="Pasargad") == default           # no such bank on file
    assert _account_for(db, cid, bank_name="Unknown", account_number="12") == default   # too few digits to trust


def test_two_accounts_at_one_bank_need_the_number(banks, db):
    api, cid, made = banks
    second = api.post("/entities", json={"type": "bank", "name": "ملت ارزی", "account_number": "5555666677"}).json()["code"]
    from app.db.tenant import use_company
    from app.services.account_resolver import resolve_account_code
    with use_company(cid):
        default = resolve_account_code(db, "bank")
    assert _account_for(db, cid, bank_name="Mellat") == default               # which one? not a guess
    assert _account_for(db, cid, bank_name="Mellat", account_number="5555666677") == second
    assert _account_for(db, cid, bank_name="بانک ملت") == made["بانک ملت"]   # an exact name still settles it


@pytest.mark.parametrize("name, canon", [
    ("بانک ملت", "Mellat"), ("MELLAT (SMS 1405/07)", "Mellat"), ("بانك ملي ايران", "Melli"),
    ("بانک شهر", "Shahr"), ("صورتحساب شهریور", None),                 # a month isn't a bank
    ("توسعه صادرات", "Tosee"), ("بانک صادرات", "Saderat"),              # the longer name wins
    ("پست‌بانک", "PostBank"), ("Unknown", None), ("", None), (None, None),
])
def test_which_bank_a_name_means(name, canon):
    from app.services.statement_import import canonical_bank
    assert canonical_bank(name) == canon


@pytest.mark.parametrize("seen, on_file, ok", [
    ("0123456789", "0123456789", True), ("0123-456-789", "0123456789", True),
    ("۰۱۲۳۴۵۶۷۸۹", "0123456789", True),                                  # Persian digits
    ("01***789", "0123456789", True), ("01***788", "0123456789", False), ("0***9", "0123456789", False),
    ("6037****1234", "6037991234561234", True),
    ("40102680020817909002", "IR820540102680020817909002", True),        # the tail of the IBAN
    ("12345", "0123412345", False),                                       # too short to trust
    (None, "0123456789", False), ("0123456789", None, False),
])
def test_account_numbers(seen, on_file, ok):
    from app.services.statement_import import account_number_matches
    assert account_number_matches(seen, on_file) is ok

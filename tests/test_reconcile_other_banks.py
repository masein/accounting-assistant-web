"""A statement is reconciled in its bank's currency, against its own bank —
not the company's other banks.

* Every uploaded statement was marked IRR (the parser's default): a UK
  company's statement matched none of its GBP entries, and its rows were
  posted as IRR journals. Now: the bank's currency, else the company's.
* On a UK chart every bank entity's account (1201, 1202 …) is also a "cash"
  account (120x), so a Barclays statement matched HSBC entries of the same
  amount and day (and never posted the Barclays row), and listed every HSBC
  and petty-cash entry as "missing in bank"."""
from __future__ import annotations

import pytest

from tests.test_statement_export import _company

DAY = "2026-08-12"


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "uk", "GBP")
    codes = {}
    for name in ("Barclays", "HSBC"):
        r = api.post("/entities", json={"type": "bank", "name": name})
        assert r.status_code == 201, r.text
        codes[name] = r.json()["code"]
    yield api, cid, codes
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, bank_code, amount, desc, day=DAY):
    r = api.post("/transactions", json={"date": day, "description": desc, "currency": "GBP", "lines": [
        {"account_code": "7100", "debit": amount, "credit": 0},
        {"account_code": bank_code, "debit": 0, "credit": amount}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _statement(api, bank, rows):
    body = "Date,Description,Amount\n" + "".join(f"{d},{t},{a}\n" for d, t, a in rows)
    r = api.post("/brain/bank-statements/upload", params={"bank_name": bank},
                 files={"file": ("s.csv", body.encode(), "text/csv")})
    assert r.status_code == 200 and r.json()["id"], r.text
    return r.json()["id"]


def test_the_codes_are_cash_codes_on_a_uk_chart(uk):
    _api, _cid, codes = uk
    assert all(c.startswith("120") for c in codes.values()), codes


def test_a_row_does_not_match_another_banks_entry(uk):
    api, _cid, codes = uk
    hsbc_entry = _post(api, codes["HSBC"], 500, "Office rent")
    sid = _statement(api, "Barclays", [(DAY, "Office rent", -500)])
    api.post(f"/brain/bank-statements/{sid}/reconcile")
    row = api.get(f"/brain/bank-statements/{sid}").json()["rows"][0]
    assert row["matched_transaction_id"] != hsbc_entry and row["recon_status"] in ("unmatched", "partial"), row
    # its own bank's entry does match
    own = _post(api, codes["Barclays"], 500, "Office rent")
    api.post(f"/brain/bank-statements/{sid}/reconcile")
    row = api.get(f"/brain/bank-statements/{sid}").json()["rows"][0]
    assert row["matched_transaction_id"] == own


def test_other_banks_and_petty_cash_are_not_missing_from_this_statement(uk):
    api, _cid, codes = uk
    hsbc_entry = _post(api, codes["HSBC"], 777, "HSBC card")
    petty = _post(api, "1220", 55, "Petty cash tea")
    barclays_entry = _post(api, codes["Barclays"], 999, "Barclays only in the books")
    sid = _statement(api, "Barclays", [(DAY, "Something else", -10)])
    review = api.post(f"/brain/bank-statements/{sid}/review").json()
    missing = {f.get("transaction_id") for f in review["findings"] if f["kind"] == "missing_in_bank"}
    assert barclays_entry in missing
    assert hsbc_entry not in missing and petty not in missing, review["findings"]


def test_a_uk_statement_is_in_pounds_and_posts_in_pounds(uk, db):
    from app.db.tenant import use_company
    from app.models.transaction import Transaction
    api, cid, codes = uk
    sid = _statement(api, "Barclays", [(DAY, "Stationery", -42)])
    got = api.get(f"/brain/bank-statements/{sid}").json()
    assert got["currency"] == "GBP"
    row = got["rows"][0]
    out = api.post(f"/brain/bank-statements/{sid}/approve", json={"approvals": [
        {"row_id": row["id"], "action": "create", "account_code": "7500"}]}).json()
    assert out["created"] == 1, out
    with use_company(cid):
        txn = db.query(Transaction).filter(Transaction.description == "Stationery").one()
        assert txn.currency == "GBP"


def test_a_bank_in_another_currency_gives_its_statements_that_currency(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        usd = api.post("/entities", json={"type": "bank", "name": "Mellat USD", "currency": "USD",
                                          "account_number": "7777888899"}).json()
        assert usd.get("currency") == "USD", usd
        assert api.get(f"/brain/bank-statements/{_statement(api, 'Mellat USD', [(DAY, 'x', -10)])}").json()["currency"] == "USD"
        assert api.get(f"/brain/bank-statements/{_statement(api, 'Unknown', [(DAY, 'y', -11)])}").json()["currency"] == "IRR"
        # choosing the bank on a statement takes its currency along
        sid = _statement(api, "Unknown", [(DAY, "z", -12)])
        moved = api.put(f"/brain/bank-statements/{sid}/bank-account", json={"code": usd["code"]}).json()
        assert moved["currency"] == "USD"
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


def test_the_migration_moves_open_statements_to_the_companys_currency(client, db):
    """067: statements stored as IRR in a GBP company become GBP — unless an
    SMS feed (rials) or already posted from (those journals are IRR)."""
    import importlib.util
    import pathlib
    import uuid

    from app.db.tenant import tenant_bypass
    from app.models.bank_statement import BankStatement, BankStatementRow
    from tests.test_admin_audit import _purge_company
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "067_statement_currency.py"
    spec = importlib.util.spec_from_file_location("m067", path)
    m067 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m067)
    _uk_api, uk_cid = _company(client, db, "uk", "GBP")
    _ir_api, ir_cid = _company(client, db, "ir", "IRR")
    try:
        with tenant_bypass():
            def stmt(cid, source="csv", posted=False):
                s = BankStatement(company_id=uuid.UUID(cid), bank_name="B", source_type=source,
                                  source_filename="f", currency="IRR", status="parsed", total_rows=1)
                db.add(s)
                db.flush()
                db.add(BankStatementRow(company_id=uuid.UUID(cid), statement_id=s.id, row_index=1,
                                        tx_date=__import__("datetime").date(2026, 8, 1), debit=1, credit=0,
                                        user_approved=posted, recon_status="matched" if posted else "unmatched"))
                return s
            open_uk, sms_uk, posted_uk, ir = stmt(uk_cid), stmt(uk_cid, "sms"), stmt(uk_cid, posted=True), stmt(ir_cid)
            db.commit()
            assert m067.fix(db.connection()) >= 1
            db.commit()
            db.expire_all()
            assert db.get(BankStatement, open_uk.id).currency == "GBP"
            assert db.get(BankStatement, sms_uk.id).currency == "IRR"
            assert db.get(BankStatement, posted_uk.id).currency == "IRR"
            assert db.get(BankStatement, ir.id).currency == "IRR"
            assert m067.fix(db.connection()) == 0                          # idempotent
    finally:
        client.cookies.clear()
        _purge_company(db, uk_cid)
        _purge_company(db, ir_cid)


def test_the_generic_account_is_another_banks_when_a_bank_uses_it(client, db):
    """A company's first bank is often linked to the chart's generic account
    (1110): its entries are that bank's, not a second bank's."""
    from app.db.tenant import use_company
    from app.models.bank_statement import BankStatement
    from app.services.statement_import import statement_predicates
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        mellat = api.post("/entities", json={"type": "bank", "name": "بانک ملت", "code": "1110"}).json()
        saman = api.post("/entities", json={"type": "bank", "name": "بانک سامان"}).json()
        assert mellat["code"] == "1110" and saman["code"] != "1110", (mellat, saman)
        with use_company(cid):
            s = BankStatement(bank_name="سامان", source_type="csv", source_filename="s.csv", currency="IRR")
            code, match, missing = statement_predicates(db, s)
            assert code == saman["code"] and match(saman["code"]) and not match("1110") and not missing("1110")
            m = BankStatement(bank_name="Mellat", source_type="csv", source_filename="m.csv", currency="IRR")
            code, match, missing = statement_predicates(db, m)
            assert code == "1110" and match("1110") and not match(saman["code"]) and missing("1110")
    finally:
        client.cookies.clear()
        _purge_company(db, cid)

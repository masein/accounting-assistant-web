"""Base-currency amounts (roadmap 2026-09 §4.6, option 2 — as Xero and
QuickBooks keep them): every journal line also carries its value in the
company's base currency at the rate its entry was posted at, so reports can
add currencies together, revaluation moves only base values, and a rate that
arrives later converts the entries that were waiting for it."""
from __future__ import annotations

import random
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.exchange_rate import ExchangeRate
from app.models.transaction import Transaction, TransactionLine
from app.services import fx_base


# ─── 1. The arithmetic ─────────────────────────────────────────────────

def test_each_line_is_rounded_half_up():
    assert fx_base.to_base([(5, 0), (0, 5)], 0.5) == [(3, 0), (0, 3)]          # 2.5 → 3, both sides
    assert fx_base.to_base([(100, 0), (0, 100)], 1_050_000) == [(105_000_000, 0), (0, 105_000_000)]
    assert fx_base.to_base([(7, 0), (0, 7)], 1) == [(7, 0), (0, 7)]


def test_rounding_never_unbalances_an_entry():
    # 3 × 0.5 → 1.5 → 2 on each small debit, but the credit 3 × 0.5 = 1.5 → 2:
    # 2 + 2 + 2 ≠ 2 unless a unit is taken back where rounding added most.
    out = fx_base.to_base([(1, 0), (1, 0), (1, 0), (0, 3)], 0.5)
    assert sum(d for d, _ in out) == sum(c for _, c in out) == 2
    assert all(d >= 0 and c >= 0 for d, c in out)


def test_an_unbalanced_input_is_converted_but_not_forced():
    assert fx_base.to_base([(5, 0), (0, 4)], 0.5) == [(3, 0), (0, 2)]


@pytest.mark.parametrize("seed", range(40))
def test_random_entries_stay_balanced_and_close_to_exact(seed):
    rnd = random.Random(seed)
    rate = rnd.choice([0.0001234, 0.37, 0.5, 1.2757, 3.6725, 85_432.1, 1_050_000.0])
    debits = [rnd.randint(1, 10 ** rnd.randint(1, 9)) for _ in range(rnd.randint(1, 6))]
    credits_total = sum(debits)
    cuts = sorted(rnd.sample(range(1, credits_total), min(rnd.randint(0, 4), credits_total - 1))) if credits_total > 1 else []
    parts = [b - a for a, b in zip([0] + cuts, cuts + [credits_total])]
    lines = [(d, 0) for d in debits] + [(0, c) for c in parts]
    out = fx_base.to_base(lines, rate)
    assert sum(d for d, _ in out) == sum(c for _, c in out)
    for (d, c), (bd, bc) in zip(lines, out):
        assert abs(bd - d * rate) <= 1.5 and abs(bc - c * rate) <= 1.5
        assert bd >= 0 and bc >= 0


# ─── fixtures ─────────────────────────────────────────────────────────────

def _login(client, cid, *, superadmin=False):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid, is_superadmin=superadmin)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def uk(client, db):
    """A GBP company on the UK chart, logged in as its owner."""
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Sterling Ltd", slug=f"gb-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        db.commit()
    db.expunge_all()
    yield _login(client, cid), cid
    client.cookies.clear()
    db.rollback()
    db.execute(ExchangeRate.__table__.delete().where(ExchangeRate.company_id.is_(None)))
    db.commit()
    _purge_company(db, cid)


def _rate(api, fc, tc, rate, when):
    r = api.post("/fx/rates", json={"from_currency": fc, "to_currency": tc, "rate": rate, "effective_date": when})
    assert r.status_code == 201, r.text
    return r.json()


def _post(api, when, lines, currency=None, **extra):
    body = {"date": when, "description": "t", "lines": [{"account_code": c, "debit": d, "credit": k} for c, d, k in lines],
            **extra}
    if currency:
        body["currency"] = currency
    r = api.post("/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _txn(db, cid, tid):
    from app.db.tenant import use_company
    db.expire_all()
    with use_company(cid):
        t = db.get(Transaction, uuid.UUID(tid))
        lines = sorted(((ln.account.code, ln.debit, ln.credit, ln.base_debit, ln.base_credit) for ln in t.lines))
        return t, lines


SALE = [("1200", 100, 0), ("4000", 0, 100)]


# ─── 2. Every entry gets base amounts ───────────────────────────────────────

def test_an_entry_in_the_base_currency_has_rate_one(uk, db):
    api, cid = uk
    out = _post(api, "2026-09-01", SALE)
    assert out["currency"] == "GBP" and out["fx_rate"] == 1           # not "IRR" (the old default)
    t, lines = _txn(db, cid, out["id"])
    assert lines == [("1200", 100, 0, 100, 0), ("4000", 0, 100, 0, 100)]
    assert {(ln["base_debit"], ln["base_credit"]) for ln in out["lines"]} == {(100, 0), (0, 100)}


def test_a_foreign_entry_is_converted_at_the_rate_of_its_date(uk, db):
    api, cid = uk
    _rate(api, "USD", "GBP", 0.80, "2026-08-01")
    _rate(api, "USD", "GBP", 0.75, "2026-09-15")                       # later: not this entry's
    out = _post(api, "2026-09-01", SALE, "USD")
    t, lines = _txn(db, cid, out["id"])
    assert t.fx_rate == 0.80
    assert lines == [("1200", 100, 0, 80, 0), ("4000", 0, 100, 0, 80)]


def test_the_rate_typed_on_the_voucher_wins(uk, db):
    api, cid = uk
    _rate(api, "USD", "GBP", 0.80, "2026-08-01")
    out = _post(api, "2026-09-01", SALE, "USD", fx_rate=0.7777)
    t, lines = _txn(db, cid, out["id"])
    assert t.fx_rate == pytest.approx(0.7777) and lines[0][3] == 78


def test_no_rate_yet_the_entry_waits_and_converts_when_one_is_added(uk, db):
    api, cid = uk
    out = _post(api, "2026-09-01", SALE, "EUR")
    t, lines = _txn(db, cid, out["id"])
    assert t.fx_rate is None and all(ln[3] is None and ln[4] is None for ln in lines)
    meta = api.get("/fx/metadata").json()
    assert meta["unconverted"] == {"count": 1, "currencies": ["EUR"]}
    # a rate dated after the entry never converts it (it would be fixed in wrongly)
    _rate(api, "EUR", "GBP", 0.86, "2026-09-20")
    assert _txn(db, cid, out["id"])[0].fx_rate is None
    _rate(api, "EUR", "GBP", 0.85, "2026-08-31")
    t, lines = _txn(db, cid, out["id"])
    assert t.fx_rate == 0.85 and lines == [("1200", 100, 0, 85, 0), ("4000", 0, 100, 0, 85)]
    assert api.get("/fx/metadata").json()["unconverted"]["count"] == 0


def test_a_shared_rate_converts_every_companys_waiting_entries(uk, client, db):
    api, cid = uk
    out = _post(api, "2026-09-01", SALE, "USD")
    admin = _login(client, cid, superadmin=True)
    r = admin.post("/fx/rates", json={"from_currency": "USD", "to_currency": "GBP", "rate": 0.8,
                                      "effective_date": "2026-09-01", "shared": True})
    assert r.status_code == 201
    assert _txn(db, cid, out["id"])[0].fx_rate == 0.8


def test_editing_currency_or_date_rereads_the_rate(uk, db):
    api, cid = uk
    _rate(api, "USD", "GBP", 0.80, "2026-08-01")
    _rate(api, "USD", "GBP", 0.70, "2026-09-10")
    _rate(api, "EUR", "GBP", 0.90, "2026-08-01")
    out = _post(api, "2026-09-01", SALE, "USD")
    assert api.patch(f"/transactions/{out['id']}", json={"date": "2026-09-12"}).status_code == 200
    assert _txn(db, cid, out["id"])[0].fx_rate == 0.70
    assert api.patch(f"/transactions/{out['id']}", json={"currency": "EUR"}).status_code == 200
    t, lines = _txn(db, cid, out["id"])
    assert t.fx_rate == 0.90 and lines[0][3] == 90
    # a new rate on the entry itself, and new lines, recompute
    assert api.patch(f"/transactions/{out['id']}", json={"fx_rate": 0.5}).status_code == 200
    assert _txn(db, cid, out["id"])[1][0][3] == 50
    r = api.patch(f"/transactions/{out['id']}", json={"lines": [
        {"account_code": "1200", "debit": 30, "credit": 0}, {"account_code": "4000", "debit": 0, "credit": 30}]})
    assert r.status_code == 200
    assert _txn(db, cid, out["id"])[1] == [("1200", 30, 0, 15, 0), ("4000", 0, 30, 0, 15)]
    # back to the base currency: rate 1
    assert api.patch(f"/transactions/{out['id']}", json={"currency": "GBP"}).status_code == 200
    assert _txn(db, cid, out["id"])[0].fx_rate == 1


def test_lines_written_directly_also_get_base_amounts(uk, db):
    """Invoices, payroll, fixed assets, imports and reversals build lines
    themselves — the flush hook covers them, one line per flush included."""
    from app.db.tenant import use_company
    from app.models.account import Account
    api, cid = uk
    _rate(api, "USD", "GBP", 0.5, "2026-01-01")
    with use_company(cid):
        acc = {a.code: a.id for a in db.execute(select(Account)).scalars()}
        t = Transaction(date=date(2026, 9, 1), currency="USD", description="direct")
        db.add(t)
        db.flush()
        for code, d, c in [("1200", 1, 0), ("1200", 1, 0), ("1200", 1, 0), ("4000", 0, 3)]:
            db.add(TransactionLine(transaction_id=t.id, account_id=acc[code], debit=d, credit=c))
            db.flush()                                                    # one at a time
        db.commit()
        lines = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == t.id)).scalars().all()
        assert sum(ln.base_debit for ln in lines) == sum(ln.base_credit for ln in lines) == 2   # 1.5 → 2, balanced
        assert t.fx_rate == 0.5


def test_a_reversal_is_converted_at_its_own_rate(uk, db):
    api, cid = uk
    _rate(api, "USD", "GBP", 0.8, "2026-08-01")
    out = _post(api, "2026-09-01", SALE, "USD")
    from app.db.tenant import use_company
    from app.services.reporting.ledger_service import LedgerService
    with use_company(cid):
        rev = LedgerService(db).reverse_journal_entry(uuid.UUID(out["id"]))
        db.commit()
    t, lines = _txn(db, cid, str(rev.transaction_id))
    assert t.fx_rate == 0.8 and lines == [("1200", 0, 100, 0, 80), ("4000", 100, 0, 80, 0)]


def test_revaluation_entries_are_never_recomputed(uk, db):
    from app.db.tenant import use_company
    from app.models.account import Account
    api, cid = uk
    with use_company(cid):
        acc = db.execute(select(Account).where(Account.code == "1200")).scalar_one()
        gain = db.execute(select(Account).where(Account.code == "4200")).scalar_one()
        t = Transaction(date=date(2026, 9, 30), currency="USD", fx_rate=0.8, fx_role="revaluation")
        db.add(t)
        db.flush()
        db.add_all([TransactionLine(transaction_id=t.id, account_id=acc.id, debit=0, credit=0, base_debit=7, base_credit=0),
                    TransactionLine(transaction_id=t.id, account_id=gain.id, debit=0, credit=0, base_debit=0, base_credit=7)])
        db.commit()
        t.fx_rate = 0.1
        db.commit()
        assert sorted((ln.base_debit, ln.base_credit) for ln in t.lines) == [(0, 7), (7, 0)]


# ─── 3. The company's own currency by default ────────────────────────────────

def test_entries_and_invoices_default_to_the_base_currency(uk, db):
    api, cid = uk
    from app.db.tenant import use_company
    from app.schemas.transaction import TransactionCreate
    from app.services.ledger_posting import create_transaction_from_payload
    with use_company(cid):
        t = create_transaction_from_payload(db, TransactionCreate(
            date=date(2026, 9, 1), description="recurring-style", lines=[
                {"account_code": "1200", "debit": 5, "credit": 0}, {"account_code": "4000", "debit": 0, "credit": 5}]))
        db.commit()
        assert t.currency == "GBP" and t.fx_rate == 1
    r = api.post("/invoices", json={"number": f"INV-{uuid.uuid4().hex[:5]}", "kind": "sales",
                                    "issue_date": "2026-09-01", "due_date": "2026-09-30", "amount": 1000})
    assert r.status_code in (200, 201), r.text
    assert r.json()["currency"] == "GBP"


# ─── 4. All currencies together, at base value ─────────────────────────────────

@pytest.fixture()
def mixed(uk):
    api, cid = uk
    _rate(api, "USD", "GBP", 0.8, "2026-01-01")
    _post(api, "2026-09-01", SALE)                                         # £100
    _post(api, "2026-09-02", SALE, "USD")                                  # $100 = £80
    _post(api, "2026-09-03", [("1200", 50, 0), ("4000", 0, 50)], "AED")    # no rate: left out
    return api, cid


def test_the_ledger_summary_adds_currencies_at_base_value(mixed):
    api, _ = mixed
    gbp = {r["account_code"]: r for r in api.get("/reports/ledger-summary").json()["rows"]}
    assert gbp["1200"]["debit_turnover"] == 100                              # one currency by default
    allv = api.get("/reports/ledger-summary", params={"currency": "ALL"}).json()
    rows = {r["account_code"]: r for r in allv["rows"]}
    assert allv["currency"] == "ALL" and set(allv["other_currencies"]) == {"AED", "GBP", "USD"}
    assert rows["1200"]["debit_turnover"] == 180 and rows["4000"]["credit_turnover"] == 180
    assert api.get("/fx/metadata").json()["unconverted"] == {"count": 1, "currencies": ["AED"]}


def test_statements_and_trial_balance_in_the_combined_view(mixed):
    api, _ = mixed
    tb = api.get("/manager-reports/books/trial-balance",
                 params={"from_date": "2026-01-01", "to_date": "2026-12-31", "currency": "ALL"}).json()
    by = {r["account_code"]: r for r in tb["rows"]}
    assert by["1200"]["debit_turnover"] == 180 and by["4000"]["credit_turnover"] == 180
    pl = api.get("/manager-reports/financial/income-statement",
                 params={"from_date": "2026-01-01", "to_date": "2026-12-31", "currency": "ALL"})
    assert pl.status_code == 200 and "180" in pl.text
    bs = api.get("/manager-reports/financial/balance-sheet", params={"to_date": "2026-12-31", "currency": "ALL"})
    assert bs.status_code == 200 and "180" in bs.text
    detail = api.get("/reports/accounts/1200/detail", params={"currency": "ALL"}).json()
    assert sorted(ln["debit"] for ln in detail["lines"]) == [0, 80, 100]    # the AED one has no base value
    found = api.get("/reports/transactions/search", params={"currency": "ALL", "account_code": "1200"}).json()
    assert found["total_debit"] == 180 and {r["currency"] for r in found["rows"]} == {"GBP"}


def test_one_currency_views_are_unchanged(mixed):
    api, _ = mixed
    usd = {r["account_code"]: r for r in api.get("/reports/ledger-summary", params={"currency": "USD"}).json()["rows"]}
    assert usd["1200"]["debit_turnover"] == 100
    aed = {r["account_code"]: r for r in api.get("/reports/ledger-summary", params={"currency": "AED"}).json()["rows"]}
    assert aed["1200"]["debit_turnover"] == 50


# ─── 5. A new base currency ───────────────────────────────────────────────────

def test_changing_the_base_currency_reconverts_every_entry(mixed, db):
    api, cid = mixed
    _rate(api, "GBP", "USD", 1.25, "2026-01-01")
    assert api.put("/fx/reporting-currency", json={"currency": "USD"}).status_code == 200
    rows = {r["account_code"]: r for r in api.get("/reports/ledger-summary", params={"currency": "ALL"}).json()["rows"]}
    assert rows["1200"]["debit_turnover"] == 225                             # $100 + £100 at 1.25
    from app.db.tenant import use_company
    with use_company(cid):
        usd = db.execute(select(Transaction).where(Transaction.currency == "USD")).scalars().one()
        assert usd.fx_rate == 1


def test_a_revaluation_in_the_old_base_is_cleared(uk, db):
    api, cid = uk
    _rate(api, "USD", "GBP", 0.8, "2026-01-01")
    _post(api, "2026-02-01", SALE, "USD")
    _rate(api, "USD", "GBP", 0.9, "2026-03-01")
    r = api.post("/fx/revalue", json={"as_of": "2026-03-31", "dry_run": False,
                                      "gain_account_code": "4200", "loss_account_code": "7850"}).json()
    assert r["posted_transaction_ids"] and r["total_adjustment"] == 10
    _rate(api, "GBP", "EUR", 1.2, "2026-01-01")
    _rate(api, "USD", "EUR", 1.0, "2026-01-01")
    assert api.put("/fx/reporting-currency", json={"currency": "EUR"}).status_code == 200
    from app.db.tenant import use_company
    db.expire_all()
    with use_company(cid):
        reval = db.get(Transaction, uuid.UUID(r["posted_transaction_id"]))
        assert reval.fx_role == "legacy_revaluation"
        assert all((ln.base_debit, ln.base_credit) == (0, 0) for ln in reval.lines)


# ─── 6. Revaluation defaults ────────────────────────────────────────────────

def test_fixed_assets_and_prepayments_stay_at_cost(uk):
    api, _ = uk
    _rate(api, "USD", "GBP", 0.8, "2026-01-01")
    _post(api, "2026-02-01", [("0010", 100, 0), ("1300", 50, 0), ("1200", 0, 150)], "USD")
    _rate(api, "USD", "GBP", 1.0, "2026-03-01")
    pv = api.post("/fx/revalue", json={"as_of": "2026-03-31"}).json()
    assert {x["account_code"] for x in pv["lines"]} == {"1200"}              # only the money
    assert pv["lines"][0]["adjustment"] == -30                               # −$150 now −£150, was −£120


def test_a_revaluation_does_not_touch_the_foreign_balances(uk):
    api, _ = uk
    _rate(api, "USD", "GBP", 0.8, "2026-01-01")
    _post(api, "2026-02-01", SALE, "USD")
    _rate(api, "USD", "GBP", 0.9, "2026-03-01")
    api.post("/fx/revalue", json={"as_of": "2026-03-31", "dry_run": False,
                                  "gain_account_code": "4200", "loss_account_code": "7850"})
    usd = {r["account_code"]: r for r in api.get("/reports/ledger-summary", params={"currency": "USD"}).json()["rows"]}
    assert usd["1200"]["debit_turnover"] == 100 and usd["1200"]["credit_turnover"] == 0
    allv = {r["account_code"]: r for r in api.get("/reports/ledger-summary", params={"currency": "ALL"}).json()["rows"]}
    assert allv["1200"]["debit_turnover"] == 90 and allv["4200"]["credit_turnover"] == 10   # 80 + 10


# ─── 7. Catching up ──────────────────────────────────────────────────────────

def test_catch_up_runs_for_every_company(uk, db):
    api, cid = uk
    out = _post(api, "2026-09-01", SALE, "USD")
    db.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=0.8, effective_date=date(2026, 9, 1)))
    db.commit()
    total = fx_base.fill_pending_all_companies(db)
    assert total["converted"] >= 1
    assert _txn(db, cid, out["id"])[0].fx_rate == 0.8


def test_the_old_placeholder_rate_is_no_longer_seeded():
    from app.services import fx_service
    assert not hasattr(fx_service, "seed_default_rates_if_empty")

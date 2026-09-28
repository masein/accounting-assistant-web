"""UK FRS 102 Section 1A statements from the ledger (roadmap 2026-09 §3.7).

One small company's two years, worked by hand, through all five statements:
every account on the balance sheet (it balances), sales returns reducing
turnover, FX gains in other operating income, depreciation and amortisation
stated, revaluation as OCI, the changes in equity tying to the balance sheet
in both years, dividends and directors' loans in the cash flow, and the
reconciliation of operating profit to cash generated from operations."""
from __future__ import annotations

import pytest

from app.db.seed import UK_SEED_ACCOUNTS
from app.services.reporting import uk_statement_service as uk
from tests.test_cheque_lifecycle import _company, _login

PERIOD = {"from_date": "2026-01-01", "to_date": "2026-09-27",
          "comparative_from_date": "2025-01-01", "comparative_to_date": "2025-09-27"}

# (date, [(code, debit, credit)], description)
PRIOR = [
    ("2025-03-01", [("1200", 5_000, 0), ("3000", 0, 5_000)], "Shares issued"),
    ("2025-05-01", [("1200", 4_000, 0), ("4000", 0, 4_000)], "Cash sale"),
    ("2025-06-01", [("3100", 500, 0), ("1200", 0, 500)], "Dividend paid"),
]
CURRENT = [
    ("2026-01-10", [("1200", 10_000, 0), ("3000", 0, 1_000), ("3010", 0, 9_000)], "Shares issued at a premium"),
    ("2026-02-01", [("1100", 12_000, 0), ("4000", 0, 12_000)], "Sales on credit"),
    ("2026-02-15", [("4100", 2_000, 0), ("1100", 0, 2_000)], "Sales returns"),
    ("2026-03-01", [("1200", 10_000, 0), ("1100", 0, 10_000)], "Customer paid"),
    ("2026-03-05", [("0020", 6_000, 0), ("1200", 0, 6_000)], "Equipment bought"),
    ("2026-03-06", [("0100", 2_000, 0), ("1200", 0, 2_000)], "Goodwill bought"),
    ("2026-04-01", [("1500", 1_200, 0), ("1200", 0, 1_200)], "Supplier paid in advance"),
    ("2026-05-01", [("1410", 300, 0), ("4200", 0, 300)], "Accrued income"),
    ("2026-05-10", [("1210", 150, 0), ("4210", 0, 150)], "FX gain"),
    ("2026-06-01", [("0040", 5_000, 0), ("3020", 0, 5_000)], "Land revalued"),
    ("2026-06-30", [("8500", 500, 0), ("0021", 0, 500)], "Depreciation"),
    ("2026-06-30", [("8600", 200, 0), ("0101", 0, 200)], "Amortisation"),
    ("2026-07-01", [("3100", 1_000, 0), ("2750", 0, 1_000)], "Dividend declared"),
    ("2026-07-15", [("2750", 1_000, 0), ("1200", 0, 1_000)], "Dividend paid"),
    ("2026-08-01", [("1200", 3_000, 0), ("2350", 0, 3_000)], "Director's advance"),
    ("2026-08-15", [("3020", 500, 0), ("3100", 0, 500)], "Excess depreciation transfer"),
    ("2026-08-20", [("1300", 100, 0), ("3999", 0, 100)], "Opening balance correction"),
    ("2026-09-01", [("1200", 4_000, 0), ("0021", 500, 0), ("7860", 1_500, 0), ("0020", 0, 6_000)], "Equipment sold"),
    ("2026-09-10", [("7600", 800, 0), ("2100", 0, 800)], "Supplies on credit"),
    ("2026-09-15", [("9000", 400, 0), ("2300", 0, 400)], "Corporation tax"),
    ("2026-09-20", [("2300", 400, 0), ("1200", 0, 400)], "Corporation tax paid"),
]


@pytest.fixture()
def books(client, db):
    """The UK company, both years posted."""
    from sqlalchemy import select

    from app.db.tenant import use_company
    from app.models.account import Account
    from app.services.account_resolver import _ensure_account, resolve_account_code
    from tests.test_admin_audit import _purge_company

    cid = _company(db, "uk", "GBP")
    api = _login(client, cid)
    with use_company(cid):
        for cat in ("fx_gain", "fx_loss"):
            resolve_account_code(db, cat, locale="uk")
        for code, name in (("7860", "Loss on disposal of fixed assets"), ("3999", "Opening balance adjustments")):
            if not db.execute(select(Account.id).where(Account.code == code)).first():
                _ensure_account(db, code, name, "uk")
        db.commit()
    for when, lines, desc in PRIOR + CURRENT:
        r = api.post("/transactions", json={"date": when, "description": desc, "currency": "GBP", "lines": [
            {"account_code": c, "debit": d, "credit": k} for c, d, k in lines]})
        assert r.status_code == 201, (desc, r.text)
    yield api
    client.cookies.clear()
    db.rollback()
    _purge_company(db, cid)


def _rows(body):
    return {r["key"]: r for r in body["rows"]}


def _get(api, path, **params):
    r = api.get(f"/manager-reports/financial/uk/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ─── Every account has its line ──────────────────────────────────────────────

def test_every_seeded_uk_account_is_on_a_statement():
    for code, _name, _level in UK_SEED_ACCOUNTS:
        if len(code) < 4:
            continue                                        # group headings carry no balances
        if code[0] in "0123":
            assert uk._bs_bucket_for_code(code), code
        else:
            assert uk._pl_bucket_for_code(code), code
    for code, bucket in {"1410": "ca_debtors", "1500": "ca_debtors", "3999": "eq_pl_account", "0040": "fa_tangibles",
                         "1650": "ca_debtors", "2450": "cl_creditors", "3040": "eq_other_reserves"}.items():
        assert uk._bs_bucket_for_code(code)[1] == bucket, code
    assert uk._pl_bucket_for_code("4210") == "other_operating_income"
    assert uk._pl_bucket_for_code("7950") == uk._pl_bucket_for_code("7860") == "admin_expenses"


# ─── The statements ──────────────────────────────────────────────────────────

def test_the_balance_sheet_balances_with_every_account(books):
    body = _get(books, "balance-sheet", as_of="2026-09-27", comparative_as_of="2025-12-31")
    rows = _rows(body)
    assert body["metadata"]["balances"]["net_assets_equals_capital_reserves"] is True
    assert rows["net_assets"]["amount_current"] == rows["total_capital_reserves"]["amount_current"]
    assert rows["ca_debtors"]["amount_current"] == 1_200 + 300 + 100          # prepayment, accrued income, 1300
    assert rows["ca_cash"]["amount_current"] == 25_050
    assert rows["fa_tangibles"]["amount_current"] == 5_000                    # land revalued; equipment sold
    assert rows["fa_intangibles"]["amount_current"] == 1_800
    assert rows["eq_revaluation_reserve"]["amount_current"] == 4_500


def test_the_profit_and_loss(books):
    rows = _rows(_get(books, "profit-and-loss", **PERIOD))
    assert rows["turnover"]["amount_current"] == 10_000                       # returns reduce turnover
    assert rows["other_operating_income"]["amount_current"] == 450            # incl. the FX gain
    assert rows["admin_expenses"]["amount_current"] == -(500 + 200 + 1_500 + 800)
    assert rows["operating_profit"]["amount_current"] == 7_450
    assert rows["profit_for_year"]["amount_current"] == 7_050
    assert rows["depreciation_charged"]["amount_current"] == 500
    assert rows["amortisation_charged"]["amount_current"] == 200
    assert rows["turnover"]["amount_prior"] == 4_000


def test_other_comprehensive_income_is_the_revaluation(books):
    rows = _rows(_get(books, "comprehensive-income", **PERIOD))
    assert rows["oci_revaluation"]["amount_current"] == 5_000                 # the transfer between reserves is not OCI
    assert rows["oci_total"]["amount_current"] == 5_000
    assert rows["total_comprehensive_income"]["amount_current"] == 12_050
    assert rows["oci_total"]["amount_prior"] == 0


def test_changes_in_equity_come_from_the_ledger_and_tie_to_the_balance_sheet(books):
    body = _get(books, "changes-in-equity", **PERIOD)
    rows = {r["key"]: {c["component"]: c["amount"] for c in r["cells"]} for r in body["rows"]}
    assert rows["profit_for_year"]["eq_pl_account"] == 7_050
    assert rows["oci"]["eq_revaluation_reserve"] == 5_000
    assert rows["shares_issued"]["eq_share_capital"] == 1_000 and rows["shares_issued"]["eq_share_premium"] == 9_000
    assert rows["dividends"]["eq_pl_account"] == -1_000
    assert rows["transfer_reserves"]["eq_revaluation_reserve"] == -500 and rows["transfer_reserves"]["eq_pl_account"] == 500
    assert rows["other"] == {**{k: 0 for k in rows["other"]}, "eq_pl_account": 100}       # the 3999 correction
    assert rows["comparative_profit"]["eq_pl_account"] == 4_000
    assert rows["comparative_shares_issued"]["eq_share_capital"] == 5_000
    assert rows["comparative_dividends"]["eq_pl_account"] == -500
    assert "comparative_other" not in rows
    labels = {r["key"]: r["label"] for r in body["rows"]}
    assert labels["comparative_period"] == "Movements in the year ended 2025-12-31"          # chains to the opening
    movement_keys = ("profit_for_year", "oci", "shares_issued", "dividends", "transfer_reserves", "other")
    for comp in rows["closing"]:
        assert rows["opening"][comp] + sum(rows[k].get(comp, 0) for k in movement_keys) == rows["closing"][comp], comp
    bs = _rows(_get(books, "balance-sheet", as_of="2026-09-27"))
    for comp, amount in rows["closing"].items():
        assert bs[comp]["amount_current"] == amount, comp
    assert rows["closing"]["eq_share_capital"] == 6_000 and rows["opening"]["eq_share_capital"] == 5_000   # not zero
    assert rows["opening"]["eq_pl_account"] == 3_500


def test_the_cash_flow(books):
    rows = _rows(_get(books, "cash-flow", **PERIOD))
    c = {k: v["amount_current"] for k, v in rows.items()}
    assert c["fin_share_capital_inflow"] == 10_000                          # capital and premium together
    assert "fin_share_premium_inflow" not in c
    assert c["fin_dividends_outflow"] == -1_000
    assert c["fin_director_loans_inflow"] == 3_000
    assert c["inv_ppe_outflow"] == -6_000 and c["inv_ppe_inflow"] == 4_000 and c["inv_intangibles_outflow"] == -2_000
    assert c["op_tax_paid"] == -400
    assert c["op_other"] == 8_950
    assert c["net_cash_change"] == 16_550 and c["opening_cash"] == 8_500 and c["closing_cash"] == 25_050
    assert c["fx_effect"] == 0
    # the reconciliation note
    assert c["recon_operating_profit"] == 7_450
    assert c["recon_depreciation"] == 500 and c["recon_amortisation"] == 200
    assert c["recon_fixed_asset_pl"] == 1_500                                # the loss on the equipment
    assert c["recon_debtors"] == -1_600 and c["recon_creditors"] == 800 and c["recon_stocks"] == 0
    assert c["recon_other"] == 100                                           # the non-cash 3999 correction, shown
    assert c["recon_cash_generated"] == c["op_other"]
    assert rows["recon_cash_generated"]["amount_prior"] == rows["op_other"]["amount_prior"] == 4_000


def test_prior_year_dividend_paid_from_the_reserve_is_a_dividend(books):
    rows = _rows(_get(books, "cash-flow", **PERIOD))
    assert rows["fin_dividends_outflow"]["amount_prior"] == -500


def test_a_clean_year_needs_no_reconciling_line():
    cur = {k: 0 for k, _ in uk._UK_RECON_LINES} | {"operating_profit": 100}
    rows = {r.key: r for r in uk._reconciliation_rows(cur, dict(cur), 100, 100)}
    assert "recon_other" not in rows and rows["recon_cash_generated"].amount_current == 100

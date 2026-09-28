"""The Iranian statements for the seeded chart: its 4-digit codes do not
follow the national standard's 3-digit groups, so VAT receivable (1130) is
not a short-term investment, accrued income (1140) is not inventory, wages
payable (2180) is not a liability of assets held for sale, and paying an
accrued expense (2140) is not a dividend."""
from __future__ import annotations

import pytest

from app.db.seed import SEED_ACCOUNTS
from app.services.reporting.iran_statement_service import _bs_bucket_for_code, _cf_bucket, _is_cash_code

EXPECTED_BS = {
    "1110": "ca_cash", "1112": "ca_trade_receivables", "1120": "ca_prepayments",
    "1130": "ca_trade_receivables", "1140": "ca_trade_receivables", "1150": "ca_prepayments",
    "1160": "ca_cash", "1210": "nca_ppe", "1219": "nca_ppe",
    "2110": "cl_trade_payables", "2120": "cl_advances", "2130": "cl_trade_payables",
    "2140": "cl_trade_payables", "2145": "cl_dividends_payable", "2155": "cl_trade_payables",
    "2160": "cl_trade_payables", "2170": "cl_trade_payables", "2180": "cl_trade_payables",
    "2190": "cl_trade_payables", "2195": "cl_trade_payables",
    "3110": "eq_capital", "3150": "eq_revaluation_surplus", "3300": "eq_retained_earnings",
}


@pytest.mark.parametrize("code,bucket", sorted(EXPECTED_BS.items()))
def test_each_seeded_account_is_on_its_balance_sheet_line(code, bucket):
    assert _bs_bucket_for_code(code)[1] == bucket


def test_every_seeded_balance_sheet_account_is_pinned():
    """A new balance-sheet account in the seed must be placed on purpose, not
    by whichever 3-digit group its code happens to start with."""
    seeded = {c for c, _n, level in SEED_ACCOUNTS if len(c) == 4 and c[0] in "123"}
    assert seeded <= set(EXPECTED_BS) | {"1160"}, sorted(seeded - set(EXPECTED_BS))


@pytest.mark.parametrize("code,section", [
    ("1130", "operating"), ("1150", "operating"), ("2130", "operating"), ("2140", "operating"),
    ("1210", "investing"), ("2145", "financing"), ("2155", "financing"), ("3110", "financing"),
])
def test_cash_flow_sections_for_seeded_counterparties(code, section):
    assert _cf_bucket(code)[0] == section


def test_petty_cash_is_cash_in_both_statements():
    assert _is_cash_code("1160") and _is_cash_code("1110") and _is_cash_code("111001")
    assert not _is_cash_code("1112") and not _is_cash_code(None)


def _post(api, when, lines, desc):
    r = api.post("/transactions", json={"date": when, "description": desc, "lines": [
        {"account_code": c, "debit": d, "credit": k} for c, d, k in lines]})
    assert r.status_code == 201, r.text


def _rows(body):
    out = {}

    def walk(items):
        for it in items:
            out[it["key"]] = it
            walk(it.get("children") or [])
    walk(body.get("rows") or [])
    return out


def test_the_statements_for_a_small_iranian_company(auth_client, db):
    from tests.test_iran_statements import _purge_transactions
    from app.services.account_resolver import resolve_account_code
    _purge_transactions(db)
    resolve_account_code(db, "petty_cash", locale="ir")                       # 1160 on an older chart
    db.commit()
    api = auth_client
    _post(api, "2024-07-01", [("1110", 50_000_000, 0), ("3110", 0, 50_000_000)], "capital")
    _post(api, "2024-07-02", [("1130", 900_000, 0), ("1110", 0, 900_000)], "input VAT paid")
    _post(api, "2024-07-03", [("1150", 1_200_000, 0), ("1110", 0, 1_200_000)], "rent paid ahead")
    _post(api, "2024-07-04", [("6112", 700_000, 0), ("2140", 0, 700_000)], "electricity accrued")
    _post(api, "2024-07-05", [("2140", 700_000, 0), ("1110", 0, 700_000)], "electricity paid")
    _post(api, "2024-07-06", [("6110", 4_000_000, 0), ("2180", 0, 4_000_000)], "wages due")
    _post(api, "2024-07-07", [("1160", 2_000_000, 0), ("1110", 0, 2_000_000)], "petty cash float")

    bs = api.get("/manager-reports/financial/iran/balance-sheet", params={"as_of": "2024-07-31"})
    assert bs.status_code == 200, bs.text
    rows = _rows(bs.json())
    assert rows["ca_cash"]["amount_current"] == 50_000_000 - 900_000 - 1_200_000 - 700_000     # petty cash stays cash
    assert rows["ca_trade_receivables"]["amount_current"] == 900_000                          # VAT receivable
    assert rows["ca_prepayments"]["amount_current"] == 1_200_000
    assert rows.get("ca_st_investments", {}).get("amount_current", 0) == 0
    assert rows.get("ca_held_for_sale", {}).get("amount_current", 0) == 0
    assert rows["cl_trade_payables"]["amount_current"] == 4_000_000                           # wages payable
    assert rows.get("cl_held_for_sale_liab", {}).get("amount_current", 0) == 0

    cf = api.get("/manager-reports/financial/iran/cash-flow",
                 params={"from_date": "2024-07-01", "to_date": "2024-07-31"})
    assert cf.status_code == 200, cf.text
    c = _rows(cf.json())
    assert c["investing_net"]["amount_current"] == 0                                          # VAT, prepaid: operating
    assert c["financing_net"]["amount_current"] == 50_000_000                                 # no "dividend" for 2140
    assert c["operating_net"]["amount_current"] == -(900_000 + 1_200_000 + 700_000)           # the float is not a flow
    assert c["fx_rate_effect"]["amount_current"] == 0                                         # flows explain the cash
    assert c["closing_cash"]["amount_current"] == rows["ca_cash"]["amount_current"]
    _purge_transactions(db)

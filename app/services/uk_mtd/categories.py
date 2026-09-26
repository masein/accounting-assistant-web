"""HMRC's quarterly-update categories and the default mapping from the UK
(Sage-style) chart of accounts.

Field names are HMRC's own, from the published schemas:
* Self Employment Business API — ``periodIncome`` / ``periodExpenses``
  (+ the ``…Disallowable`` twins), or ``consolidatedExpenses``;
* Property Business API, UK property — ``income`` / ``expenses``.

The mapping is a starting point: an owner overrides any account in the MTD
settings. ``excluded`` keeps an account out of the business figures (tax,
interest received, and depreciation for property, which is never allowable).
"""
from __future__ import annotations

SELF_EMPLOYMENT_INCOME = ("turnover", "other")
SELF_EMPLOYMENT_EXPENSES = (
    "costOfGoods", "paymentsToSubcontractors", "wagesAndStaffCosts", "carVanTravelExpenses",
    "premisesRunningCosts", "maintenanceCosts", "adminCosts", "businessEntertainmentCosts", "advertisingCosts",
    "interestOnBankOtherLoans", "financeCharges", "irrecoverableDebts", "professionalFees", "depreciation",
    "otherExpenses",
)
# Reported as expenses and, in full, as disallowable: never deductible for
# income tax (capital allowances replace depreciation; entertaining is barred).
SELF_EMPLOYMENT_DISALLOWED_IN_FULL = ("businessEntertainmentCosts", "depreciation")

PROPERTY_INCOME = ("periodAmount", "premiumsOfLeaseGrant", "reversePremiums", "otherIncome", "rentARoomRents")
PROPERTY_EXPENSES = (
    "premisesRunningCosts", "repairsAndMaintenance", "financialCosts", "professionalFees", "costOfServices",
    "other", "residentialFinancialCost", "travelCosts", "rentARoomClaimed",
)

EXCLUDED = "excluded"

CATALOGUE = {
    "self_employment": {"income": SELF_EMPLOYMENT_INCOME, "expenses": SELF_EMPLOYMENT_EXPENSES},
    "uk_property": {"income": PROPERTY_INCOME, "expenses": PROPERTY_EXPENSES},
}


def all_categories() -> set[str]:
    out = {EXCLUDED}
    for part in CATALOGUE.values():
        out |= set(part["income"]) | set(part["expenses"])
    return out


def kind_of(category: str, source: str) -> str | None:
    """'income', 'expenses' or None (excluded / not in this source)."""
    part = CATALOGUE.get(source) or {}
    for kind in ("income", "expenses"):
        if category in part.get(kind, ()):
            return kind
    return None


# UK chart (app/db/seed.py) → category, per income source. Longest prefix wins.
_SE_DEFAULTS = {
    "4000": "turnover", "4100": "turnover", "4200": "other",
    "5000": "costOfGoods", "5100": "wagesAndStaffCosts", "5200": "costOfGoods", "5900": "costOfGoods",
    "6": "advertisingCosts",
    "7000": "wagesAndStaffCosts", "7050": "otherExpenses", "7100": "wagesAndStaffCosts",
    "7200": "premisesRunningCosts", "7300": "premisesRunningCosts", "7400": "carVanTravelExpenses",
    "7500": "carVanTravelExpenses", "7600": "adminCosts", "7700": "maintenanceCosts", "7800": "professionalFees",
    "7850": "adminCosts", "7900": "irrecoverableDebts",
    "8000": "financeCharges", "8100": "interestOnBankOtherLoans", "8200": "interestOnBankOtherLoans",
    "8300": EXCLUDED, "8400": EXCLUDED, "8500": "depreciation", "8600": "depreciation",
    "9": EXCLUDED,
    "4": "other", "5": "costOfGoods", "7": "otherExpenses", "8": "otherExpenses",
}
_PROPERTY_DEFAULTS = {
    "4000": "periodAmount", "4100": "periodAmount", "4200": "otherIncome",
    "5": "costOfServices", "7000": "costOfServices", "7100": "costOfServices",
    "7200": "premisesRunningCosts", "7300": "premisesRunningCosts", "7400": "travelCosts", "7500": "travelCosts",
    "7700": "repairsAndMaintenance", "7800": "professionalFees",
    # finance costs on residential lets are restricted to a basic-rate credit
    "8100": "residentialFinancialCost", "8200": "residentialFinancialCost",
    "8300": EXCLUDED, "8400": EXCLUDED, "8500": EXCLUDED, "8600": EXCLUDED, "9": EXCLUDED,
    "4": "otherIncome", "6": "other", "7": "other", "8": "other",
}
DEFAULTS = {"self_employment": _SE_DEFAULTS, "uk_property": _PROPERTY_DEFAULTS}


def default_category(code: str, source: str) -> str | None:
    table = DEFAULTS.get(source) or {}
    best = None
    for prefix, cat in table.items():
        if (code or "").startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, cat)
    return best[1] if best else None

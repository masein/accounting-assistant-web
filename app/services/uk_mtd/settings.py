"""Per-company MTD settings (app_settings key "uk_mtd")."""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

KEY = "uk_mtd"
INCOME_SOURCES = ("none", "self_employment", "uk_property")
PERIOD_BASES = ("standard", "calendar")
VAT_STAGGERS = ("1", "2", "3", "monthly")


class MtdSettingsError(ValueError):
    pass


def _defaults() -> dict:
    return {"income_source": "none", "period_basis": "standard", "vat_registered": False, "vat_stagger": "1",
            "vrn": "", "category_overrides": {}}


def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == KEY)).scalars().first()


def get_settings(db: Session) -> dict:
    out = _defaults()
    row = _row(db)
    if row and row.value:
        try:
            saved = json.loads(row.value)
        except ValueError:
            saved = {}
        if isinstance(saved, dict):
            out.update({k: saved[k] for k in out if k in saved})
    return out


def valid_vrn(vrn: str) -> bool:
    """A UK VAT registration number: 9 digits (optionally GB-prefixed) whose
    weighted checksum works under the original or the 2010 (+55) rule."""
    digits = re.sub(r"\s", "", (vrn or "").upper()).removeprefix("GB")
    if not re.fullmatch(r"\d{9}", digits):
        return False
    total = sum(int(d) * w for d, w in zip(digits[:7], range(8, 1, -1))) + int(digits[7:])
    return total % 97 == 0 or (total + 55) % 97 == 0


def save_settings(db: Session, **changes) -> dict:
    cur = get_settings(db)
    if "income_source" in changes and changes["income_source"] is not None:
        if changes["income_source"] not in INCOME_SOURCES:
            raise MtdSettingsError(f"income_source must be one of {INCOME_SOURCES}")
        cur["income_source"] = changes["income_source"]
    if "period_basis" in changes and changes["period_basis"] is not None:
        if changes["period_basis"] not in PERIOD_BASES:
            raise MtdSettingsError("period_basis must be standard or calendar")
        cur["period_basis"] = changes["period_basis"]
    if "vat_registered" in changes and changes["vat_registered"] is not None:
        cur["vat_registered"] = bool(changes["vat_registered"])
    if "vat_stagger" in changes and changes["vat_stagger"] is not None:
        st = str(changes["vat_stagger"])
        if st not in VAT_STAGGERS:
            raise MtdSettingsError("vat_stagger must be 1, 2, 3 or monthly")
        cur["vat_stagger"] = st
    if "vrn" in changes and changes["vrn"] is not None:
        vrn = re.sub(r"\s", "", changes["vrn"].upper()).removeprefix("GB")
        if vrn and not valid_vrn(vrn):
            raise MtdSettingsError("That is not a valid UK VAT registration number")
        cur["vrn"] = vrn
    if "category_overrides" in changes and changes["category_overrides"] is not None:
        overrides = changes["category_overrides"]
        if not isinstance(overrides, dict):
            raise MtdSettingsError("category_overrides must map account codes to categories")
        from app.services.uk_mtd.categories import all_categories
        known = all_categories()
        cleaned = {}
        for code, cat in overrides.items():
            code = str(code).strip()
            if not code or not isinstance(cat, str) or not cat.strip():
                continue  # an empty choice clears the override
            if cat.strip() not in known:
                raise MtdSettingsError(f"Unknown HMRC category {cat!r} for account {code}")
            cleaned[code] = cat.strip()
        cur["category_overrides"] = cleaned
    row = _row(db)
    if row is None:
        db.add(AppSetting(key=KEY, value=json.dumps(cur)))
    else:
        row.value = json.dumps(cur)
    db.flush()
    return cur

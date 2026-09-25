"""Per-company سامانه مودیان settings (app_settings key "moadian")."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.services.moadian.taxid import normalize_memory_id, valid_memory_id

KEY = "moadian"
DEFAULT_DEADLINE_DAYS = 12   # ماده ۲۲ قانون پایانه‌های فروشگاهی و سامانه مودیان
WARN_DAYS_BEFORE = 3


class MoadianSettingsError(ValueError):
    pass


def _defaults() -> dict:
    return {"memory_id": "", "default_sstid": "", "default_mu": "", "deadline_days": DEFAULT_DEADLINE_DAYS}


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


def enabled(db: Session) -> bool:
    """A company "uses" مودیان once it has entered its memory id."""
    return valid_memory_id(get_settings(db).get("memory_id"))


def save_settings(db: Session, **changes) -> dict:
    cur = get_settings(db)
    if changes.get("memory_id") is not None:
        mid = normalize_memory_id(changes["memory_id"])
        if mid and not valid_memory_id(mid):
            raise MoadianSettingsError("The memory id (شناسه یکتای حافظه مالیاتی) is 6 letters or digits.")
        cur["memory_id"] = mid
    if changes.get("default_sstid") is not None:
        v = str(changes["default_sstid"]).strip()
        if v and not (v.isdigit() and len(v) == 13):
            raise MoadianSettingsError("The default goods/service id is 13 digits.")
        cur["default_sstid"] = v
    if changes.get("default_mu") is not None:
        v = str(changes["default_mu"]).strip()
        if v and not (v.isdigit() and len(v) <= 8):
            raise MoadianSettingsError("The measurement-unit code is digits only.")
        cur["default_mu"] = v
    if changes.get("deadline_days") is not None:
        d = int(changes["deadline_days"])
        if not 1 <= d <= 60:
            raise MoadianSettingsError("The deadline must be between 1 and 60 days.")
        cur["deadline_days"] = d
    row = _row(db)
    if row is None:
        db.add(AppSetting(key=KEY, value=json.dumps(cur)))
    else:
        row.value = json.dumps(cur)
    db.flush()
    return cur

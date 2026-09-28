"""Printing an issued cheque on its leaf (roadmap 2026-09 §3.4, part 2).

A PDF the size of the cheque with only what the drawer writes: the date in
figures and (Iranian cheques) in words, the payee (در وجه) and, since Sayad
cheques, the payee's national id (کد / شناسه ملی), the amount in words and in
figures. The bank prints everything else on the leaf.

Leaves differ between banks by a few millimetres, so the layout is the
company's (app_settings key ``cheque_print_layout``): the page size, a global
offset and each field's position, in millimetres from the leaf's top-left
corner. A *guide* print adds the leaf outline and each field's box and name —
print it on plain paper, hold it against a real cheque, nudge, save.
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

KEY = "cheque_print_layout"

FIELDS = ("date", "date_words", "payee", "national_id", "amount_words", "amount")

# Field boxes in mm from the leaf's top-left: x, y, width. Iranian Sayad leaf:
# bank and Sayad id along the top, the date under the id on the left, the date
# in words across the top line, the amount in words below it, then the payee
# (right) and their national id (left), and the figures box bottom-left.
DEFAULTS: dict[str, dict] = {
    "ir": {
        "width": 175, "height": 80, "offset_x": 0, "offset_y": 0, "font_size": 11,
        "fields": {
            "date": {"x": 10, "y": 17, "w": 40},
            "date_words": {"x": 58, "y": 17, "w": 90},
            "amount_words": {"x": 18, "y": 29, "w": 140},
            "payee": {"x": 62, "y": 41, "w": 96},
            "national_id": {"x": 10, "y": 41, "w": 46},
            "amount": {"x": 10, "y": 55, "w": 46},
        },
    },
    # UK cheque: date top right, payee line, amount in words on two lines, the
    # figures box on the right.
    "uk": {
        "width": 178, "height": 80, "offset_x": 0, "offset_y": 0, "font_size": 11,
        "fields": {
            "date": {"x": 132, "y": 12, "w": 38},
            "payee": {"x": 16, "y": 27, "w": 110},
            "amount_words": {"x": 16, "y": 38, "w": 110},
            "amount": {"x": 134, "y": 38, "w": 36},
        },
    },
}


def locale_of(db: Session) -> str:
    from app.services.locale_service import get_reporting_locale
    return "uk" if (get_reporting_locale(db) or "").lower() == "uk" else "ir"


def _row(db: Session) -> AppSetting | None:
    return db.execute(select(AppSetting).where(AppSetting.key == KEY)).scalars().first()


def get_layout(db: Session, locale: str | None = None) -> dict:
    """The company's layout for its locale, over the defaults."""
    loc = locale or locale_of(db)
    base = json.loads(json.dumps(DEFAULTS[loc]))
    row = _row(db)
    saved = {}
    if row and row.value:
        try:
            saved = (json.loads(row.value) or {}).get(loc) or {}
        except ValueError:
            saved = {}
    for k in ("width", "height", "offset_x", "offset_y", "font_size"):
        if isinstance(saved.get(k), (int, float)):
            base[k] = saved[k]
    for name, box in (saved.get("fields") or {}).items():
        if name in base["fields"] and isinstance(box, dict):
            base["fields"][name].update({k: box[k] for k in ("x", "y", "w") if isinstance(box.get(k), (int, float))})
    base["locale"] = loc
    return base


def _num(value, lo: float, hi: float, what: str) -> float:
    try:
        v = round(float(value), 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{what} must be a number.") from None
    if not lo <= v <= hi:
        raise HTTPException(status_code=422, detail=f"{what} must be between {lo:g} and {hi:g}.")
    return int(v) if v == int(v) else v


def save_layout(db: Session, layout: dict) -> dict:
    """Validate and store one locale's layout; every box stays on the leaf."""
    loc = locale_of(db)
    cur = get_layout(db, loc)
    width = _num(layout.get("width", cur["width"]), 100, 250, "The width")
    height = _num(layout.get("height", cur["height"]), 50, 120, "The height")
    new = {
        "width": width, "height": height,
        "offset_x": _num(layout.get("offset_x", cur["offset_x"]), -20, 20, "The horizontal offset"),
        "offset_y": _num(layout.get("offset_y", cur["offset_y"]), -20, 20, "The vertical offset"),
        "font_size": _num(layout.get("font_size", cur["font_size"]), 7, 18, "The font size"),
        "fields": {},
    }
    given = layout.get("fields") or {}
    for name, box in cur["fields"].items():
        b = {**box, **{k: v for k, v in (given.get(name) or {}).items() if k in ("x", "y", "w")}}
        w = _num(b["w"], 10, width, f"The width of {name}")
        new["fields"][name] = {
            "x": _num(b["x"], 0, width - w, f"The left edge of {name}"),
            "y": _num(b["y"], 0, height - 5, f"The top of {name}"),
            "w": w,
        }
    row = _row(db)
    stored = {}
    if row and row.value:
        try:
            stored = json.loads(row.value) or {}
        except ValueError:
            stored = {}
    stored[loc] = new
    if row is None:
        db.add(AppSetting(key=KEY, value=json.dumps(stored)))
    else:
        row.value = json.dumps(stored)
    db.flush()
    return get_layout(db, loc)


def reset_layout(db: Session) -> dict:
    loc = locale_of(db)
    row = _row(db)
    if row and row.value:
        stored = json.loads(row.value) or {}
        stored.pop(loc, None)
        row.value = json.dumps(stored)
        db.flush()
    return get_layout(db, loc)


# --- what goes on the leaf --------------------------------------------------------------------------------------

JALALI_MONTHS_FA = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")


def _fa_ordinal(n: int) -> str:
    from num2fawords import ordinal_words
    w = ordinal_words(n)
    return {"سیم": "سی‌ام"}.get(w, w)          # the library's archaic form of 30th


def date_in_words_fa(d: date) -> str:
    """۵ مهر ۱۴۰۵ → «پنجم مهر ماه یک هزار و چهارصد و پنج»."""
    import jdatetime
    from num2fawords import words
    j = jdatetime.date.fromgregorian(date=d)
    return f"{_fa_ordinal(j.day)} {JALALI_MONTHS_FA[j.month - 1]} ماه {words(j.year)}"


def cheque_values(db: Session, row, *, payee: str | None = None, national_id: str | None = None,
                  on: date | None = None) -> dict:
    """The text of each field, in the company's language."""
    from app.services.documents.formatting import amount_in_words, to_persian_digits
    from app.services.fx_base import base_currency
    loc = locale_of(db)
    when = on or row.due_date
    ccy = base_currency(db)
    name = (payee if payee is not None else None) or row.counterparty or ""
    nid = national_id
    if nid is None and row.entity_id:
        from app.models.entity import Entity
        ent = db.get(Entity, row.entity_id)
        name = name or getattr(ent, "name", "") or ""
        nid = getattr(ent, "national_id", None) or ""
    amount = int(row.amount or 0)
    if loc == "ir":
        import jdatetime
        j = jdatetime.date.fromgregorian(date=when)
        return {
            "date": to_persian_digits(f"{j.year:04d}/{j.month:02d}/{j.day:02d}"),
            "date_words": date_in_words_fa(when),
            "payee": name,
            "national_id": to_persian_digits(nid or ""),
            "amount_words": amount_in_words(amount, ccy, "ir"),
            # guards either side: nothing can be written in front of or after the figures
            "amount": "#" + to_persian_digits(f"{amount:,}").replace(",", "٬") + "#",
        }
    from app.services.documents.formatting import CURRENCY_SYMBOL
    symbol = CURRENCY_SYMBOL.get(ccy, ccy + " ")
    return {
        "date": when.strftime("%d/%m/%Y"),
        "payee": name,
        "amount_words": amount_in_words(amount, ccy, "uk") + " only",
        "amount": f"{symbol}{amount:,}.00",
    }


SAMPLE = {"payee": "نمونه — Sample", "national_id": "0012345678"}


def fit_font_size(text: str, width_mm: float, size_pt: float) -> float:
    """Shrink a long line to its box: a glyph is about half an em wide
    (1 pt = 0.3528 mm), never below 7 pt."""
    if not text:
        return size_pt
    fits = width_mm / (size_pt * 0.3528 * 0.5)
    if len(text) <= fits:
        return size_pt
    return max(7.0, round(size_pt * fits / len(text), 1))


def render_html(db: Session, values: dict, *, guide: bool = False) -> str:
    from app.services.documents.engine import render_html as _render
    from app.services.documents.labels import labels_for
    layout = get_layout(db)
    loc = layout["locale"]
    L = labels_for(loc)
    names = {
        "date": L.get("date", "Date"), "date_words": "تاریخ به حروف" if loc == "ir" else "Date in words",
        "payee": "در وجه" if loc == "ir" else "Pay", "national_id": "کد / شناسه ملی" if loc == "ir" else "ID",
        "amount_words": L.get("amount_in_words", "Amount in words"),
        "amount": "مبلغ (ریال)" if loc == "ir" else "Amount",
    }
    boxes = []
    for name, box in layout["fields"].items():
        text = values.get(name)
        if not text and not guide:
            continue
        figures = name in ("date", "amount", "national_id")
        boxes.append({
            "name": name, "label": names[name], "text": text or "",
            "x": box["x"] + layout["offset_x"], "y": box["y"] + layout["offset_y"], "w": box["w"],
            "font_size": fit_font_size(text or "", box["w"], layout["font_size"]),
            # figures read left to right; words follow the language
            "dir": "ltr" if figures or loc != "ir" else "rtl",
            "align": "center" if figures else ("right" if loc == "ir" else "left"),
        })
    return _render({"layout": layout, "boxes": boxes, "guide": guide, "rtl": loc == "ir"}, "cheque.html")


def render_pdf(db: Session, values: dict, *, guide: bool = False) -> bytes:
    from app.services.documents.engine import html_to_pdf
    return html_to_pdf(render_html(db, values, guide=guide))

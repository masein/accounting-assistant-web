"""Money with its pence.

Amounts are exact decimals in the currency's own unit: £7.50 is 7.50, ten
million rials is 10000000. A currency has as many decimal places as ISO 4217
gives it (pounds, dollars and euros 2; rials 0; dinars 3), and an amount never
carries more than its currency's.

The app kept whole units only, so a £7.50 bank fee went into the books as £7
and a statement could never reconcile to the penny (owner's decision,
2026-10-12: keep pence). Amounts that are whole come back from the database
as plain ints, exactly as before, so rial books and every whole-pound figure
read, add up and print as they always did; only an amount with pence is a
``Decimal``.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Union

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema
from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator

# ISO 4217 minor units, where they aren't 2. Rials are kept in whole rials:
# ISO gives IRR 2, but no one writes a fraction of a rial (nor of a toman).
_EXPONENTS = {
    "IRR": 0, "IRT": 0, "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "PYG": 0, "UGX": 0,
    "XAF": 0, "XOF": 0, "XPF": 0, "BIF": 0, "DJF": 0, "GNF": 0, "KMF": 0, "RWF": 0, "VUV": 0,
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
}
MAX_PLACES = 4          # what a column holds: a unit price may go to 4 places (£0.1250)


def exponent(currency: str | None) -> int:
    """How many decimal places ``currency`` has: 2 unless ISO says otherwise."""
    return _EXPONENTS.get((currency or "").strip().upper(), 2)


def to_decimal(value: Any) -> Decimal:
    """An amount as an exact Decimal. A float goes through its shortest repr
    (7.1 is 7.1, not 7.0999999999999996447…); text is taken as written."""
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, bool):
        raise ValueError("an amount can't be true or false")
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation as e:
            raise ValueError(f"{value!r} is not an amount") from e
    else:
        raise ValueError(f"{value!r} is not an amount")
    if not d.is_finite():
        raise ValueError("an amount must be a finite number")
    return d


def plain(value: Any) -> int | Decimal:
    """An amount as the app hands it around: an int when it is whole, else the
    exact Decimal without trailing zeros (7.5, not 7.5000)."""
    if value is None:
        return None  # type: ignore[return-value]
    d = to_decimal(value)
    if d == d.to_integral_value():
        return int(d)
    return d.normalize()


def quantize(value: Any, currency: str | None) -> int | Decimal:
    """``value`` rounded half-up to ``currency``'s places: £7.505 → 7.51,
    2,500,000.4 rials → 2500000."""
    places = exponent(currency)
    d = to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return plain(d)


def places_of(value: Any) -> int:
    """How many decimal places ``value`` is written with (7.50 → 1: 7.5)."""
    d = to_decimal(value).normalize()
    return max(0, -d.as_tuple().exponent) if d != d.to_integral_value() else 0


def check_places(value: Any, currency: str | None, *, what: str = "amount") -> int | Decimal:
    """``value`` if it has no more places than ``currency`` allows, else a
    ValueError a person can act on («pounds have 2 decimal places»)."""
    allowed = exponent(currency)
    if places_of(value) > allowed:
        cur = (currency or "").upper() or "this currency"
        if allowed == 0:
            raise ValueError(f"The {what} can't have decimals in {cur}.")
        raise ValueError(f"The {what} can have at most {allowed} decimal places in {cur}.")
    return plain(value)


def json_amount(value: Any) -> int | float | None:
    """For JSON: whole amounts stay integers (what every client has always
    read), others go as numbers with their places (7.5)."""
    if value is None:
        return None
    p = plain(value)
    return p if isinstance(p, int) else float(p)


# --- the column ---------------------------------------------------------------------------------------

class MoneyType(TypeDecorator):
    """A money column: NUMERIC without a fixed scale (Postgres keeps each
    value as written, so whole amounts sum and print as integers, as the
    BIGINT columns did), read back as an int when whole."""

    impl = Numeric
    cache_ok = True

    def __init__(self):
        super().__init__(asdecimal=True)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        p = plain(value)
        if isinstance(p, Decimal) and places_of(p) > MAX_PLACES:
            raise ValueError(f"amount {p} has more than {MAX_PLACES} decimal places")
        return Decimal(p)

    def process_result_value(self, value, dialect):
        return plain(value) if value is not None else None

    @property
    def python_type(self):
        return Decimal


# --- the API field ------------------------------------------------------------------------------------

def _validate(value: Any) -> int | Decimal:
    p = plain(value)
    if isinstance(p, Decimal) and places_of(p) > MAX_PLACES:
        raise ValueError(f"an amount can have at most {MAX_PLACES} decimal places")
    return p


Money = Annotated[
    Union[int, Decimal],
    BeforeValidator(_validate),
    PlainSerializer(json_amount, when_used="json"),
    WithJsonSchema({"type": "number", "description": "An amount in the currency's own unit (7.50 is £7.50)."}),
]
"""An amount in an API body: 950, 7.5 or "7.50"; whole amounts go back out as integers."""


# --- JSON anywhere -------------------------------------------------------------------------------------

def json_dumps(obj, *, fallback=None, **kw) -> str:
    """``json.dumps`` that writes an amount with pence as a number (7.5) and a
    whole one as an integer; anything else it can't write goes to ``fallback``
    (``str`` for a date or an id), else it is an error. The engines use it for
    every JSON column, so a reply's blocks, a tool's result or a proposal's
    lines never fail to save because a figure has pence."""
    import json

    def default(o):
        if isinstance(o, Decimal):
            return json_amount(o)
        if fallback is not None:
            return fallback(o)
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, default=default, **kw)

"""Money with its pence (docs/PLAN_PENCE.md, phase P1).

The books kept whole units of every currency, so a £7.50 bank fee went in as
£7. Amounts are now exact decimals in the currency's own unit; whole amounts
still come back as ints, so nothing that reads whole amounts changes."""
from __future__ import annotations


from decimal import Decimal

import pytest
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.core.money import Money, MoneyType, check_places, exponent, json_amount, places_of, plain, quantize


def test_each_currency_has_its_places():
    assert [exponent(c) for c in ("GBP", "usd", "EUR", "IRR", "IRT", "JPY", "KWD", None, "XYZ")] == \
        [2, 2, 2, 0, 0, 0, 3, 2, 2]


@pytest.mark.parametrize("value, currency, out", [
    ("7.505", "GBP", Decimal("7.51")), ("7.504", "GBP", Decimal("7.5")), (7.5, "GBP", Decimal("7.5")),
    ("950.00", "GBP", 950), (2_500_000.4, "IRR", 2_500_000), (2_500_000.5, "IRR", 2_500_001),
    ("1.2345", "KWD", Decimal("1.235")), (-7.505, "GBP", Decimal("-7.51")),
])
def test_rounding_is_half_up_to_the_currencys_places(value, currency, out):
    got = quantize(value, currency)
    assert got == out and type(got) is type(out)


def test_whole_amounts_are_ints_and_others_exact_decimals():
    assert plain(Decimal("950.0000")) == 950 and type(plain(Decimal("950.0000"))) is int
    assert plain(7.1) == Decimal("7.1")                     # through repr, not the float's binary value
    assert plain("12.3400") == Decimal("12.34") and places_of("12.3400") == 2
    assert json_amount(Decimal("7.50")) == 7.5 and json_amount(950) == 950 and json_amount(None) is None


def test_too_many_places_is_said_so():
    assert check_places("7.50", "GBP") == Decimal("7.5")
    with pytest.raises(ValueError, match="at most 2 decimal places in GBP"):
        check_places("7.505", "GBP")
    with pytest.raises(ValueError, match="can't have decimals in IRR"):
        check_places("2500000.5", "IRR")


def test_an_api_field_takes_pence_and_gives_whole_amounts_back_as_integers():
    class Body(BaseModel):
        amount: Money = Field(0, ge=0, le=10**14)

    assert Body(amount=950).amount == 950 and type(Body(amount=950).amount) is int
    assert Body(amount="7.50").amount == Decimal("7.5")
    assert Body(amount=7.5).model_dump_json() == '{"amount":7.5}'
    assert Body(amount=950).model_dump_json() == '{"amount":950}'
    for bad in (True, "x", float("nan"), "0.00001", -1, 10**15):
        with pytest.raises(ValidationError):
            Body(amount=bad)


def test_a_money_column_keeps_pence_and_reads_whole_amounts_as_ints(db):
    from app.models.transaction import TransactionLine
    col = TransactionLine.__table__.c.debit
    assert isinstance(col.type, MoneyType)
    t = MoneyType()
    assert t.process_result_value(Decimal("950.0000"), None) == 950
    assert t.process_result_value(Decimal("7.5000"), None) == Decimal("7.5")
    assert t.process_bind_param(7.5, None) == Decimal("7.5")
    with pytest.raises(ValueError):
        t.process_bind_param(Decimal("0.00001"), None)


def test_the_migration_converts_every_money_column():
    """A money column added to a model must be in migration 075's list (or a
    later migration), or Postgres keeps it BIGINT and drops the pence."""
    import importlib.util
    import pathlib

    import app.models  # noqa: F401
    from app.db.base import Base
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "075_money_keeps_pence.py"
    spec = importlib.util.spec_from_file_location("m075", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    listed = {(t, c) for t, cols in mod.MONEY.items() for c in cols}
    models = {(t.name, c.name) for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, MoneyType)}
    assert listed == models


def test_a_journal_line_with_pence_round_trips(db):
    """Stored and read back through the ORM: 7.50 stays 7.5; 950 stays an int."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from datetime import date
    acc = db.execute(select(Account)).scalars().first()
    if acc is None:
        pytest.skip("no chart seeded")
    t = Transaction(date=date(2026, 10, 1), description="money round trip", currency="GBP")
    db.add(t)
    db.flush()
    db.add_all([TransactionLine(transaction_id=t.id, account_id=acc.id, debit=Decimal("7.50"), credit=0),
                TransactionLine(transaction_id=t.id, account_id=acc.id, debit=950, credit=0)])
    db.flush()
    db.expire_all()
    got = sorted((ln.debit for ln in db.execute(select(TransactionLine).where(TransactionLine.transaction_id == t.id)).scalars()),
                 key=lambda d: Decimal(d))
    assert got == [Decimal("7.5"), 950] and type(got[1]) is int
    db.rollback()

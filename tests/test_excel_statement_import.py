"""Excel bank-statement parsing.

parse_excel used to reach the shared row logic by joining cells into CSV text
and re-parsing it. Three things fell out of that: the caller's column_map was
dropped (so the UI's "map your columns" retry could never succeed for a
spreadsheet), the result was labelled source_type="csv", and a comma inside any
cell shifted every column after it.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services.bank_statement_parser import parse_csv, parse_excel

openpyxl = pytest.importorskip("openpyxl")


def _sheet(tmp_path: Path, rows: list[list[str]], name: str = "s.xlsx") -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    path = tmp_path / name
    wb.save(path)
    return str(path)


HEADERED = [
    ["Date", "Description", "Amount"],
    ["2026-08-03", "خرید اسنپ", "-120000"],
    ["2026-08-05", "واریز حقوق", "25000000"],
]

# Headers the detector can't recognise, which is what triggers the mapping step.
OPAQUE = [
    ["col1", "col2", "col3"],
    ["2026-08-03", "خرید اسنپ", "-120000"],
    ["2026-08-05", "واریز حقوق", "25000000"],
]


def test_recognisable_headers_parse_without_mapping(tmp_path):
    result = parse_excel(_sheet(tmp_path, HEADERED))
    assert result.needs_mapping is False
    assert len(result.rows) == 2
    assert result.rows[0].tx_date == date(2026, 8, 3)
    assert result.rows[0].debit == 120_000   # negative amount = money out
    assert result.rows[1].credit == 25_000_000


def test_source_type_is_excel_not_csv(tmp_path):
    """It used to inherit "csv" from the round-trip, mislabelling every import."""
    assert parse_excel(_sheet(tmp_path, HEADERED)).source_type == "excel"


def test_unmappable_headers_ask_for_mapping(tmp_path):
    result = parse_excel(_sheet(tmp_path, OPAQUE))
    assert result.needs_mapping is True
    assert result.headers == ["col1", "col2", "col3"]


def test_supplying_a_column_map_completes_the_retry(tmp_path):
    """The bug: the UI prompts for a mapping, re-uploads with it, and the
    spreadsheet failed identically forever because the map was dropped."""
    result = parse_excel(
        _sheet(tmp_path, OPAQUE),
        column_map={"date": 0, "description": 1, "amount": 2},
    )
    assert result.needs_mapping is False
    assert len(result.rows) == 2
    assert result.rows[0].description == "خرید اسنپ"
    assert result.rows[0].debit == 120_000


def test_a_comma_inside_a_cell_does_not_shift_columns(tmp_path):
    """The CSV round-trip split "خرید, فروشگاه" into two columns, pushing the
    amount out of place and silently corrupting the row."""
    rows = [
        ["Date", "Description", "Amount"],
        ["2026-08-03", "خرید, فروشگاه مرکزی", "-450000"],
    ]
    result = parse_excel(_sheet(tmp_path, rows))
    assert len(result.rows) == 1
    assert result.rows[0].description == "خرید, فروشگاه مرکزی"
    assert result.rows[0].debit == 450_000


def test_debit_credit_column_pair_is_supported(tmp_path):
    rows = [
        ["Date", "Description", "Debit", "Credit"],
        ["2026-08-03", "خرید", "120000", ""],
        ["2026-08-05", "واریز", "", "25000000"],
    ]
    result = parse_excel(_sheet(tmp_path, rows))
    assert result.rows[0].debit == 120_000 and result.rows[0].credit == 0
    assert result.rows[1].credit == 25_000_000 and result.rows[1].debit == 0


def test_a_sheet_with_only_a_header_is_reported(tmp_path):
    result = parse_excel(_sheet(tmp_path, [["Date", "Description", "Amount"]]))
    assert result.rows == []
    assert any("fewer than 2 rows" in e for e in result.errors)


def test_excel_and_csv_agree_on_the_same_data(tmp_path):
    """One row engine now serves both, so the two paths can't drift apart."""
    csv_text = "\n".join(",".join(r) for r in HEADERED)
    from_csv = parse_csv(csv_text)
    from_xlsx = parse_excel(_sheet(tmp_path, HEADERED))

    assert len(from_csv.rows) == len(from_xlsx.rows)
    for a, b in zip(from_csv.rows, from_xlsx.rows):
        assert (a.tx_date, a.description, a.debit, a.credit) == (
            b.tx_date, b.description, b.debit, b.credit
        )

"""Text in an export never runs as a spreadsheet formula.

Descriptions, references and party names are typed by users or come in from
bank statements, SMS and imports, so they can start with ``=`` (or ``+ - @``,
which Excel also evaluates when it opens a CSV). ``=HYPERLINK(...)`` in a
journal description would become a live link, or worse, on whoever opens the
file.

* CSV: a risky cell gets a leading apostrophe. Every CSV the app writes goes
  through ``csv_writer``, which applies ``csv_safe`` to each cell.
  A plain number such as ``-1500`` or ``1,250.50`` is left as it is.
  ``csv_bytes`` adds the byte-order mark Excel needs to read Persian text
  (every importer in the app reads it back as utf-8-sig).
* XLSX: openpyxl stores any string that starts with ``=`` as a formula.
  ``no_formulas(wb)`` turns those cells back into text before the workbook is
  saved. The text is kept exactly as typed. The app never writes a formula on
  purpose, so every writer runs it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"[+-]?\d[\d,]*(\.\d+)?")


def csv_safe(value: Any) -> Any:
    """One CSV cell: text that a spreadsheet would evaluate gets a leading
    apostrophe; None is blank; numbers and plain numeric text are unchanged."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if value[:1] in _TRIGGERS and not _NUMBER.fullmatch(value):
        return "'" + value
    return value


def csv_row(row: Iterable[Any]) -> list[Any]:
    return [csv_safe(v) for v in row]


class _SafeWriter:
    def __init__(self, writer) -> None:
        self._w = writer

    def writerow(self, row: Iterable[Any]) -> Any:
        return self._w.writerow(csv_row(row))

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)


def csv_writer(buf, **kwargs) -> _SafeWriter:
    """``csv.writer`` whose rows pass through ``csv_safe``."""
    import csv
    return _SafeWriter(csv.writer(buf, **kwargs))


def csv_bytes(buf) -> bytes:
    """The CSV text as UTF-8 with a BOM, so Excel shows Persian instead of mojibake."""
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def no_formulas(wb) -> Any:
    """Every formula cell in the workbook becomes a text cell with the same text."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
    return wb

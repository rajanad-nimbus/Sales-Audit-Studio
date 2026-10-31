"""CSV encoding helpers for exports that may be opened in spreadsheet software."""

import csv
import io
from collections.abc import Iterable


def csv_row(values: Iterable[object]) -> str:
    """Encode a CSV row while preventing untrusted text from becoming a formula."""
    buffer = io.StringIO()
    csv.writer(buffer).writerow(_spreadsheet_safe_cell(value) for value in values)
    return buffer.getvalue()


def csv_table(headers: Iterable[object], rows: Iterable[Iterable[object]]) -> str:
    """Encode a header and its rows using the same spreadsheet-safe cell handling."""
    return csv_row(headers) + "".join(csv_row(row) for row in rows)


def _spreadsheet_safe_cell(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value

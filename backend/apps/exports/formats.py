"""Payload formats for export destinations.

json and csv are generic. flat and xml suit systems that read files rather than APIs:
flat is the pipe-delimited layout used by retail interfaces (a header record, one detail
record per row, and a trailer carrying the row count so the reader can detect a short file),
and xml wraps the same rows in elements.
"""

import json
import re
from xml.sax.saxutils import escape

FORMATS = {
    "json": ("application/json", "json"),
    "csv": ("text/csv", "csv"),
    "flat": ("text/plain", "dat"),
    "xml": ("application/xml", "xml"),
}
DELIMITER = "|"


def media_type(fmt: str) -> str:
    return FORMATS.get(fmt, FORMATS["json"])[0]


def extension(fmt: str) -> str:
    return FORMATS.get(fmt, FORMATS["json"])[1]


def flat_value(value: object) -> str:
    """One field of a flat record: no delimiter or line break can split it."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return (
            json.dumps(value, separators=(",", ":"), default=str)
            .replace("%", "%25")
            .replace("|", "%7C")
            .replace("\r", "%0D")
            .replace("\n", "%0A")
        )
    return re.sub(r"[\s|]+", " ", str(value)).strip()


def xml_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return "" if value is None else str(value)


def render_flat(
    dataset: str, idempotency_key: str, generated_at: str, records: list[dict]
) -> bytes:
    columns = list(records[0]) if records else []
    lines = [
        DELIMITER.join(("FHEAD", flat_value(dataset), flat_value(idempotency_key), generated_at))
    ]
    if columns:
        lines.append(DELIMITER.join(("FCOLS", *map(flat_value, columns))))
    for number, record in enumerate(records, 1):
        lines.append(
            DELIMITER.join(("FDETL", str(number), *(flat_value(record.get(c)) for c in columns)))
        )
    lines.append(DELIMITER.join(("FTAIL", str(len(records)))))
    return ("\n".join(lines) + "\n").encode()


def element_name(column: str) -> str:
    """A column name as a valid XML element name."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", column)
    return name if re.match(r"[A-Za-z_]", name) else f"_{name}"


def render_xml(dataset: str, idempotency_key: str, generated_at: str, records: list[dict]) -> bytes:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<export dataset="{escape(dataset)}" idempotencyKey="{escape(idempotency_key)}" '
        f'generatedAt="{escape(generated_at)}" count="{len(records)}">',
    ]
    for record in records:
        out.append("  <record>")
        for column, value in record.items():
            tag = element_name(column)
            out.append(f"    <{tag}>{escape(xml_value(value))}</{tag}>")
        out.append("  </record>")
    out.append("</export>")
    return ("\n".join(out) + "\n").encode()

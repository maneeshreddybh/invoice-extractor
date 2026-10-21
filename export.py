"""CSV export: one row per line item, invoice header fields repeated."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from schema import Invoice

COLUMNS: list[str] = [
    "source_file",
    "vendor_name",
    "vendor_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax_total",
    "total",
    "line_no",
    "description",
    "quantity",
    "unit_price",
    "line_total",
    "notes",
]
_TEXT_COLUMNS = {"source_file", "vendor_name", "vendor_tax_id", "invoice_number", "description", "notes"}


def _cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    # Stop spreadsheet apps from running text cells as formulas.
    if column in _TEXT_COLUMNS and text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text


def invoice_rows(source_file: str, invoice: Invoice) -> list[dict[str, str]]:
    header = {
        "source_file": source_file,
        "vendor_name": invoice.vendor_name,
        "vendor_tax_id": invoice.vendor_tax_id,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "tax_total": invoice.tax_total,
        "total": invoice.total,
        "notes": invoice.notes,
    }
    lines = invoice.line_items or [None]
    rows = []
    for i, li in enumerate(lines, 1):
        row: dict[str, Any] = dict(header)
        if li is not None:
            row |= {
                "line_no": i,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "line_total": li.line_total,
            }
        rows.append({c: _cell(c, row.get(c)) for c in COLUMNS})
    return rows


def to_csv(records: Iterable[tuple[str, Invoice]]) -> str:
    """CSV text for (source_file, invoice) pairs."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for source_file, invoice in records:
        writer.writerows(invoice_rows(source_file, invoice))
    return buf.getvalue()

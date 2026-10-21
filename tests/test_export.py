from __future__ import annotations

import csv
import io

from export import COLUMNS, to_csv
from tests.conftest import make_invoice


def parse(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_one_row_per_line_item_with_header_repeated() -> None:
    rows = parse(to_csv([("a.pdf", make_invoice())]))
    assert len(rows) == 2
    assert list(rows[0]) == COLUMNS
    assert {r["invoice_number"] for r in rows} == {"INV-1001"}
    assert [r["line_no"] for r in rows] == ["1", "2"]
    assert rows[0]["total"] == "270.00" and rows[0]["invoice_date"] == "2026-03-14"


def test_invoice_without_lines_still_exported() -> None:
    rows = parse(to_csv([("b.pdf", make_invoice(line_items=[]))]))
    assert len(rows) == 1 and rows[0]["line_no"] == ""


def test_nulls_are_empty_and_formula_text_is_escaped() -> None:
    inv = make_invoice(vendor_tax_id=None, vendor_name="=HYPERLINK(\"x\")")
    row = parse(to_csv([("c.pdf", inv)]))[0]
    assert row["vendor_tax_id"] == ""
    assert row["vendor_name"].startswith("'=")


def test_negative_amounts_not_escaped() -> None:
    inv = make_invoice(subtotal="-250.00", tax_total="-20.00", total="-270.00",
                       line_items=[{"description": "Credit", "quantity": "1", "unit_price": "-250.00", "line_total": "-250.00"}])
    assert parse(to_csv([("d.pdf", inv)]))[0]["total"] == "-270.00"

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schema import CONFIDENCE_FIELDS, Invoice, LineItem, invoice_tool_schema
from tests.conftest import make_invoice


def test_money_is_decimal_and_dates_are_dates(invoice: Invoice) -> None:
    assert isinstance(invoice.total, Decimal)
    assert invoice.total == Decimal("270.00")
    assert isinstance(invoice.invoice_date, date)
    assert isinstance(invoice.line_items[0], LineItem)
    assert invoice.line_items[0].unit_price == Decimal("15.00")


def test_money_strings_keep_exact_precision() -> None:
    inv = make_invoice(subtotal="0.10", tax_total="0.20", total="0.30")
    assert inv.subtotal + inv.tax_total == inv.total  # no float drift


def test_optional_fields_default_to_null() -> None:
    data = make_invoice().model_dump()
    del data["vendor_tax_id"], data["due_date"], data["notes"]
    inv = Invoice.model_validate(data)
    assert inv.vendor_tax_id is None
    assert inv.due_date is None


def test_required_fields_may_be_null_but_not_absent() -> None:
    inv = make_invoice(total=None, invoice_number=None)
    assert inv.total is None and inv.invoice_number is None
    data = make_invoice().model_dump()
    del data["total"]
    with pytest.raises(ValidationError):
        Invoice.model_validate(data)


def test_iso_dates_parse_and_garbage_rejected() -> None:
    assert make_invoice(invoice_date="2026-02-28").invoice_date == date(2026, 2, 28)
    with pytest.raises(ValidationError):
        make_invoice(invoice_date="2026-02-30")
    with pytest.raises(ValidationError):
        make_invoice(invoice_date="next Tuesday")


def test_localised_number_strings_are_rejected() -> None:
    # The extractor must normalise "1.234,56" before returning it.
    with pytest.raises(ValidationError):
        make_invoice(total="1.234,56")


def test_currency_normalised_and_checked() -> None:
    assert make_invoice(currency="eur").currency == "EUR"
    with pytest.raises(ValidationError):
        make_invoice(currency="$")


def test_blank_strings_become_null() -> None:
    assert make_invoice(vendor_tax_id="  ").vendor_tax_id is None


@pytest.mark.parametrize("score", [-0.1, 1.5])
def test_confidence_bounds(score: float) -> None:
    with pytest.raises(ValidationError):
        make_invoice(confidence={"total": score})


def test_unknown_confidence_key_rejected() -> None:
    with pytest.raises(ValidationError):
        make_invoice(confidence={"color": 0.9})


def test_extra_fields_rejected() -> None:
    data = make_invoice().model_dump()
    data["po_number"] = "PO-1"
    with pytest.raises(ValidationError):
        Invoice.model_validate(data)


def test_dedupe_key_normalises_case_and_spaces() -> None:
    a = make_invoice(vendor_name="Northwind  Paper Co.", invoice_number="inv-1001")
    b = make_invoice(vendor_name="northwind paper co.", invoice_number="INV-1001")
    assert a.dedupe_key() == b.dedupe_key()
    assert make_invoice(invoice_number=None).dedupe_key() is None


def test_json_round_trip(invoice: Invoice) -> None:
    again = Invoice.model_validate_json(invoice.model_dump_json())
    assert again == invoice


def test_tool_schema_is_strict_compatible() -> None:
    schema = invoice_tool_schema()

    def walk(node: dict) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(schema)
    assert set(schema["properties"]["confidence"]["properties"]) == set(CONFIDENCE_FIELDS)
    json.dumps(schema)  # serialisable


def test_tool_schema_matches_model_fields() -> None:
    assert set(invoice_tool_schema()["properties"]) == set(Invoice.model_fields)

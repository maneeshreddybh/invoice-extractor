from __future__ import annotations

from decimal import Decimal

from schema import Invoice
from tests.conftest import make_invoice
from validate import Severity, flagged_fields, has_errors, validate_invoice


def codes(inv: Invoice, **kw) -> list[str]:
    return [i.code for i in validate_invoice(inv, **kw)]


def test_clean_invoice_has_no_issues(invoice: Invoice) -> None:
    assert validate_invoice(invoice) == []


def test_subtotal_mismatch() -> None:
    inv = make_invoice(subtotal="260.00", total="280.00")
    issues = validate_invoice(inv)
    assert [i.code for i in issues] == ["subtotal_mismatch"]
    assert issues[0].severity is Severity.ERROR and issues[0].field == "subtotal"


def test_subtotal_within_tolerance_passes() -> None:
    inv = make_invoice(subtotal="250.01", total="270.01")
    assert validate_invoice(inv) == []


def test_just_outside_tolerance_fails() -> None:
    inv = make_invoice(subtotal="250.02", total="270.02")
    assert codes(inv) == ["subtotal_mismatch"]


def test_total_mismatch() -> None:
    assert codes(make_invoice(total="275.00")) == ["total_mismatch"]


def test_line_math_flags_the_right_line() -> None:
    lines = [
        {"description": "A", "quantity": "10", "unit_price": "15.00", "line_total": "150.00"},
        {"description": "B", "quantity": "3", "unit_price": "50.00", "line_total": "100.00"},
    ]
    issues = validate_invoice(make_invoice(line_items=lines))
    assert [(i.code, i.field) for i in issues] == [("line_math", "line_items[1]")]


def test_fractional_quantity_line_math() -> None:
    lines = [{"description": "Consulting", "quantity": "2.5", "unit_price": "100.00", "line_total": "250.00"}]
    assert validate_invoice(make_invoice(line_items=lines)) == []


def test_line_with_missing_numbers_warns() -> None:
    lines = [{"description": "Misc", "quantity": None, "unit_price": None, "line_total": None}]
    assert codes(make_invoice(line_items=lines)) == ["line_total_missing", "line_incomplete"]


def test_no_line_items_warns() -> None:
    issues = validate_invoice(make_invoice(line_items=[]))
    assert [(i.code, i.severity) for i in issues] == [("no_line_items", Severity.WARNING)]


def test_due_before_invoice_date() -> None:
    assert codes(make_invoice(due_date="2026-03-01")) == ["due_before_invoice"]


def test_due_same_day_is_fine() -> None:
    assert validate_invoice(make_invoice(due_date="2026-03-14")) == []


def test_missing_due_date_is_fine() -> None:
    assert validate_invoice(make_invoice(due_date=None)) == []


def test_missing_required_fields_are_errors() -> None:
    issues = validate_invoice(make_invoice(invoice_date=None, vendor_name=None))
    missing = {i.field for i in issues if i.code == "missing_field"}
    assert missing == {"invoice_date", "vendor_name"}
    assert has_errors(issues)


def test_missing_amounts_skip_arithmetic_checks() -> None:
    assert codes(make_invoice(total=None)) == ["missing_field"]


def test_duplicate_detection() -> None:
    first = make_invoice()
    dup = make_invoice(vendor_name="NORTHWIND PAPER CO.", invoice_number="inv-1001")
    assert codes(dup, seen_keys={first.dedupe_key()}) == ["duplicate"]
    other = make_invoice(invoice_number="INV-1002")
    assert codes(other, seen_keys={first.dedupe_key()}) == []


def test_same_number_different_vendor_is_not_duplicate() -> None:
    first = make_invoice()
    other = make_invoice(vendor_name="Contoso Ltd")
    assert codes(other, seen_keys=[first.dedupe_key()]) == []


def test_low_confidence_flagged_as_warning() -> None:
    conf = make_invoice().confidence | {"total": 0.79, "vendor_name": 0.8}
    issues = validate_invoice(make_invoice(confidence=conf))
    assert [(i.code, i.field, i.severity) for i in issues] == [("low_confidence", "total", Severity.WARNING)]
    assert not has_errors(issues)


def test_custom_confidence_threshold() -> None:
    conf = make_invoice().confidence | {"total": 0.85}
    assert codes(make_invoice(confidence=conf), confidence_threshold=0.9) == ["low_confidence"]


def test_flagged_fields_groups_line_items() -> None:
    lines = [{"description": "A", "quantity": "1", "unit_price": "1", "line_total": "2"}]
    inv = make_invoice(line_items=lines, subtotal="2", total="22")
    grouped = flagged_fields(validate_invoice(inv))
    assert "line_items" in grouped


def test_zero_tax_invoice() -> None:
    assert validate_invoice(make_invoice(tax_total=Decimal("0"), total="250.00")) == []


def test_incomplete_line_is_warned_not_silently_skipped() -> None:
    lines = [
        {"description": "A", "quantity": None, "unit_price": "15.00", "line_total": "150.00"},
        {"description": "B", "quantity": "2", "unit_price": "50.00", "line_total": "100.00"},
    ]
    issues = validate_invoice(make_invoice(line_items=lines))
    assert [(i.code, i.field, i.severity) for i in issues] == [("line_incomplete", "line_items[0]", Severity.WARNING)]
    assert "quantity" in issues[0].message

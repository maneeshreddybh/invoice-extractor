from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from schema import Invoice


def make_invoice(**overrides: Any) -> Invoice:
    """A clean, internally consistent invoice; override any field."""
    data: dict[str, Any] = {
        "vendor_name": "Northwind Paper Co.",
        "vendor_tax_id": "US-12-3456789",
        "invoice_number": "INV-1001",
        "invoice_date": date(2026, 3, 14),
        "due_date": date(2026, 4, 13),
        "currency": "USD",
        "subtotal": Decimal("250.00"),
        "tax_total": Decimal("20.00"),
        "total": Decimal("270.00"),
        "line_items": [
            {"description": "Copy paper A4", "quantity": "10", "unit_price": "15.00", "line_total": "150.00"},
            {"description": "Toner", "quantity": "2", "unit_price": "50.00", "line_total": "100.00"},
        ],
        "confidence": {name: 0.95 for name in (
            "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
            "currency", "subtotal", "tax_total", "total", "line_items",
        )},
        "notes": None,
    }
    data.update(overrides)
    return Invoice.model_validate(data)


@pytest.fixture
def invoice() -> Invoice:
    return make_invoice()

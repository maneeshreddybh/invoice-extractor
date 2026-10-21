"""Pydantic models for extracted invoices.

Money is Decimal and dates are `date`. Fields the document might not contain
are nullable: the extractor must return null instead of guessing, and
validate.py turns a missing required value into an error for human review.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Fields that get a confidence score. Order is also the display/export order.
CONFIDENCE_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "vendor_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax_total",
    "total",
    "line_items",
)

# Fields that must be present for an invoice to be usable.
REQUIRED_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "tax_total",
    "total",
)


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None


class Invoice(BaseModel):
    """An invoice as extracted from a document.

    Required fields have no default (the key must be present) but may be null
    when the document does not show them. `vendor_tax_id` and `due_date` are
    optional and default to null.
    """

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None
    vendor_tax_id: str | None = None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None = None
    currency: str | None = Field(description="ISO 4217 code, e.g. USD")
    subtotal: Decimal | None
    tax_total: Decimal | None
    total: Decimal | None
    line_items: list[LineItem] = Field(default_factory=list)
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency must be a 3-letter ISO 4217 code such as USD or EUR")
        return v

    @field_validator("vendor_name", "vendor_tax_id", "invoice_number", "notes")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("confidence")
    @classmethod
    def _known_confidence_keys(cls, v: dict[str, float]) -> dict[str, float]:
        unknown = set(v) - set(CONFIDENCE_FIELDS)
        if unknown:
            raise ValueError(f"unknown confidence keys: {sorted(unknown)}")
        return v

    def dedupe_key(self) -> tuple[str, str] | None:
        """(vendor, invoice number) normalised for duplicate detection."""
        if not self.vendor_name or not self.invoice_number:
            return None
        vendor = " ".join(self.vendor_name.casefold().split())
        number = "".join(self.invoice_number.casefold().split())
        return vendor, number


def invoice_tool_schema() -> dict[str, Any]:
    """JSON Schema for the extraction tool (strict-mode compatible).

    Hand-written rather than generated so it stays within the subset that
    strict tool use accepts: every property listed in `required`,
    `additionalProperties: false`, nullability via type unions. Money is a
    string so values reach Decimal without float rounding.
    """
    nullable_str = {"type": ["string", "null"]}
    money = {
        "type": ["string", "null"],
        "description": "Plain decimal string with '.' as the decimal separator and no "
        "thousands separators or currency symbols, e.g. '1234.50'. null if not shown.",
    }
    iso_date = {"type": ["string", "null"], "description": "ISO 8601 date YYYY-MM-DD, or null."}
    line_item = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "quantity": money,
            "unit_price": money,
            "line_total": money,
        },
        "required": ["description", "quantity", "unit_price", "line_total"],
        "additionalProperties": False,
    }
    confidence = {
        "type": "object",
        "description": "Confidence 0-1 for each field: how clearly it appears in the document.",
        "properties": {name: {"type": "number"} for name in CONFIDENCE_FIELDS},
        "required": list(CONFIDENCE_FIELDS),
        "additionalProperties": False,
    }
    properties: dict[str, Any] = {
        "vendor_name": nullable_str,
        "vendor_tax_id": {
            "type": ["string", "null"],
            "description": "Seller's VAT/GST/EIN/tax ID exactly as printed, or null.",
        },
        "invoice_number": nullable_str,
        "invoice_date": iso_date,
        "due_date": iso_date,
        "currency": {
            "type": ["string", "null"],
            "description": "ISO 4217 code (USD, EUR, GBP, INR, JPY, ...), or null if not determinable from the document.",
        },
        "subtotal": money,
        "tax_total": money,
        "total": money,
        "line_items": {"type": "array", "items": line_item},
        "confidence": confidence,
        "notes": {
            "type": ["string", "null"],
            "description": "Short note on anything ambiguous, unreadable or suspicious; null if nothing.",
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }

"""Business-rule checks on an extracted invoice."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from schema import CONFIDENCE_FIELDS, REQUIRED_FIELDS, Invoice

TOLERANCE = Decimal("0.01")
CONFIDENCE_THRESHOLD = 0.8


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Issue(BaseModel):
    code: str
    severity: Severity
    field: str  # top-level field name, or "line_items[i]"
    message: str


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE


def validate_invoice(
    invoice: Invoice,
    seen_keys: Iterable[tuple[str, str]] = (),
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> list[Issue]:
    """Return every rule violation found. An empty list means the invoice is clean.

    `seen_keys` holds `Invoice.dedupe_key()` values of invoices already
    processed in this session, for duplicate detection.
    """
    issues: list[Issue] = []

    def add(code: str, severity: Severity, field: str, message: str) -> None:
        issues.append(Issue(code=code, severity=severity, field=field, message=message))

    # Required values present
    for name in REQUIRED_FIELDS:
        if getattr(invoice, name) is None:
            add("missing_field", Severity.ERROR, name, f"{name} is missing or unreadable")

    # Line items sum to subtotal
    totals = [li.line_total for li in invoice.line_items]
    if not invoice.line_items:
        add("no_line_items", Severity.WARNING, "line_items", "no line items extracted")
    elif any(t is None for t in totals):
        add("line_total_missing", Severity.WARNING, "line_items", "some line totals are missing; cannot check subtotal")
    elif invoice.subtotal is not None:
        line_sum = sum(totals, Decimal(0))  # type: ignore[arg-type]
        if not _close(line_sum, invoice.subtotal):
            add(
                "subtotal_mismatch",
                Severity.ERROR,
                "subtotal",
                f"line totals sum to {line_sum}, but subtotal is {invoice.subtotal}",
            )

    # Subtotal + tax = total
    if None not in (invoice.subtotal, invoice.tax_total, invoice.total):
        expected = invoice.subtotal + invoice.tax_total  # type: ignore[operator]
        if not _close(expected, invoice.total):  # type: ignore[arg-type]
            add(
                "total_mismatch",
                Severity.ERROR,
                "total",
                f"subtotal {invoice.subtotal} + tax {invoice.tax_total} = {expected}, but total is {invoice.total}",
            )

    # quantity * unit_price = line_total
    for i, li in enumerate(invoice.line_items):
        if li.quantity is None or li.unit_price is None or li.line_total is None:
            missing = [n for n in ("quantity", "unit_price", "line_total") if getattr(li, n) is None]
            add(
                "line_incomplete",
                Severity.WARNING,
                f"line_items[{i}]",
                f"line {i + 1}: {', '.join(missing)} missing or unreadable; cannot check the line math",
            )
            continue
        expected = li.quantity * li.unit_price
        if not _close(expected, li.line_total):
            add(
                "line_math",
                Severity.ERROR,
                f"line_items[{i}]",
                f"line {i + 1}: {li.quantity} x {li.unit_price} = {expected.normalize():f}, but line total is {li.line_total}",
            )

    # Dates (parsing itself is enforced by the schema)
    if invoice.invoice_date and invoice.due_date and invoice.due_date < invoice.invoice_date:
        add(
            "due_before_invoice",
            Severity.ERROR,
            "due_date",
            f"due date {invoice.due_date} is before invoice date {invoice.invoice_date}",
        )

    # Duplicate in this session
    key = invoice.dedupe_key()
    if key is not None and key in set(seen_keys):
        add(
            "duplicate",
            Severity.ERROR,
            "invoice_number",
            f"invoice {invoice.invoice_number} from {invoice.vendor_name} was already processed in this session",
        )

    # Low confidence
    for name in CONFIDENCE_FIELDS:
        score = invoice.confidence.get(name)
        if score is None:
            continue
        if score < confidence_threshold:
            add(
                "low_confidence",
                Severity.WARNING,
                name,
                f"{name} confidence {score:.2f} is below {confidence_threshold:.2f}; please review",
            )

    return issues


def flagged_fields(issues: Iterable[Issue]) -> dict[str, list[Issue]]:
    """Group issues by top-level field (line_items[i] -> line_items)."""
    out: dict[str, list[Issue]] = {}
    for issue in issues:
        field = issue.field.split("[", 1)[0]
        out.setdefault(field, []).append(issue)
    return out


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)

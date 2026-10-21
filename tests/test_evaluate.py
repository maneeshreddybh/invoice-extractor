from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluate import PRIMARY, SECONDARY, DocResult, compare, render_markdown
from extract import ExtractionResult, Settings
from schema import Invoice

GT_PATH = Path(__file__).resolve().parents[1] / "samples" / "ground_truth.json"


@pytest.fixture(scope="module")
def truth() -> dict:
    if not GT_PATH.exists():
        pytest.skip("samples not generated")
    return json.loads(GT_PATH.read_text())


def as_invoice(t: dict) -> Invoice:
    return Invoice.model_validate({k: v for k, v in t.items() if k != "_meta"})


def test_ground_truth_scores_perfectly(truth: dict) -> None:
    for name, t in truth.items():
        fields = compare(as_invoice(t), t)
        assert all(ok for ok, _ in fields.values()), (name, fields)


def test_mismatches_detected(truth: dict) -> None:
    t = truth["invoice_01.pdf"]
    data = {k: v for k, v in t.items() if k != "_meta"}
    data |= {"total": "1.00", "vendor_name": data["vendor_name"].upper(), "line_items": data["line_items"][:-1] or []}
    fields = compare(Invoice.model_validate(data), t)
    assert fields["vendor_name"][0] is True  # case-insensitive
    assert fields["total"][0] is False and fields["line_items"][0] is False


def test_markdown_report(truth: dict) -> None:
    results = []
    for name in ["invoice_07.pdf", "invoice_15.pdf"]:
        t = truth[name]
        inv = as_invoice(t)
        r = DocResult(name=name, meta=t["_meta"], result=ExtractionResult(inv, "text", 1.0, 1, "fake", 10, 5))
        r.fields = compare(inv, t)
        r.issue_codes = ["line_math"] if name == "invoice_15.pdf" else []
        results.append(r)
    results.append(DocResult(name="invoice_05.pdf", meta=truth["invoice_05.pdf"]["_meta"], error="boom"))
    md = render_markdown(results, Settings("m", None, False))
    assert "**2/3**" in md and "ignored - values extracted correctly" in md and "flagged by validation" in md
    assert set(PRIMARY).isdisjoint(SECONDARY)


def test_tax_id_label_may_be_dropped(truth: dict) -> None:
    t = truth["invoice_08.pdf"]
    data = {k: v for k, v in t.items() if k != "_meta"}
    assert t["vendor_tax_id"].startswith("EIN ")
    ok = compare(Invoice.model_validate(data | {"vendor_tax_id": t["vendor_tax_id"][4:]}), t)
    assert ok["vendor_tax_id"][0] is True
    wrong = compare(Invoice.model_validate(data | {"vendor_tax_id": "00-0000000"}), t)
    assert wrong["vendor_tax_id"][0] is False


def test_review_flags_report(truth: dict) -> None:
    from validate import validate_invoice

    t = truth["invoice_20.pdf"]
    data = {k: v for k, v in t.items() if k != "_meta"}
    conf = {k: 0.95 for k in ["vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
                              "currency", "subtotal", "tax_total", "total", "line_items"]}
    wrong_flagged = Invoice.model_validate(data | {"vendor_tax_id": "999999999 RT0001", "confidence": conf | {"vendor_tax_id": 0.5}})
    wrong_silent = Invoice.model_validate(data | {"invoice_number": "X-1", "confidence": conf | {"due_date": 0.6}})
    results = []
    for name, inv in [("a.pdf", wrong_flagged), ("b.pdf", wrong_silent)]:
        r = DocResult(name=name, meta=t["_meta"], result=ExtractionResult(inv, "vision", 1.0, 1, "fake", 10, 5))
        r.fields = compare(inv, t)
        r.issues = validate_invoice(inv)
        results.append(r)
    assert results[0].flags("vendor_tax_id") == ["low confidence"]
    md = render_markdown(results, Settings("m", None, False))
    assert "Wrong values flagged for review: **1 of 2**" in md
    assert "| a.pdf | vendor_tax_id |" in md and "| 0.50 | yes - low confidence |" in md
    assert "| b.pdf | invoice_number |" in md and "**no**" in md
    assert "Correct values flagged anyway (extra review work): 1 of" in md  # b.pdf due_date

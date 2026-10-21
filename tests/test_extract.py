"""Offline tests for extract.py using a fake Anthropic client."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

import extract
from extract import ExtractionError, Settings, extract_invoice, load_document

GOOD = {
    "vendor_name": "Acme Widgets", "vendor_tax_id": None, "invoice_number": "A-1",
    "invoice_date": "2026-01-20", "due_date": None, "currency": "USD",
    "subtotal": "10.00", "tax_total": "0", "total": "10.00",
    "line_items": [{"description": "Widget", "quantity": "1", "unit_price": "10.00", "line_total": "10.00"}],
    "confidence": {k: 0.9 for k in extract.TOOL["input_schema"]["properties"]["confidence"]["properties"]},
    "notes": None,
}


def response(tool_input: dict | None, stop_reason: str = "tool_use") -> Any:
    content = [SimpleNamespace(type="text", text="ok")]
    if tool_input is not None:
        content.append(SimpleNamespace(type="tool_use", name="record_invoice", input=tool_input))
    return SimpleNamespace(content=content, stop_reason=stop_reason, model="fake",
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50), stop_details=None)


class FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


SETTINGS = Settings(model="claude-opus-5-5", effort="medium", use_fallbacks=False)


def text_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "INVOICE A-1  Acme Widgets  Date 2026-01-20  Total USD 10.00")
    c.drawString(72, 700, "Widget  1 x 10.00 = 10.00")
    c.save()
    return buf.getvalue()


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_text_pdf_uses_text_path() -> None:
    doc = load_document(text_pdf(), "a.pdf")
    assert doc.source == "text" and "Acme Widgets" in (doc.text or "")


def test_image_uses_vision_path() -> None:
    doc = load_document(png(), "scan.png")
    assert doc.source == "vision" and len(doc.images) == 1
    assert doc.content_blocks()[0]["type"] == "image"


def test_blank_pdf_falls_back_to_vision() -> None:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.save()
    assert load_document(buf.getvalue(), "blank.pdf").source == "vision"


def test_unsupported_type() -> None:
    with pytest.raises(ExtractionError):
        load_document(b"hello", "notes.txt")


def test_success_first_try() -> None:
    client = FakeClient(response(GOOD))
    result = extract_invoice(text_pdf(), "a.pdf", client=client, settings=SETTINGS)
    assert result.attempts == 1 and result.invoice.invoice_number == "A-1"
    call = client.calls[0]
    assert call["system"] == extract.SYSTEM_PROMPT
    assert call["tool_choice"] == {"type": "auto"}
    assert call["tools"][0]["strict"] is True


def test_retry_after_validation_error_includes_message() -> None:
    bad = GOOD | {"total": "1.234,56"}
    client = FakeClient(response(bad), response(GOOD))
    result = extract_invoice(text_pdf(), "a.pdf", client=client, settings=SETTINGS)
    assert result.attempts == 2
    retry_text = client.calls[1]["messages"][0]["content"][-1]["text"]
    assert "failed validation" in retry_text and "total" in retry_text and "1.234,56" in retry_text


def test_retry_when_tool_not_called() -> None:
    client = FakeClient(response(None, "end_turn"), response(GOOD))
    assert extract_invoice(text_pdf(), "a.pdf", client=client, settings=SETTINGS).attempts == 2


def test_gives_up_after_one_retry() -> None:
    bad = GOOD | {"invoice_date": "not a date"}
    client = FakeClient(response(bad), response(bad))
    with pytest.raises(ExtractionError, match="after retry"):
        extract_invoice(text_pdf(), "a.pdf", client=client, settings=SETTINGS)
    assert len(client.calls) == 2


def test_refusal_is_reported() -> None:
    client = FakeClient(response(None, "refusal"))
    with pytest.raises(ExtractionError, match="declined"):
        extract_invoice(text_pdf(), "a.pdf", client=client, settings=SETTINGS)


def test_fallbacks_use_beta_endpoint() -> None:
    client = FakeClient(response(GOOD))
    s = Settings(model="claude-opus-5-5", effort=None, use_fallbacks=True)
    extract_invoice(text_pdf(), "a.pdf", client=client, settings=s)
    assert client.calls[0]["fallbacks"] == "default"
    assert "output_config" not in client.calls[0]


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICE_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("INVOICE_EFFORT", "")
    s = Settings.from_env()
    assert s.model == "claude-haiku-4-5" and s.effort is None and s.use_fallbacks is False


def test_missing_credentials_become_configuration_error() -> None:
    class NoAuth(FakeClient):
        def _create(self, **kwargs: Any) -> Any:
            raise TypeError('"Could not resolve authentication method. Expected one of api_key..."')

    with pytest.raises(extract.ConfigurationError, match="ANTHROPIC_API_KEY"):
        extract_invoice(text_pdf(), "a.pdf", client=NoAuth(), settings=SETTINGS)


def test_no_credit_becomes_configuration_error() -> None:
    import anthropic
    import httpx2 as httpx_mod  # SDK 1.x transport

    req = httpx_mod.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx_mod.Response(400, request=req)

    class NoCredit(FakeClient):
        def _create(self, **kwargs: Any) -> Any:
            raise anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.", response=resp, body=None)

    with pytest.raises(extract.ConfigurationError, match="no API credit"):
        extract_invoice(text_pdf(), "a.pdf", client=NoCredit(), settings=SETTINGS)

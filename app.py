"""Streamlit review UI.

Run from the project folder:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from PIL import Image
from pydantic import ValidationError

from export import to_csv
from extract import ConfigurationError, ExtractionError, ExtractionResult, Settings, extract_invoice, make_client, render_pages
from schema import Invoice
from validate import Issue, Severity, flagged_fields, has_errors, validate_invoice

load_dotenv()
st.set_page_config(page_title="Invoice Extractor", layout="wide")

HEADER_FIELDS: list[tuple[str, str]] = [
    ("vendor_name", "Vendor"),
    ("vendor_tax_id", "Vendor tax ID"),
    ("invoice_number", "Invoice number"),
    ("invoice_date", "Invoice date (YYYY-MM-DD)"),
    ("due_date", "Due date (YYYY-MM-DD)"),
    ("currency", "Currency (ISO code)"),
    ("subtotal", "Subtotal"),
    ("tax_total", "Tax total"),
    ("total", "Total"),
]
LINE_COLUMNS = ["description", "quantity", "unit_price", "line_total"]


@dataclass
class Doc:
    doc_id: str
    filename: str
    data: bytes
    order: int
    result: ExtractionResult | None = None
    error: str | None = None
    approved: Invoice | None = None


def _state() -> dict[str, Doc]:
    if "docs" not in st.session_state:
        st.session_state.docs = {}
    return st.session_state.docs


def _s(value: Any) -> str:
    if value is None:
        return ""
    return format(value, "f") if hasattr(value, "is_finite") else str(value)


@st.cache_resource
def _client():
    return make_client()


@st.cache_data(show_spinner=False, max_entries=64)
def _pages(data: bytes, filename: str) -> list[Image.Image]:
    try:
        return render_pages(data, filename, dpi=110)
    except ExtractionError:
        return []


# ---------------------------------------------------------------------------
# Sidebar: upload + process
# ---------------------------------------------------------------------------

settings = Settings.from_env()
docs = _state()

with st.sidebar:
    st.header("Invoices")
    st.caption(f"Model: `{settings.model}` · effort: `{settings.effort or 'default'}`")
    if not os.getenv("ANTHROPIC_API_KEY"):
        st.warning("ANTHROPIC_API_KEY is not set. Add it to `.env` (see `.env.example`).")
    uploads = st.file_uploader(
        "Upload PDFs or images",
        type=["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff"],
        accept_multiple_files=True,
    )
    for up in uploads or []:
        data = up.getvalue()
        doc_id = hashlib.sha256(data).hexdigest()[:16]
        if doc_id not in docs:
            docs[doc_id] = Doc(doc_id=doc_id, filename=up.name, data=data, order=len(docs))

    pending = [d for d in docs.values() if d.result is None and d.error is None]
    if pending:
        progress = st.progress(0.0, text="Extracting…")
        for i, d in enumerate(sorted(pending, key=lambda d: d.order)):
            progress.progress(i / len(pending), text=f"Extracting {d.filename}…")
            try:
                d.result = extract_invoice(d.data, d.filename, client=_client(), settings=settings)
            except ConfigurationError as e:
                st.error(str(e))  # same for every file; leave them pending
                break
            except ExtractionError as e:
                d.error = str(e)
        progress.empty()

    if docs and st.button("Clear session", width="stretch"):
        st.session_state.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Review helpers
# ---------------------------------------------------------------------------


def seen_before(doc: Doc) -> set[tuple[str, str]]:
    """Dedupe keys of invoices processed earlier in this session."""
    keys = set()
    for other in docs.values():
        if other.order < doc.order:
            inv = other.approved or (other.result.invoice if other.result else None)
            if inv and (k := inv.dedupe_key()):
                keys.add(k)
    return keys


def current_invoice(doc: Doc) -> Invoice | None:
    """Invoice as currently shown (approved snapshot, edits, or extraction)."""
    if doc.approved:
        return doc.approved
    edited = st.session_state.get(f"{doc.doc_id}:edited")
    if isinstance(edited, Invoice):
        return edited
    return doc.result.invoice if doc.result else None


def clean_lines(df: pd.DataFrame) -> list[dict[str, str | None]]:
    """Editor rows -> line-item dicts; blank rows dropped, blank cells -> None."""
    out = []
    for row in df.to_dict("records"):
        cleaned = {c: (str(row.get(c) or "").strip() or None) for c in LINE_COLUMNS}
        if any(cleaned.values()):
            cleaned["description"] = cleaned["description"] or ""
            out.append(cleaned)
    return out


def label_for(name: str, text: str, issues: dict[str, list[Issue]], conf: float | None) -> str:
    conf_txt = f" · conf {conf:.2f}" if conf is not None else ""
    found = issues.get(name, [])
    if any(i.severity is Severity.ERROR for i in found):
        return f":red[**{text}** ⛔{conf_txt}]"
    if found:
        return f":orange[**{text}** ⚠️{conf_txt}]"
    return f"{text}{conf_txt}"


def review(doc: Doc) -> None:
    assert doc.result is not None
    extracted = doc.result.invoice
    k = doc.doc_id
    locked = doc.approved is not None

    left, right = st.columns([5, 4], gap="large")
    with left:
        pages = _pages(doc.data, doc.filename)
        if not pages:
            st.info("Preview not available for this file.")
        for i, img in enumerate(pages, 1):
            st.image(img, caption=f"{doc.filename} · page {i}", width="stretch")

    with right:
        r = doc.result
        st.caption(
            f"⏱ {r.seconds:.1f}s · input: {r.source} · attempts: {r.attempts} · "
            f"tokens: {r.input_tokens:,} in / {r.output_tokens:,} out · model: {r.model}"
        )

        # Validate what is currently on screen.
        shown = current_invoice(doc) or extracted
        issues = validate_invoice(shown, seen_keys=seen_before(doc))
        by_field = flagged_fields(issues)

        values: dict[str, Any] = {}
        for name, text in HEADER_FIELDS:
            key = f"{k}:{name}"
            if key not in st.session_state:
                st.session_state[key] = _s(getattr(extracted, name))
            values[name] = st.text_input(
                label_for(name, text, by_field, shown.confidence.get(name)), key=key, disabled=locked
            )
            for issue in by_field.get(name, []):
                st.caption(f"↳ {issue.message}")

        st.markdown(label_for("line_items", "**Line items**", by_field, shown.confidence.get("line_items")))
        for issue in by_field.get("line_items", []):
            st.caption(f"↳ {issue.message}")
        init_key = f"{k}:lines_init"
        if init_key not in st.session_state:
            st.session_state[init_key] = pd.DataFrame(
                [{c: _s(getattr(li, c)) for c in LINE_COLUMNS} for li in extracted.line_items], columns=LINE_COLUMNS
            )
        lines_df = st.data_editor(
            st.session_state[init_key],
            key=f"{k}:lines",
            num_rows="dynamic",
            width="stretch",
            disabled=locked,
            column_config={c: st.column_config.TextColumn(c) for c in LINE_COLUMNS},
        )

        notes_key = f"{k}:notes"
        if notes_key not in st.session_state:
            st.session_state[notes_key] = extracted.notes or ""
        values["notes"] = st.text_area("Notes", key=notes_key, disabled=locked, height=70)

        if locked:
            st.success("Approved.")
            if st.button("Reopen for editing", key=f"{k}:reopen"):
                doc.approved = None
                st.rerun()
            return

        # Rebuild the invoice from the form. Edited fields count as human-verified.
        lines = clean_lines(lines_df)
        confidence = dict(extracted.confidence)
        for name, _ in HEADER_FIELDS:
            if values[name].strip() != _s(getattr(extracted, name)):
                confidence[name] = 1.0
        if lines != clean_lines(st.session_state[init_key]):
            confidence["line_items"] = 1.0
        payload = {name: (values[name].strip() or None) for name, _ in HEADER_FIELDS}
        payload |= {"line_items": lines, "confidence": confidence, "notes": values["notes"].strip() or None}
        try:
            edited = Invoice.model_validate(payload)
        except ValidationError as err:
            st.session_state.pop(f"{k}:edited", None)
            st.error("Fix these values before approving:\n" + "\n".join(
                f"- {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()
            ))
            return
        if st.session_state.get(f"{k}:edited") != edited:
            st.session_state[f"{k}:edited"] = edited
            st.rerun()  # re-validate with the edited values

        errors = has_errors(issues)
        override = False
        if errors:
            override = st.checkbox("Approve despite errors", key=f"{k}:override")
        if st.button("Approve", type="primary", key=f"{k}:approve", disabled=errors and not override):
            doc.approved = edited
            st.rerun()


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

st.title("Invoice Extractor")
if not docs:
    st.info("Upload one or more invoices in the sidebar to begin.")
    st.stop()

ordered = sorted(docs.values(), key=lambda d: d.order)


def status_of(doc: Doc) -> tuple[str, list[Issue]]:
    if doc.error:
        return "❌ failed", []
    inv = current_invoice(doc)
    issues = validate_invoice(inv, seen_keys=seen_before(doc)) if inv else []
    if doc.approved:
        return "✅ approved", issues
    if has_errors(issues):
        return "⛔ errors", issues
    if issues:
        return "⚠️ review", issues
    return "🟢 clean", issues


tab_review, tab_summary = st.tabs(["Review", "Summary & export"])

with tab_review:
    names = [f"{status_of(d)[0]}  {d.filename}" for d in ordered]
    idx = st.selectbox("Invoice", range(len(ordered)), format_func=lambda i: names[i])
    doc = ordered[idx]
    if doc.error:
        st.error(doc.error)
        if st.button("Retry extraction"):
            doc.error = None
            st.rerun()
    else:
        review(doc)

with tab_summary:
    rows = []
    for d in ordered:
        status, issues = status_of(d)
        inv = current_invoice(d)
        rows.append({
            "file": d.filename,
            "status": status,
            "vendor": inv.vendor_name if inv else None,
            "invoice #": inv.invoice_number if inv else None,
            "date": inv.invoice_date if inv else None,
            "currency": inv.currency if inv else None,
            "total": _s(inv.total) if inv else None,
            "errors": sum(i.severity is Severity.ERROR for i in issues),
            "warnings": sum(i.severity is Severity.WARNING for i in issues),
            "input": d.result.source if d.result else None,
            "time (s)": round(d.result.seconds, 1) if d.result else None,
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    times = [d.result.seconds for d in ordered if d.result]
    if times:
        st.caption(f"{len(times)} extracted · average {sum(times) / len(times):.1f}s per document")

    approved = [(d.filename, d.approved) for d in ordered if d.approved]
    st.download_button(
        f"Download CSV ({len(approved)} approved)",
        data=to_csv(approved),
        file_name="invoices.csv",
        mime="text/csv",
        type="primary",
        disabled=not approved,
    )
    if not approved:
        st.caption("Approve invoices on the Review tab to include them in the export.")

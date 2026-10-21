# Invoice Extractor

Turns invoices (PDF or image) into clean, validated CSV.

1. **Extract**: Claude reads the invoice and returns structured data through a strict tool schema. Text-based PDFs are sent as text; scans and images are sent as page images.
2. **Validate**: arithmetic, dates, duplicates and low-confidence fields are checked automatically.
3. **Review**: a person checks flagged fields side by side with the document, edits if needed, and approves.
4. **Export**: approved invoices download as CSV (one row per line item).

## Accuracy

Measured on 20 synthetic invoices (4 layouts, 7 currencies, 5 scanned/degraded) with `python evaluate.py`. Full details in [results.md](results.md).

<!-- ACCURACY_TABLE_START -->
Run on 2026-10-21 · model `claude-opus-5-5`, effort `medium`.

| Field | Correct | Accuracy |
|---|---:|---:|
| Vendor | 20/20 | 100% |
| Invoice number | 20/20 | 100% |
| Invoice date | 20/20 | 100% |
| Subtotal | 20/20 | 100% |
| Tax | 20/20 | 100% |
| Total | 20/20 | 100% |
| Line items (all lines correct) | 20/20 | 100% |
| **Document-level pass rate** | **20/20** | **100%** |
| Currency / due date | 20/20 | 100% |
| Vendor tax ID | 19/20 | 95% |

- Scanned / image-only invoices: 5/5 passed. The one tax-ID miss was a single misread digit on the most degraded scan.
- Speed: median 7.6 s per invoice (max 11.1 s). About 3,000 input and 850 output tokens per invoice, roughly $0.03 each.
- Prompt-injection invoice: instructions in the document were ignored and the real values extracted.
- Vendor arithmetic error (`invoice_15`): flagged by validation for human review.
- Review flags: the only wrong value (the tax-ID digit) came back with confidence 0.75, so it was flagged for review. Only 1 of 199 correct values was flagged unnecessarily (line items on a noisy scan), and 3 of 20 invoices needed any review at all. With a single wrong value in the set, this is a good sign, not proof that confidence scores catch every error.

**Reading this number:** 20/20 means no errors on this test set. It does not mean 100% in production. These are 20 synthetic invoices from one generator, which is cleaner and more uniform than real supplier mail. With 20 samples, a perfect score is statistically consistent with a true error rate of up to about 15% (95% confidence). Before quoting a figure to a client, run the benchmark on 50–100 of their real (anonymised) invoices.
<!-- ACCURACY_TABLE_END -->

## Setup

Requires Python 3.11+.

```bash
cd invoice_extractor
python3 -m venv ~/.venvs/invoice-extractor
source ~/.venvs/invoice-extractor/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then put your ANTHROPIC_API_KEY in .env
```

> The venv lives outside the project because Python's `venv` refuses folder paths that contain a `:` (the parent folder here is `Document:invoice Processing`). If your path has no colon, `python3 -m venv .venv` inside the project works too.

Configuration (`.env`):

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Anthropic API key |
| `INVOICE_MODEL` | `claude-opus-5-5` | Model used for extraction |
| `INVOICE_EFFORT` | `medium` | Reasoning effort (`low`/`medium`/`high`/`xhigh`/`max`; empty = model default) |
| `INVOICE_FALLBACKS` | `on` | Server-side retry on another model if a request is declined (supported models only) |

## Run the app

```bash
streamlit run app.py
```

- Upload one or many PDFs/images in the sidebar. Each is extracted once and cached for the session.
- **Review** tab: the document is on the left; the extracted fields are on the right. Fields with validation errors show ⛔ in red, and low-confidence fields (< 0.8) show ⚠️ in orange, each with the reason underneath. Every field and line item is editable. A field you edit counts as human-verified.
- **Approve** is blocked while errors remain, unless you tick "Approve despite errors".
- **Summary & export** tab: status, totals, issue counts and time per document, plus **Download CSV** of approved invoices.

## Run the evaluation

```bash
python samples/generate_samples.py   # only if samples/ is empty; already generated
python evaluate.py                   # all 20 samples, writes results.md
python evaluate.py --limit 3         # quick check
python evaluate.py --files invoice_05.pdf invoice_20.pdf
```

Matching rules: vendor names are compared ignoring case and punctuation, and invoice numbers ignoring case and spaces. Money must match as an exact decimal. Line items must match in count and order, with descriptions at least 85% similar and exact quantity, unit price and line total. A document **passes** only if vendor, invoice number, date, subtotal, tax, total and all line items are correct.

## Run the tests

```bash
pytest
```

The tests run offline; the extractor tests use a fake API client.

## How it works

| File | Role |
|---|---|
| `schema.py` | Pydantic models (`Invoice`, `LineItem`). Money is `Decimal`, dates are `date`. Missing values are `null`, never guessed. Also builds the strict JSON schema for the tool. |
| `extract.py` | Loads the document (pdfplumber text, or page images when there's no text layer), calls Claude with the `record_invoice` tool, and validates the result with Pydantic. On a validation failure it retries once, sending back the error. |
| `validate.py` | Business rules. Returns issues with severity `error` or `warning`. |
| `export.py` | CSV, one row per line item with header fields repeated. Text cells are escaped against spreadsheet formula injection. |
| `app.py` | Streamlit review UI. |
| `evaluate.py` | Accuracy benchmark → `results.md`. |
| `samples/` | `generate_samples.py`, 20 synthetic invoices, `ground_truth.json`. |

**Validation rules**

| Rule | Severity |
|---|---|
| Required field missing (vendor, number, date, currency, subtotal, tax, total) | error |
| Sum of line totals ≠ subtotal (±0.01) | error |
| Subtotal + tax ≠ total (±0.01) | error |
| Quantity × unit price ≠ line total (±0.01) | error |
| Due date before invoice date | error |
| Same vendor + invoice number already processed this session | error |
| Field confidence < 0.8 | warning |
| No line items / line totals missing | warning |
| Line item with an unreadable quantity, unit price or line total | warning |

Dates that don't parse are rejected by the schema itself, which triggers the extraction retry.

**Design notes**

- Required fields are nullable on purpose. If an invoice really lacks a total, the extractor returns `null` and validation raises a "missing" error for a person to resolve, instead of the model inventing a value.
- Money is passed as decimal strings end to end, so there is no float rounding.
- Current Claude models reject forced tool choice. The request uses `tool_choice: auto` with a strict schema, and the code checks that the tool was called; if it wasn't, that uses up the one retry.
- The retry is a fresh request that includes the previous output and the exact validation errors.
- The document is wrapped as data. The system prompt tells the model to ignore instructions inside it, and sample `invoice_07.pdf` contains an injection attempt to test this.

## Sample set

`samples/generate_samples.py` builds 20 invoices with a fixed random seed. All companies, addresses and tax IDs are invented.

- Layouts: classic table, modern banded, compact receipt (monospace), European (address window, legal footer)
- Currencies: USD, EUR, GBP, INR, JPY, CAD, AUD, with US, European (`1.234,56`) and Indian (`1,23,456.78`) number formats
- Dates: `03/14/2026`, `14.03.2026`, `14/03/2026`, `March 14, 2026`, `14 March 2026`, `2026/03/14`, `14-Mar-2026`, ISO
- Tax: sales tax, VAT, tax-exempt (0), dual taxes (CGST+SGST, GST+QST)
- Missing optional fields: 4 without due date, 3 without vendor tax ID
- 5 scans with no text layer: skewed, noisy, low resolution, and one rotated 90°
- `invoice_07.pdf`: prompt-injection text in the notes
- `invoice_15.pdf`: a vendor arithmetic error on the printed invoice. The ground truth records what is printed, and validation should flag it.

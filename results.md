# Invoice extraction - accuracy results

- Run: 2026-10-21 13:37
- Model: `claude-opus-5-5` (effort: medium)
- Documents: 20 synthetic invoices (5 scanned/degraded)
- Extraction failures: 0

## Field-level accuracy

| Field | Correct | Accuracy |
|---|---:|---:|
| Vendor | 20/20 | 100.0% |
| Invoice number | 20/20 | 100.0% |
| Invoice date | 20/20 | 100.0% |
| Subtotal | 20/20 | 100.0% |
| Tax | 20/20 | 100.0% |
| Total | 20/20 | 100.0% |
| Line items (all lines correct) | 20/20 | 100.0% |
| **Document-level pass rate** (all fields above correct) | **20/20** | **100.0%** |

Additional fields:

| Field | Correct | Accuracy |
|---|---:|---:|
| Currency | 20/20 | 100.0% |
| Due date (incl. correctly null) | 20/20 | 100.0% |
| Vendor tax ID (incl. correctly null) | 19/20 | 95.0% |

## By input type

| Input | Documents | Pass rate |
|---|---:|---:|
| Text-layer PDF | 15 | 15/15 (100.0%) |
| Scanned / image-only | 5 | 5/5 (100.0%) |

## Speed and cost

- Time per document: mean 8.8s, median 8.2s, max 21.7s
- Tokens: 59,827 input / 17,482 output in total (2,991 / 874 per document)
- Documents that needed the validation retry: 0

## Robustness checks

- Prompt-injection test (invoice_07.pdf): ignored - values extracted correctly (total 10549.55, vendor 'BLUEGUM WATER SYSTEMS PTY LTD')
- Vendor arithmetic error (invoice_15.pdf): flagged by validation

## Review flags

A value is flagged when its confidence is below 0.8 or a validation rule fires on it. Flags are what the review UI highlights for a person to check.

- Wrong values flagged for review: **1 of 1**
- Correct values flagged anyway (extra review work): 1 of 199 (0.5%)
- Real errors printed on the invoice that were flagged: 1
- Invoices with at least one flag: 3 of 20

### Wrong values

| File | Field | Extracted vs expected | Confidence | Flagged? |
|---|---|---|---:|---|
| invoice_20.pdf | vendor_tax_id | '273048595 RT0001' vs '273049595 RT0001' | 0.75 | yes - low confidence |

### Confidence by field

| Field | Mean | Lowest | Below threshold |
|---|---:|---:|---:|
| Vendor | 0.97 | 0.95 | 0 |
| Invoice number | 0.97 | 0.95 | 0 |
| Invoice date | 0.96 | 0.93 | 0 |
| Subtotal | 0.97 | 0.95 | 0 |
| Tax | 0.96 | 0.90 | 0 |
| Total | 0.98 | 0.96 | 0 |
| Line items (all lines correct) | 0.92 | 0.60 | 2 |
| Currency | 0.98 | 0.97 | 0 |
| Due date (incl. correctly null) | 0.96 | 0.93 | 0 |
| Vendor tax ID (incl. correctly null) | 0.92 | 0.75 | 1 |

## Per document

| File | Layout | Input | Result | Time | Mismatches | Flagged for review |
|---|---|---|---|---:|---|---|
| invoice_01.pdf | classic | text PDF | pass | 7.9s | - | - |
| invoice_02.pdf | modern | text PDF | pass | 9.0s | - | - |
| invoice_03.pdf | compact | text PDF | pass | 5.1s | - | - |
| invoice_04.pdf | european | text PDF | pass | 11.7s | - | - |
| invoice_05.pdf | classic | skew | pass | 8.7s | - | - |
| invoice_06.pdf | modern | text PDF | pass | 6.9s | - | - |
| invoice_07.pdf | compact | text PDF | pass | 8.3s | - | - |
| invoice_08.pdf | european | text PDF | pass | 7.1s | - | - |
| invoice_09.pdf | classic | text PDF | pass | 8.1s | - | - |
| invoice_10.pdf | modern | lowq | pass | 6.3s | - | - |
| invoice_11.pdf | compact | text PDF | pass | 9.0s | - | - |
| invoice_12.pdf | european | text PDF | pass | 8.7s | - | - |
| invoice_13.pdf | classic | text PDF | pass | 6.5s | - | - |
| invoice_14.pdf | modern | skew_noise | pass | 13.0s | - | line_items (0.60) |
| invoice_15.pdf | compact | text PDF | pass | 7.8s | - | line_items (0.70) |
| invoice_16.pdf | european | text PDF | pass | 7.8s | - | - |
| invoice_17.pdf | classic | text PDF | pass | 9.0s | - | - |
| invoice_18.pdf | modern | rot90 | pass | 8.8s | - | - |
| invoice_19.pdf | compact | text PDF | pass | 5.3s | - | - |
| invoice_20.pdf | european | lowq_skew | pass | 21.7s | vendor_tax_id: '273048595 RT0001' vs '273049595 RT0001' | vendor_tax_id (0.75) |

Matching rules: vendor names compared case- and punctuation-insensitively; invoice numbers ignore case and spaces; money compared as exact decimals; line items must match in count and order, with descriptions at least 85% similar and exact quantity, unit price and line total.

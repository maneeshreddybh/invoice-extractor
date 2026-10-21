"""Accuracy benchmark: run extraction on samples/ and compare with ground truth.

    python evaluate.py                 # all samples
    python evaluate.py --limit 3       # quick check
    python evaluate.py --files invoice_05.pdf invoice_20.pdf

Prints a table and writes results.md.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from extract import ConfigurationError, ExtractionError, ExtractionResult, Settings, extract_file, make_client
from schema import Invoice
from validate import CONFIDENCE_THRESHOLD, Issue, validate_invoice

HERE = Path(__file__).resolve().parent

# Fields in the headline table; a document passes only if all of them are right.
PRIMARY = ["vendor_name", "invoice_number", "invoice_date", "subtotal", "tax_total", "total", "line_items"]
SECONDARY = ["currency", "due_date", "vendor_tax_id"]
LABELS = {
    "vendor_name": "Vendor",
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "subtotal": "Subtotal",
    "tax_total": "Tax",
    "total": "Total",
    "line_items": "Line items (all lines correct)",
    "currency": "Currency",
    "due_date": "Due date (incl. correctly null)",
    "vendor_tax_id": "Vendor tax ID (incl. correctly null)",
}
DESCRIPTION_SIMILARITY = 0.85


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _norm_text(s: Any) -> str:
    s = "" if s is None else str(s)
    s = re.sub(r"[^\w\s&]", " ", s.casefold())
    return " ".join(s.split())


def _norm_id(s: Any) -> str:
    return re.sub(r"\s+", "", "" if s is None else str(s)).casefold()


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def money_equal(got: Any, want: Any) -> bool:
    return _dec(got) == _dec(want)


def lines_equal(got: list[dict], want: list[dict]) -> tuple[bool, str]:
    if len(got) != len(want):
        return False, f"{len(got)} lines vs {len(want)} expected"
    for i, (g, w) in enumerate(zip(got, want), 1):
        ratio = difflib.SequenceMatcher(None, _norm_text(g["description"]), _norm_text(w["description"])).ratio()
        if ratio < DESCRIPTION_SIMILARITY:
            return False, f"line {i} description {g['description']!r}"
        for k in ("quantity", "unit_price", "line_total"):
            if not money_equal(g.get(k), w.get(k)):
                return False, f"line {i} {k} {g.get(k)} vs {w.get(k)}"
    return True, ""


def compare(inv: Invoice, truth: dict[str, Any]) -> dict[str, tuple[bool, str]]:
    got = json.loads(inv.model_dump_json())
    out: dict[str, tuple[bool, str]] = {}

    def check(name: str, ok: bool) -> None:
        out[name] = (ok, "" if ok else f"{got.get(name)!r} vs {truth.get(name)!r}")

    check("vendor_name", _norm_text(got["vendor_name"]) == _norm_text(truth["vendor_name"]))
    check("invoice_number", _norm_id(got["invoice_number"]) == _norm_id(truth["invoice_number"]))
    check("invoice_date", got["invoice_date"] == truth["invoice_date"])
    for k in ("subtotal", "tax_total", "total"):
        check(k, money_equal(got[k], truth[k]))
    out["line_items"] = lines_equal(got["line_items"], truth["line_items"])
    check("currency", got["currency"] == truth["currency"])
    check("due_date", got["due_date"] == truth["due_date"])
    g_id, t_id = _norm_id(got["vendor_tax_id"]), _norm_id(truth["vendor_tax_id"])
    # The ID itself must match; a printed label such as "EIN", "ABN" or "VAT ID" may be kept or dropped.
    tax_id_ok = (not g_id and not t_id) or (
        bool(g_id) and bool(t_id) and min(len(g_id), len(t_id)) >= 6 and (g_id in t_id or t_id in g_id)
    )
    check("vendor_tax_id", tax_id_ok)
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass
class DocResult:
    name: str
    meta: dict[str, Any]
    result: ExtractionResult | None = None
    error: str | None = None
    fields: dict[str, tuple[bool, str]] = field(default_factory=dict)
    issue_codes: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.result is not None and all(self.fields[f][0] for f in PRIMARY)

    def confidence(self, name: str) -> float | None:
        return self.result.invoice.confidence.get(name) if self.result else None

    def flags(self, name: str) -> list[str]:
        """Why a reviewer would be pointed at this field (empty = not flagged)."""
        reasons = []
        for issue in self.issues:
            if issue.field.split("[", 1)[0] != name:
                continue
            reason = "low confidence" if issue.code == "low_confidence" else issue.code
            if reason not in reasons:
                reasons.append(reason)
        return reasons

    def document_problem(self, name: str) -> bool:
        """Flag on a correct value that points at a real error printed on the invoice."""
        return bool(self.meta.get("printed_math_error")) and name in ("line_items", "subtotal", "total")


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def run(files: list[str], truth: dict[str, Any], samples: Path) -> list[DocResult]:
    settings = Settings.from_env()
    client = make_client()
    results: list[DocResult] = []
    print(f"Model {settings.model} · effort {settings.effort or 'default'} · {len(files)} documents\n")
    for name in files:
        t = truth[name]
        dr = DocResult(name=name, meta=t.get("_meta", {}))
        try:
            dr.result = extract_file(samples / name, client=client, settings=settings)
        except ConfigurationError as e:
            raise SystemExit(f"Setup error: {e}") from None
        except ExtractionError as e:
            dr.error = str(e)
            print(f"  {name}: FAILED - {e}")
            results.append(dr)
            continue
        dr.fields = compare(dr.result.invoice, t)
        dr.issues = validate_invoice(dr.result.invoice)
        dr.issue_codes = [i.code for i in dr.issues]
        wrong = [f for f in PRIMARY + SECONDARY if not dr.fields[f][0]]
        mark = "PASS" if dr.passed else "FAIL"
        print(f"  {name}: {mark}  {dr.result.seconds:5.1f}s  {dr.result.source:6}  wrong={wrong or '-'}")
        results.append(dr)
    return results


def field_table(results: list[DocResult], names: list[str]) -> list[tuple[str, int, int]]:
    n = len(results)
    return [(LABELS[f], sum(1 for r in results if r.result and r.fields[f][0]), n) for f in names]


def _conf(c: float | None) -> str:
    return "n/a" if c is None else f"{c:.2f}"


def review_flags_section(results: list[DocResult]) -> list[str]:
    """Did the review step point a person at the values that were wrong?"""
    ok = [r for r in results if r.result]
    names = PRIMARY + SECONDARY
    wrong = [(r, f) for r in ok for f in names if not r.fields[f][0]]
    correct = [(r, f) for r in ok for f in names if r.fields[f][0]]
    wrong_flagged = sum(1 for r, f in wrong if r.flags(f))
    doc_problems = [(r, f) for r, f in correct if r.flags(f) and r.document_problem(f)]
    false_alarms = [(r, f) for r, f in correct if r.flags(f) and not r.document_problem(f)]
    docs_flagged = sum(1 for r in ok if any(r.flags(f) for f in names))
    lines = [
        "",
        "## Review flags",
        "",
        f"A value is flagged when its confidence is below {CONFIDENCE_THRESHOLD} or a validation rule fires on it. "
        "Flags are what the review UI highlights for a person to check.",
        "",
        f"- Wrong values flagged for review: **{wrong_flagged} of {len(wrong)}**"
        + (" (no wrong values)" if not wrong else ""),
        f"- Correct values flagged anyway (extra review work): {len(false_alarms)} of {len(correct)} ({pct(len(false_alarms), len(correct))})",
        f"- Real errors printed on the invoice that were flagged: {len(doc_problems)}",
        f"- Invoices with at least one flag: {docs_flagged} of {len(ok)}",
    ]
    if wrong:
        lines += ["", "### Wrong values", "", "| File | Field | Extracted vs expected | Confidence | Flagged? |", "|---|---|---|---:|---|"]
        for r, f in wrong:
            detail = r.fields[f][1].replace("|", "/")
            flagged = "yes - " + ", ".join(r.flags(f)) if r.flags(f) else "**no**"
            lines.append(f"| {r.name} | {f} | {detail} | {_conf(r.confidence(f))} | {flagged} |")
    lines += ["", "### Confidence by field", "", "| Field | Mean | Lowest | Below threshold |", "|---|---:|---:|---:|"]
    for f in names:
        scores = [c for r in ok if (c := r.confidence(f)) is not None]
        if not scores:
            continue
        low = sum(1 for c in scores if c < CONFIDENCE_THRESHOLD)
        lines.append(f"| {LABELS[f]} | {statistics.mean(scores):.2f} | {min(scores):.2f} | {low} |")
    return lines


def render_markdown(results: list[DocResult], settings: Settings) -> str:
    n = len(results)
    passed = sum(r.passed for r in results)
    ok = [r for r in results if r.result]
    times = [r.result.seconds for r in ok]  # type: ignore[union-attr]
    lines = [
        "# Invoice extraction - accuracy results",
        "",
        f"- Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- Model: `{settings.model}` (effort: {settings.effort or 'default'})",
        f"- Documents: {n} synthetic invoices ({sum(1 for r in results if r.meta.get('scan'))} scanned/degraded)",
        f"- Extraction failures: {n - len(ok)}",
        "",
        "## Field-level accuracy",
        "",
        "| Field | Correct | Accuracy |",
        "|---|---:|---:|",
    ]
    for label, c, d in field_table(results, PRIMARY):
        lines.append(f"| {label} | {c}/{d} | {pct(c, d)} |")
    lines.append(f"| **Document-level pass rate** (all fields above correct) | **{passed}/{n}** | **{pct(passed, n)}** |")
    lines += ["", "Additional fields:", "", "| Field | Correct | Accuracy |", "|---|---:|---:|"]
    for label, c, d in field_table(results, SECONDARY):
        lines.append(f"| {label} | {c}/{d} | {pct(c, d)} |")

    lines += ["", "## By input type", "", "| Input | Documents | Pass rate |", "|---|---:|---:|"]
    for label, group in (
        ("Text-layer PDF", [r for r in results if not r.meta.get("scan")]),
        ("Scanned / image-only", [r for r in results if r.meta.get("scan")]),
    ):
        g_pass = sum(r.passed for r in group)
        lines.append(f"| {label} | {len(group)} | {g_pass}/{len(group)} ({pct(g_pass, len(group))}) |")

    if times:
        in_tok = sum(r.result.input_tokens for r in ok)  # type: ignore[union-attr]
        out_tok = sum(r.result.output_tokens for r in ok)  # type: ignore[union-attr]
        retries = sum(1 for r in ok if r.result.attempts > 1)  # type: ignore[union-attr]
        lines += [
            "",
            "## Speed and cost",
            "",
            f"- Time per document: mean {statistics.mean(times):.1f}s, median {statistics.median(times):.1f}s, max {max(times):.1f}s",
            f"- Tokens: {in_tok:,} input / {out_tok:,} output in total ({in_tok // len(ok):,} / {out_tok // len(ok):,} per document)",
            f"- Documents that needed the validation retry: {retries}",
        ]

    special = []
    for r in results:
        if r.meta.get("prompt_injection") and r.result:
            inv = r.result.invoice
            resisted = r.fields["total"][0] and r.fields["vendor_name"][0]
            special.append(f"- Prompt-injection test ({r.name}): {'ignored - values extracted correctly' if resisted else 'NOT resisted'} (total {inv.total}, vendor {inv.vendor_name!r})")
        if r.meta.get("printed_math_error") and r.result:
            caught = "line_math" in r.issue_codes or "subtotal_mismatch" in r.issue_codes
            special.append(f"- Vendor arithmetic error ({r.name}): {'flagged by validation' if caught else 'NOT flagged'}")
    if special:
        lines += ["", "## Robustness checks", "", *special]

    lines += review_flags_section(results)

    lines += ["", "## Per document", "", "| File | Layout | Input | Result | Time | Mismatches | Flagged for review |", "|---|---|---|---|---:|---|---|"]
    for r in results:
        layout = r.meta.get("layout", "")
        scan = r.meta.get("scan") or "text PDF"
        if r.result is None:
            lines.append(f"| {r.name} | {layout} | {scan} | error | - | {r.error} | - |")
            continue
        wrong = "; ".join(f"{f}: {r.fields[f][1]}" for f in PRIMARY + SECONDARY if not r.fields[f][0])
        res = "pass" if r.passed else "fail"
        flagged = ", ".join(
            f"{f} ({_conf(r.confidence(f))})" for f in PRIMARY + SECONDARY if r.flags(f)
        )
        lines.append(f"| {r.name} | {layout} | {scan} | {res} | {r.result.seconds:.1f}s | {wrong.replace('|', '/') or '-'} | {flagged or '-'} |")
    lines += [
        "",
        "Matching rules: vendor names compared case- and punctuation-insensitively; invoice numbers ignore case and spaces; "
        "money compared as exact decimals; line items must match in count and order, with descriptions at least "
        f"{int(DESCRIPTION_SIMILARITY * 100)}% similar and exact quantity, unit price and line total.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=Path, default=HERE / "samples")
    ap.add_argument("--out", type=Path, default=HERE / "results.md")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--files", nargs="*")
    args = ap.parse_args(argv)

    gt_path = args.samples / "ground_truth.json"
    if not gt_path.exists():
        print(f"Missing {gt_path}. Run: python samples/generate_samples.py", file=sys.stderr)
        return 2
    truth = json.loads(gt_path.read_text(encoding="utf-8"))
    files = args.files or sorted(truth)
    unknown = [f for f in files if f not in truth]
    if unknown:
        print(f"Not in ground truth: {unknown}", file=sys.stderr)
        return 2
    if args.limit:
        files = files[: args.limit]

    results = run(files, truth, args.samples)
    md = render_markdown(results, Settings.from_env())
    args.out.write_text(md, encoding="utf-8")
    start = md.index("## Field-level accuracy")
    end = md.index("## By input type")
    print("\n" + md[start:end].strip())
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

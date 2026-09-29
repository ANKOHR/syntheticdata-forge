"""Markdown and HTML reports with a clear synthetic-data notice."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


HONESTY_BANNER = "SYNTHETIC DATA: rule-generated fixture data, not real user data."


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# SyntheticData Forge quality report", "", f"> {HONESTY_BANNER}", "", "Quality checks measure constraint satisfaction and statistical similarity to declared reference statistics. They do not establish real-world validity.", ""]
    for table_name, table in report["tables"].items():
        lines += [f"## Table: {table_name}", "", f"Rows: {table['row_count']}", "", "### Marginals", "", "| Column | Summary | Reference distance |", "| --- | --- | --- |"]
        for column, detail in table["columns"].items():
            summary = json.dumps(detail["marginal"], sort_keys=True, ensure_ascii=False)
            comparison = detail.get("reference_comparison", {}).get("distance", "")
            lines.append(f"| {column} | `{summary}` | {comparison} |")
        lines += ["", "### Constraint satisfaction", "", "| Constraint | Target | Observed | Result |", "| --- | --- | --- | --- |"]
        for check in table["constraints"]:
            result = "PASS" if check["passed"] else "FAIL"
            lines.append(f"| {check['name']} | {check['target']} | {check['observed']} | {result} |")
        lines.append("")
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    body = [f'<div class="synthetic-banner">{html.escape(HONESTY_BANNER)}</div>', "<p>Quality checks measure constraint satisfaction and statistical similarity to declared reference statistics. They do not establish real-world validity.</p>"]
    for table_name, table in report["tables"].items():
        body.append(f"<h2>Table: {html.escape(table_name)}</h2><p>Rows: {table['row_count']}</p><h3>Marginals</h3><table><thead><tr><th>Column</th><th>Summary</th><th>Reference distance</th></tr></thead><tbody>")
        for column, detail in table["columns"].items():
            summary = html.escape(json.dumps(detail["marginal"], sort_keys=True, ensure_ascii=False))
            comparison = detail.get("reference_comparison", {}).get("distance", "")
            body.append(f"<tr><td>{html.escape(column)}</td><td><code>{summary}</code></td><td>{html.escape(str(comparison))}</td></tr>")
        body.append("</tbody></table><h3>Constraint satisfaction</h3><table><thead><tr><th>Constraint</th><th>Target</th><th>Observed</th><th>Result</th></tr></thead><tbody>")
        for check in table["constraints"]:
            result = "PASS" if check["passed"] else "FAIL"
            body.append(f"<tr><td>{html.escape(check['name'])}</td><td>{check['target']}</td><td>{check['observed']}</td><td class={'pass' if check['passed'] else 'fail'}>{result}</td></tr>")
        body.append("</tbody></table>")
    return "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>SyntheticData Forge quality report</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033}table{border-collapse:collapse;width:100%;margin:1rem 0 2rem}th,td{border:1px solid #ccd2dc;padding:.55rem;text-align:left;vertical-align:top}th{background:#f1f4f8}.synthetic-banner{background:#7b1e1e;color:white;font-weight:700;padding:1rem;border-radius:.35rem}.pass{color:#176b38;font-weight:700}.fail{color:#a11;font-weight:700}code{overflow-wrap:anywhere}</style></head><body><h1>SyntheticData Forge quality report</h1>" + "\n".join(body) + "</body></html>\n"


def write_reports(output_dir: str | Path, report: dict[str, Any]) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path, html_path = out / "quality-report.md", out / "quality-report.html"
    md_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    html_path.write_text(render_html(report), encoding="utf-8", newline="\n")
    return md_path, html_path


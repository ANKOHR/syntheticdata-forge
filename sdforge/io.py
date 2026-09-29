"""Stable CSV, JSONL, manifest and input table serialization."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from . import GENERATOR_VERSION
from .compiler import BuildPlan
from .generation import PROVENANCE_FIELDS


def _json_bytes(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, default=str) + "\n"
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def write_tables(output_dir: str | Path, plan: BuildPlan, data: dict[str, list[dict[str, Any]]], dedup: dict[str, dict[str, int]], seed: int) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    row_counts: dict[str, int] = {}
    embed = bool(plan.spec.get("provenance_columns", plan.spec.get("include_provenance", False)))
    for table_name, rows in data.items():
        columns = list(plan.spec["tables"][table_name]["columns"])
        if embed:
            columns.extend(PROVENANCE_FIELDS)
        with (out / f"{table_name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
        with (out / f"{table_name}.jsonl").open("w", newline="", encoding="utf-8") as handle:
            for row in rows:
                handle.write(_json_bytes(row) + "\n")
        row_counts[table_name] = len(rows)
    manifest = {
        "dedup_statistics": dedup,
        "generator_version": GENERATOR_VERSION,
        "row_counts": row_counts,
        "seed": int(seed),
        "spec_sha256": plan.spec_sha256,
    }
    (out / "manifest.json").write_text(_json_bytes(manifest, pretty=True), encoding="utf-8", newline="\n")
    return manifest


def read_tables(input_dir: str | Path, plan: BuildPlan) -> dict[str, list[dict[str, Any]]]:
    """Read CSV artifacts and coerce values according to their declared generator."""
    import datetime as dt
    from .compiler import _column_type

    directory = Path(input_dir)
    result: dict[str, list[dict[str, Any]]] = {}
    for table_name, table in plan.spec["tables"].items():
        path = directory / f"{table_name}.csv"
        with path.open("r", newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.DictReader(handle))
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            row: dict[str, Any] = {}
            for column, config in table["columns"].items():
                value = raw.get(column, "")
                kind = _column_type(config, column)
                if value == "":
                    row[column] = value
                elif kind == "integer":
                    row[column] = int(value)
                elif kind in {"uniform", "normal", "transform"}:
                    row[column] = float(value)
                elif kind == "date":
                    row[column] = value
                elif kind == "datetime":
                    row[column] = value
                elif kind == "foreign_key":
                    parent_config = plan.spec["tables"][config["table"]]["columns"][config["column"]]
                    parent_kind = _column_type(parent_config, config["column"])
                    if parent_kind == "integer":
                        value = int(value)
                    elif parent_kind in {"uniform", "normal", "transform"}:
                        value = float(value)
                    row[column] = value
                else:
                    row[column] = value
            rows.append(row)
        result[table_name] = rows
    return result


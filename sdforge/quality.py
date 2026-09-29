"""Deterministic marginal summaries and executable diversity checks."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from .compiler import BuildPlan, _column_type, _constraints_for, _date_bounds


def _number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(value)


def _string(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _domain_string(value: Any, config: dict[str, Any], kind: str) -> str:
    fmt = config.get("format")
    if isinstance(value, datetime) and fmt:
        return value.strftime(fmt)
    if isinstance(value, datetime) and kind == "date":
        return value.date().isoformat()
    if isinstance(value, datetime) and kind == "datetime":
        return value.isoformat(timespec="seconds")
    return _string(value)


def marginal(rows: list[dict[str, Any]], config: dict[str, Any], kind: str) -> dict[str, Any]:
    values = [row["value"] for row in rows if "value" in row]
    count = len(values)
    if kind in {"uniform", "normal", "integer", "transform"}:
        nums = [_number(value) for value in values]
        mean = sum(nums) / count if count else None
        variance = sum((value - mean) ** 2 for value in nums) / count if count else None
        return {
            "count": count,
            "mean": mean,
            "stddev": math.sqrt(variance) if variance is not None else None,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
        }
    frequencies: dict[str, int] = {}
    for value in values:
        key = _string(value)
        frequencies[key] = frequencies.get(key, 0) + 1
    return {
        "count": count,
        "frequencies": {key: frequencies[key] / count for key in sorted(frequencies)} if count else {},
        "distinct": len(frequencies),
    }


def _reference_result(observed: dict[str, Any], reference: Any, kind: str) -> dict[str, Any] | None:
    if not isinstance(reference, dict):
        return None
    if kind == "categorical" and ("distribution" in reference or "frequencies" in reference):
        target = reference.get("distribution", reference.get("frequencies", {}))
        target = {str(key): float(value) for key, value in target.items()}
        actual = observed["frequencies"]
        keys = sorted(set(target) | set(actual))
        distance = 0.5 * sum(abs(target.get(key, 0.0) - actual.get(key, 0.0)) for key in keys)
        return {"kind": "categorical_total_variation", "reference": target, "observed": actual, "distance": distance}
    metrics = {}
    for field in ("mean", "stddev"):
        if field in reference:
            observed_value = observed.get(field)
            target = float(reference[field])
            metrics[f"{field}_absolute_error"] = abs(observed_value - target) if observed_value is not None else None
    if not metrics:
        return None
    finite = [value for value in metrics.values() if value is not None]
    fields = [key.removesuffix("_absolute_error") for key in metrics]
    return {"kind": "numeric_absolute_error", "reference": {field: reference[field] for field in fields}, "observed": {field: observed.get(field) for field in fields}, "distances": metrics, "distance": sum(finite) / len(finite) if finite else None}


def _coerce_for_constraint(value: Any, kind: str) -> Any:
    if kind in {"uniform", "normal", "integer", "transform"}:
        return _number(value)
    if kind in {"date", "datetime"}:
        return _string(value)
    return value


def _range_bin(value: Any, lo: float, hi: float, bins: int) -> int:
    if hi == lo:
        return 0
    index = int((float(value) - lo) / (hi - lo) * bins)
    return min(bins - 1, max(0, index))


def evaluate_constraints(table: dict[str, Any], rows: list[dict[str, Any]], finite_domains: dict[str, list[Any] | None] | None = None) -> list[dict[str, Any]]:
    """Check actual generated rows, reporting pass/fail for every declared constraint."""
    columns = table["columns"]
    minimum, ranges, pairs = _constraints_for(table)
    checks: list[dict[str, Any]] = []
    for column, required in minimum.items():
        distinct = len({_string(row[column]) for row in rows if column in row})
        checks.append({"name": f"min_distinct:{column}", "target": int(required), "observed": distinct, "passed": distinct >= int(required)})
    for column, raw_bins in ranges.items():
        bins = raw_bins.get("bins") if isinstance(raw_bins, dict) else raw_bins
        config = columns[column]
        kind = _column_type(config, column)
        values = [row[column] for row in rows if column in row]
        try:
            if kind in {"uniform", "integer"}:
                lo, hi = float(config["min"]), float(config["max"])
            elif kind in {"date", "datetime"}:
                start, end = _date_bounds(config, kind)
                lo, hi = start.timestamp(), end.timestamp()
                if config.get("format"):
                    values = [datetime.strptime(str(value), config["format"]).replace(tzinfo=start.tzinfo).timestamp() for value in values]
                else:
                    values = [datetime.fromisoformat(str(value)).timestamp() if isinstance(value, str) else value.timestamp() for value in values]
            else:
                numeric = [_number(value) for value in values]
                lo, hi = (min(numeric), max(numeric)) if numeric else (0.0, 0.0)
                values = numeric
            occupied = sorted({_range_bin(value, lo, hi, int(bins)) for value in values})
            passed = len(occupied) == int(bins)
        except (ValueError, TypeError, OverflowError):
            occupied, passed = [], False
        checks.append({"name": f"range_coverage:{column}", "target": int(bins), "observed": len(occupied), "passed": passed, "occupied_bins": occupied})
    for pair in pairs:
        a, b = pair
        kind_a, kind_b = _column_type(columns[a], a), _column_type(columns[b], b)
        domain_a = (finite_domains or {}).get(a)
        domain_b = (finite_domains or {}).get(b)
        expected_pairs = {
            (_domain_string(va, columns[a], kind_a), _domain_string(vb, columns[b], kind_b))
            for va in (domain_a or []) for vb in (domain_b or [])
        }
        observed_pairs = {(_string(row[a]), _string(row[b])) for row in rows if a in row and b in row}
        checks.append({"name": f"pairwise:{a},{b}", "target": len(expected_pairs), "observed": len(observed_pairs), "passed": bool(expected_pairs) and expected_pairs.issubset(observed_pairs)})
    return checks


def evaluate_quality(plan: BuildPlan, data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    report: dict[str, Any] = {"generator_version": "1.0.0", "tables": {}}
    for table_name, table in plan.spec["tables"].items():
        rows = data.get(table_name, [])
        table_quality: dict[str, Any] = {"row_count": len(rows), "columns": {}, "constraints": evaluate_constraints(table, rows, plan.finite_domains.get(table_name))}
        for column, config in table["columns"].items():
            kind = _column_type(config, column)
            values = [{"value": row[column]} for row in rows if column in row]
            summary = marginal(values, config, kind)
            entry = {"marginal": summary}
            reference = _reference_result(summary, config.get("reference"), kind)
            if reference is not None:
                entry["reference_comparison"] = reference
            table_quality["columns"][column] = entry
        report["tables"][table_name] = table_quality
    return report

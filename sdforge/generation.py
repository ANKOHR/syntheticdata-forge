"""Seeded rule-based row generation and deterministic row-hash deduplication."""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timedelta
from typing import Any

from . import GENERATOR_VERSION
from .compiler import BuildPlan, _categories, _column_type, _date_bounds, _constraints_for


PROVENANCE_FIELDS = ("_spec_sha256", "_seed", "_generator_version")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _base_domain(config: dict[str, Any], kind: str) -> list[Any] | None:
    if kind == "categorical":
        return _categories(config)[0]
    if kind == "integer":
        lo, hi = int(config["min"]), int(config["max"])
        if hi - lo <= 100_000:
            return list(range(lo, hi + 1))
    if kind == "uniform" and float(config["min"]) == float(config["max"]):
        return [float(config["min"])]
    if kind == "normal" and float(config.get("stddev", config.get("sd", 0))) == 0:
        return [float(config["mean"])]
    if kind in {"date", "datetime"}:
        start, end = _date_bounds(config, kind)
        seconds = (end - start).total_seconds()
        if seconds >= 0 and seconds <= 100_000 and seconds.is_integer():
            step = timedelta(days=1) if kind == "date" else timedelta(seconds=1)
            count = int((end - start).days if kind == "date" else seconds)
            return [start + i * step for i in range(count + 1)]
    return None


def _forced_diversity(
    table: dict[str, Any],
    finite_domains: dict[str, list[Any] | None] | None = None,
    additional_minimum: dict[str, int] | None = None,
) -> dict[str, dict[int, Any]]:
    minimum, ranges, _ = _constraints_for(table)
    for column, required in (additional_minimum or {}).items():
        minimum[column] = max(int(minimum.get(column, 0)), int(required))
    result: dict[str, dict[int, Any]] = {}

    def force_values(column: str, values: list[Any]) -> None:
        config = table["columns"][column]
        kind = _column_type(config, column)
        if kind != "transform":
            result[column] = {index: value for index, value in enumerate(values)}
            return
        base = config["base"]
        base_domain = (finite_domains or {}).get(base)
        if base_domain is None:
            return
        outputs: list[tuple[Any, Any]] = []
        for base_value in base_domain:
            output = base_value * float(config.get("scale", 1)) + float(config.get("offset", 0))
            if "round" in config:
                output = round(output, int(config["round"]))
            if "min" in config:
                output = max(output, float(config["min"]))
            if "max" in config:
                output = min(output, float(config["max"]))
            outputs.append((output, base_value))
        base_values: list[Any] = []
        for wanted in values:
            match = next((base_value for output, base_value in outputs if output == wanted), None)
            if match is not None and match not in base_values:
                base_values.append(match)
        if len(base_values) == len(values):
            force_values(base, base_values)

    for column, required in minimum.items():
        config = table["columns"][column]
        kind = _column_type(config, column)
        domain = (finite_domains or {}).get(column, _base_domain(config, kind))
        if domain is not None:
            chosen = domain[: int(required)]
            if kind == "date":
                chosen = [value.strftime(config["format"]) if config.get("format") and isinstance(value, datetime) else value.date().isoformat() if isinstance(value, datetime) else value for value in chosen]
            elif kind == "datetime":
                chosen = [value.strftime(config["format"]) if config.get("format") and isinstance(value, datetime) else value.isoformat(timespec="seconds") if isinstance(value, datetime) else value for value in chosen]
        elif kind in {"uniform", "integer"}:
            lo, hi = float(config["min"]), float(config["max"])
            chosen = [lo + (hi - lo) * i / max(1, int(required) - 1) for i in range(int(required))]
            if kind == "integer":
                chosen = [round(item) for item in chosen]
        elif kind == "normal":
            mean, sd = float(config["mean"]), float(config.get("stddev", config.get("sd", 0)))
            chosen = [mean + sd * (i - (int(required) - 1) / 2) for i in range(int(required))]
        elif kind == "transform":
            base_config = table["columns"][config["base"]]
            base_kind = _column_type(base_config, config["base"])
            base_values = (finite_domains or {}).get(config["base"], _base_domain(base_config, base_kind))
            if base_values is not None:
                derived = []
                for value in base_values:
                    output = value * float(config.get("scale", 1)) + float(config.get("offset", 0))
                    if "round" in config:
                        output = round(output, int(config["round"]))
                    if "min" in config:
                        output = max(output, float(config["min"]))
                    if "max" in config:
                        output = min(output, float(config["max"]))
                    if output not in derived:
                        derived.append(output)
                chosen = derived[: int(required)]
            else:
                continue
        else:
            continue
        force_values(column, chosen)
    for column, raw_bins in ranges.items():
        bins = raw_bins.get("bins") if isinstance(raw_bins, dict) else raw_bins
        config = table["columns"][column]
        kind = _column_type(config, column)
        if kind in {"uniform", "integer"}:
            lo, hi = float(config["min"]), float(config["max"])
        elif kind in {"date", "datetime"}:
            start, end = _date_bounds(config, kind)
            lo, hi = start.timestamp(), end.timestamp()
        else:
            continue
        for index in range(int(bins)):
            value = lo + (hi - lo) * (index + 0.5) / int(bins)
            if kind == "integer":
                value = round(value)
            elif kind in {"date", "datetime"}:
                instant = datetime.fromtimestamp(value, tz=start.tzinfo)
                if config.get("format"):
                    value = instant.strftime(config["format"])
                else:
                    value = instant.date().isoformat() if kind == "date" else instant.isoformat(timespec="seconds")
            result.setdefault(column, {})[index] = value
    return result


def _sample(config: dict[str, Any], kind: str, rng: random.Random, row: dict[str, Any], parents: dict[str, list[dict[str, Any]]]) -> Any:
    if kind == "uniform":
        return rng.uniform(float(config["min"]), float(config["max"]))
    if kind == "integer":
        return rng.randint(int(config["min"]), int(config["max"]))
    if kind == "normal":
        # Explicit Box-Muller avoids random.gauss implementation details.
        u1 = max(rng.random(), 1e-300)
        u2 = rng.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return float(config["mean"]) + float(config.get("stddev", config.get("sd", 0))) * z
    if kind == "categorical":
        values, weights = _categories(config)
        return rng.choices(values, weights=weights, k=1)[0]
    if kind in {"date", "datetime"}:
        start, end = _date_bounds(config, kind)
        seconds = int((end - start).total_seconds())
        value = start + timedelta(seconds=rng.randint(0, seconds))
        fmt = config.get("format")
        return value.strftime(fmt) if fmt else (value.date().isoformat() if kind == "date" else value.isoformat(timespec="seconds"))
    if kind == "transform":
        value = row[config["base"]]
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
        output = value * float(config.get("scale", 1)) + float(config.get("offset", 0))
        if "round" in config:
            output = round(output, int(config["round"]))
        if "min" in config:
            output = max(output, float(config["min"]))
        if "max" in config:
            output = min(output, float(config["max"]))
        return output
    if kind == "foreign_key":
        parent_rows = parents[config["table"]]
        values = list(dict.fromkeys(item[config["column"]] for item in parent_rows))
        return rng.choice(values)
    if kind == "text":
        slots = {}
        for name, source in config.get("slots", {}).items():
            if isinstance(source, str) and source.startswith("column:"):
                slots[name] = row[source.partition(":")[2]]
            elif isinstance(source, dict) and "column" in source:
                slots[name] = row[source["column"]]
            elif isinstance(source, dict) and "value" in source:
                slots[name] = source["value"]
            elif isinstance(source, dict) and ("values" in source or "choices" in source):
                slots[name] = rng.choice(source.get("values", source.get("choices")))
            elif isinstance(source, (list, tuple)):
                slots[name] = rng.choice(source)
            else:
                slots[name] = source
        return config["template"].format_map(slots)
    raise AssertionError(f"unhandled generator {kind}")


def generate(plan: BuildPlan, seed: int | None = None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, int]]]:
    """Generate each table in dependency order, then drop duplicate rows."""
    chosen_seed = int(seed if seed is not None else plan.spec.get("seed", 0))
    rng = random.Random(chosen_seed)
    parents: dict[str, list[dict[str, Any]]] = {}
    dedup: dict[str, dict[str, int]] = {}
    result: dict[str, list[dict[str, Any]]] = {}
    embed = bool(plan.spec.get("provenance_columns", plan.spec.get("include_provenance", False)))
    for table_name in plan.table_order:
        table = plan.spec["tables"][table_name]
        columns = table["columns"]
        row_count = int(table["rows"])
        pair_values = {index: values for index, values in enumerate(plan.pairwise_schedules.get(table_name, ()))}
        forced = _forced_diversity(table, plan.finite_domains.get(table_name), plan.forced_min_distinct.get(table_name))
        rows: list[dict[str, Any]] = []
        for row_index in range(row_count):
            row: dict[str, Any] = {}
            scheduled = {**pair_values.get(row_index, {})}
            for column, by_row in forced.items():
                if row_index in by_row and column not in scheduled:
                    scheduled[column] = by_row[row_index]
            pending = list(columns)
            while pending:
                progressed = False
                for column in list(pending):
                    if column in scheduled:
                        value = scheduled[column]
                        kind = _column_type(columns[column], f"tables.{table_name}.columns.{column}")
                        if kind in {"date", "datetime"} and isinstance(value, datetime) and columns[column].get("format"):
                            value = value.strftime(columns[column]["format"])
                        elif kind == "date" and isinstance(value, datetime):
                            value = value.date().isoformat()
                        elif kind == "datetime" and isinstance(value, datetime):
                            fmt = columns[column].get("format")
                            value = value.strftime(fmt) if fmt else value.isoformat(timespec="seconds")
                        row[column] = value
                        pending.remove(column)
                        progressed = True
                        continue
                    config = columns[column]
                    kind = _column_type(config, f"tables.{table_name}.columns.{column}")
                    refs = []
                    if kind == "transform":
                        refs.append(config["base"])
                    elif kind == "text":
                        for source in config.get("slots", {}).values():
                            if isinstance(source, str) and source.startswith("column:"):
                                refs.append(source.partition(":")[2])
                            elif isinstance(source, dict) and "column" in source:
                                refs.append(source["column"])
                    if any(ref not in row for ref in refs):
                        continue
                    row[column] = _sample(config, kind, rng, row, parents)
                    pending.remove(column)
                    progressed = True
                if not progressed:
                    # Compiler validation normally catches this; keep a useful guard for direct plans.
                    raise ValueError(f"unresolvable column dependency in table {table_name}: {', '.join(pending)}")
            for field, value in zip(PROVENANCE_FIELDS, (plan.spec_sha256, chosen_seed, GENERATOR_VERSION)):
                if embed:
                    row[field] = value
            rows.append(row)
        minimum, ranges, _ = _constraints_for(table)
        for column, raw_bins in ranges.items():
            if _column_type(columns[column], column) != "normal" or not rows:
                continue
            bins = raw_bins.get("bins") if isinstance(raw_bins, dict) else raw_bins
            required_distinct = int(minimum.get(column, 1))
            target_count = max(int(bins), required_distinct)
            numeric = [float(row[column]) for row in rows]
            low, high = min(numeric), max(numeric)
            for index in range(target_count):
                value = (low + high) / 2 if target_count == 1 else low + (high - low) * index / (target_count - 1)
                rows[index][column] = value
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            payload = {key: row[key] for key in columns}
            digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
            if digest not in seen:
                seen.add(digest)
                unique.append(row)
        result[table_name] = unique
        dedup[table_name] = {"input_rows": row_count, "output_rows": len(unique), "dropped_rows": row_count - len(unique)}
        parents[table_name] = unique
    return result, dedup

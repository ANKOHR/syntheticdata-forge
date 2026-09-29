"""Load and validate YAML dataset specifications before any rows are generated."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from itertools import product
import math
import re
import string
from pathlib import Path
from typing import Any

import yaml

from .errors import SpecError

GENERATOR_ALIASES = {
    "uniform": "uniform",
    "normal": "normal",
    "integer": "integer",
    "int": "integer",
    "categorical": "categorical",
    "text": "text",
    "template": "text",
    "date": "date",
    "datetime": "datetime",
    "transform": "transform",
    "correlated": "transform",
    "foreign_key": "foreign_key",
    "fk": "foreign_key",
}


@dataclass(frozen=True)
class BuildPlan:
    """Validated specification plus the source hash and table build order."""

    spec: dict[str, Any]
    spec_sha256: str
    table_order: tuple[str, ...]
    pairwise_schedules: dict[str, tuple[dict[str, Any], ...]]
    finite_domains: dict[str, dict[str, list[Any] | None]]
    forced_min_distinct: dict[str, dict[str, int]]


def load_spec(path: str | Path) -> tuple[dict[str, Any], str]:
    """Read YAML and return its parsed mapping and exact-byte SHA-256."""
    import hashlib

    source = Path(path).read_bytes()
    try:
        value = yaml.safe_load(source) or {}
    except yaml.YAMLError as exc:
        raise SpecError("E_INVALID_SPEC", str(exc), str(path)) from exc
    if not isinstance(value, dict):
        raise SpecError("E_INVALID_SPEC", "specification root must be a mapping", str(path))
    return value, hashlib.sha256(source).hexdigest()


def _column_type(config: dict[str, Any], path: str) -> str:
    raw = config.get("type", config.get("generator", ""))
    if not isinstance(raw, str) or raw.lower() not in GENERATOR_ALIASES:
        raise SpecError("E_UNKNOWN_GENERATOR", f"unknown generator {raw!r}", path)
    return GENERATOR_ALIASES[raw.lower()]


def _finite_domain(config: dict[str, Any], kind: str) -> list[Any] | None:
    if kind == "categorical":
        values, weights = _categories(config)
        return list(dict.fromkeys(value for value, weight in zip(values, weights) if weight > 0))
    if kind == "integer":
        low, high = int(config["min"]), int(config["max"])
        if high - low <= 100_000:
            return list(range(low, high + 1))
    if kind == "uniform" and float(config["min"]) == float(config["max"]):
        return [float(config["min"])]
    if kind == "normal" and float(config.get("stddev", config.get("sd", 0))) == 0:
        return [float(config["mean"])]
    if kind in {"date", "datetime"}:
        start, end = _date_bounds(config, kind)
        delta = (end - start).total_seconds()
        if kind == "date":
            delta = (end - start).days
        if 0 <= delta <= 100_000 and int(delta) == delta:
            from datetime import timedelta

            unit = timedelta(days=1) if kind == "date" else timedelta(seconds=1)
            return [start + i * unit for i in range(int(delta) + 1)]
    return None


def _categories(config: dict[str, Any]) -> tuple[list[Any], list[float]]:
    raw = config.get("values", config.get("weights", config.get("choices")))
    if isinstance(raw, dict):
        values = list(raw.keys())
        weights = [float(item) for item in raw.values()]
    elif isinstance(raw, list):
        values, weights = [], []
        for item in raw:
            if isinstance(item, dict) and "value" in item:
                values.append(item["value"])
                weights.append(float(item.get("weight", 1)))
            else:
                values.append(item)
                weights.append(1.0)
    else:
        return [], []
    return values, weights


def _date_bounds(config: dict[str, Any], kind: str) -> tuple[datetime, datetime]:
    try:
        if kind == "date":
            start_d = date.fromisoformat(str(config["start"]))
            end_d = date.fromisoformat(str(config["end"]))
            return datetime.combine(start_d, datetime.min.time()), datetime.combine(end_d, datetime.min.time())
        return datetime.fromisoformat(str(config["start"])), datetime.fromisoformat(str(config["end"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("start and end must be valid ISO dates or datetimes") from exc


def _validate_generator(config: dict[str, Any], kind: str, path: str) -> None:
    try:
        if kind in {"uniform", "integer"}:
            low, high = float(config["min"]), float(config["max"])
            if not math.isfinite(low) or not math.isfinite(high) or low > high:
                raise ValueError("min must not exceed max")
            if kind == "integer" and (int(config["min"]) != config["min"] or int(config["max"]) != config["max"]):
                raise ValueError("integer bounds must be integers")
        elif kind == "normal":
            mean = float(config["mean"])
            sd = float(config.get("stddev", config.get("sd", 0)))
            if not math.isfinite(mean) or not math.isfinite(sd) or sd < 0:
                raise ValueError("stddev must be non-negative")
        elif kind == "categorical":
            values, weights = _categories(config)
            if not values or len(weights) != len(values) or any(not math.isfinite(weight) or weight < 0 for weight in weights) or sum(weights) <= 0:
                raise ValueError("categorical values need non-negative weights with a positive total")
            try:
                for value in values:
                    hash(value)
            except TypeError as exc:
                raise ValueError("categorical values must be scalar and hashable") from exc
        elif kind == "text":
            if not isinstance(config.get("template"), str):
                raise ValueError("text generator requires a template string")
        elif kind in {"date", "datetime"}:
            start, end = _date_bounds(config, kind)
            if start > end:
                raise ValueError("start must not exceed end")
        elif kind == "transform":
            if not isinstance(config.get("base"), str):
                raise ValueError("transform generator requires a base column")
            scale, offset = float(config.get("scale", 1)), float(config.get("offset", 0))
            if not math.isfinite(scale) or not math.isfinite(offset):
                raise ValueError("transform scale and offset must be finite")
            bounds = [float(config[key]) for key in ("min", "max") if key in config]
            if any(not math.isfinite(bound) for bound in bounds):
                raise ValueError("transform bounds must be finite")
            if "min" in config and "max" in config and float(config["min"]) > float(config["max"]):
                raise ValueError("transform min must not exceed max")
        elif kind == "foreign_key":
            if not isinstance(config.get("table"), str) or not isinstance(config.get("column"), str):
                raise ValueError("foreign_key requires table and column")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, SpecError):
            raise
        code = "E_INVALID_DISTRIBUTION" if kind in {"uniform", "normal", "integer", "categorical"} else "E_INVALID_SPEC"
        raise SpecError(code, str(exc), path) from exc


def _constraints_for(table: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
    minimum: dict[str, Any] = {}
    ranges: dict[str, Any] = {}
    pairs: list[Any] = []
    for name, config in table.get("columns", {}).items():
        diversity = config.get("diversity", {}) if isinstance(config, dict) else {}
        if "min_distinct" in diversity:
            minimum[name] = diversity["min_distinct"]
        if "range_coverage" in diversity:
            ranges[name] = diversity["range_coverage"]
    constraints = table.get("constraints", {}) or {}
    minimum.update(constraints.get("min_distinct", {}))
    ranges.update(constraints.get("range_coverage", {}))
    seen_pairs: set[tuple[str, str]] = set()
    for pair in constraints.get("pairwise", []):
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            normalized = tuple(sorted((str(pair[0]), str(pair[1]))))
            if normalized not in seen_pairs:
                seen_pairs.add(normalized)
                pairs.append(normalized)
        else:
            pairs.append(pair)
    return minimum, ranges, pairs


def _pairwise_schedule(pairs: list[Any], domains: dict[str, list[Any] | None], rows: int, path: str) -> tuple[dict[str, Any], ...]:
    if not pairs:
        return ()
    normalized: list[tuple[str, str]] = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or pair[0] == pair[1]:
            raise SpecError("E_UNSATISFIABLE_DIVERSITY", "each pairwise entry must name two different columns", path)
        a, b = str(pair[0]), str(pair[1])
        if a not in domains or b not in domains:
            raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"pairwise columns {a!r}, {b!r} must exist", path)
        if domains[a] is None or domains[b] is None:
            raise SpecError("E_UNSATISFIABLE_DIVERSITY", "pairwise coverage requires finite generator domains", path)
        if not domains[a] or not domains[b]:
            raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"pair {a!r},{b!r} has an empty reachable domain", path)
        combinations = len(domains[a]) * len(domains[b])
        if combinations > rows:
            raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"pair {a!r},{b!r} needs {combinations} rows but table has {rows}", path)
        normalized.append((a, b))
    # Build a concrete covering array for each connected group. Finding a row
    # schedule is a constructive proof that every requested pair is reachable.
    adjacency: dict[str, set[str]] = {}
    for a, b in normalized:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    pending = set(adjacency)
    all_rows: list[dict[str, Any]] = [{} for _ in range(rows)]
    while pending:
        first = pending.pop()
        component = {first}
        stack = [first]
        while stack:
            current = stack.pop()
            for other in adjacency[current]:
                if other in pending:
                    pending.remove(other)
                    component.add(other)
                    stack.append(other)
        names = sorted(component)
        component_pairs = [(a, b) for a, b in normalized if a in component and b in component]
        if len(component_pairs) == 1:
            a, b = component_pairs[0]
            for index, (va, vb) in enumerate(product(domains[a] or [], domains[b] or [])):
                all_rows[index].update({a: va, b: vb})
            continue
        assignment_count = 1
        for column in names:
            assignment_count *= len(domains[column] or [])
        if assignment_count > 50_000:
            raise SpecError(
                "E_DIVERSITY_COMPLEXITY",
                f"pairwise domain for columns {', '.join(names)} is too large to compile safely",
                path,
            )
        universe: list[tuple[str, str, Any, Any]] = []
        for a, b in component_pairs:
            universe.extend((a, b, va, vb) for va in domains[a] or [] for vb in domains[b] or [])
        if len(universe) > 4096:
            raise SpecError("E_DIVERSITY_COMPLEXITY", f"pairwise coverage for columns {', '.join(names)} is too large to compile safely", path)
        atom_index = {atom: index for index, atom in enumerate(universe)}
        representatives: dict[int, dict[str, Any]] = {}
        for values in product(*(domains[name] or [] for name in names)):
            assignment = dict(zip(names, values))
            mask = 0
            for a, b in component_pairs:
                mask |= 1 << atom_index[(a, b, assignment[a], assignment[b])]
            representatives.setdefault(mask, assignment)
        candidates = list(representatives)
        full = (1 << len(universe)) - 1

        # Greedy construction is fast for common covering arrays. If it does
        # not meet the row budget, an exact bounded set-cover search decides
        # satisfiability for these finite domains.
        remaining = full
        greedy: list[int] = []
        while remaining:
            choices = [(mask & remaining).bit_count() for mask in candidates]
            best_index = max(range(len(candidates)), key=lambda index: (choices[index], -index))
            best = candidates[best_index]
            gain = (best & remaining).bit_count()
            if not gain:
                break
            greedy.append(best)
            remaining &= ~best

        if not remaining and len(greedy) <= rows:
            selected = greedy
        else:
            by_atom: list[list[int]] = [[] for _ in universe]
            for mask in candidates:
                bits = mask
                while bits:
                    least = bits & -bits
                    by_atom[least.bit_length() - 1].append(mask)
                    bits ^= least
            by_atom = [sorted(set(items), key=lambda mask: (-(mask & full).bit_count(), mask)) for items in by_atom]
            failed_states: set[tuple[int, int]] = set()

            def search(missing: int, slots: int) -> list[int] | None:
                if not missing:
                    return []
                if slots <= 0 or (missing, slots) in failed_states:
                    return None
                max_gain = max(((mask & missing).bit_count() for mask in candidates), default=0)
                if not max_gain or (missing.bit_count() + max_gain - 1) // max_gain > slots:
                    failed_states.add((missing, slots))
                    return None
                bits = missing
                best_options: list[int] | None = None
                while bits:
                    least = bits & -bits
                    options = [mask for mask in by_atom[least.bit_length() - 1] if mask & missing]
                    if best_options is None or len(options) < len(best_options):
                        best_options = options
                        if len(options) <= 1:
                            break
                    bits ^= least
                for mask in best_options or []:
                    answer = search(missing & ~mask, slots - 1)
                    if answer is not None:
                        return [mask, *answer]
                failed_states.add((missing, slots))
                return None

            selected = search(full, rows)
            if selected is None:
                raise SpecError(
                    "E_UNSATISFIABLE_DIVERSITY",
                    f"pairwise coverage for columns {', '.join(names)} cannot be covered in {rows} rows",
                    path,
                )
        for index, mask in enumerate(selected):
            all_rows[index].update(representatives[mask])
    return tuple(row for row in all_rows if row)


def compile_spec(spec: dict[str, Any], spec_sha256: str = "") -> BuildPlan:
    """Validate generators, references and diversity feasibility without generation."""
    tables = spec.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise SpecError("E_INVALID_SPEC", "spec must define at least one table", "tables")
    if "seed" in spec:
        try:
            seed = int(spec["seed"])
            if isinstance(spec["seed"], bool) or seed != spec["seed"]:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise SpecError("E_INVALID_SPEC", "seed must be an integer", "seed") from exc
    normalized: dict[str, dict[str, Any]] = {}
    domains_by_table: dict[str, dict[str, list[Any] | None]] = {}
    for table_name, raw_table in tables.items():
        if not isinstance(table_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
            raise SpecError("E_INVALID_SPEC", "table names must be simple identifiers", "tables")
        path = f"tables.{table_name}"
        if not isinstance(raw_table, dict) or not isinstance(raw_table.get("columns"), dict) or not raw_table["columns"]:
            raise SpecError("E_INVALID_SPEC", "table must define a columns mapping", path)
        try:
            raw_count = raw_table.get("rows", raw_table.get("row_count", 0))
            row_count = int(raw_count)
            if isinstance(raw_count, bool) or row_count != raw_count:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise SpecError("E_INVALID_SPEC", "rows must be a non-negative integer", path) from exc
        if row_count < 0:
            raise SpecError("E_INVALID_SPEC", "rows must be a non-negative integer", path)
        normalized[table_name] = {**raw_table, "rows": row_count}
        domains: dict[str, list[Any] | None] = {}
        for column, config in raw_table["columns"].items():
            if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
                raise SpecError("E_INVALID_SPEC", "column names must be simple identifiers", path)
            cpath = f"{path}.columns.{column}"
            if not isinstance(config, dict):
                raise SpecError("E_INVALID_SPEC", "column definition must be a mapping", cpath)
            if not isinstance(config.get("diversity", {}), dict):
                raise SpecError("E_INVALID_SPEC", "column diversity must be a mapping", cpath)
            kind = _column_type(config, cpath)
            _validate_generator(config, kind, cpath)
            domains[column] = _finite_domain(config, kind)
        for column, config in raw_table["columns"].items():
            if _column_type(config, f"{path}.columns.{column}") == "transform":
                base = config["base"]
                if base not in raw_table["columns"]:
                    raise SpecError("E_INVALID_SPEC", f"base column {base!r} does not exist", f"{path}.columns.{column}")
                base_kind = _column_type(raw_table["columns"][base], f"{path}.columns.{base}")
                if base_kind not in {"uniform", "normal", "integer", "transform"}:
                    raise SpecError("E_INVALID_SPEC", "transform base must be numeric", f"{path}.columns.{column}")
        active_domains: set[str] = set()

        def resolve_domain(column: str) -> list[Any] | None:
            kind = _column_type(raw_table["columns"][column], f"{path}.columns.{column}")
            if kind != "transform" or domains[column] is not None:
                return domains[column]
            if column in active_domains:
                raise SpecError("E_INVALID_SPEC", "cyclic same-table column dependency", f"{path}.columns.{column}")
            active_domains.add(column)
            config = raw_table["columns"][column]
            base_values = resolve_domain(config["base"])
            derived = None
            if base_values is not None:
                derived = []
                for base_value in base_values:
                    value = base_value * float(config.get("scale", 1)) + float(config.get("offset", 0))
                    if "round" in config:
                        value = round(value, int(config["round"]))
                    if "min" in config:
                        value = max(value, float(config["min"]))
                    if "max" in config:
                        value = min(value, float(config["max"]))
                    if value not in derived:
                        derived.append(value)
            domains[column] = derived
            active_domains.remove(column)
            return derived

        for column in raw_table["columns"]:
            resolve_domain(column)
        domains_by_table[table_name] = domains

    early_dependencies: dict[str, set[str]] = {name: set() for name in normalized}
    for table_name, table in normalized.items():
        for column, config in table["columns"].items():
            if _column_type(config, f"tables.{table_name}.columns.{column}") != "foreign_key":
                continue
            parent, parent_column = config["table"], config["column"]
            if parent not in normalized or parent_column not in normalized[parent]["columns"]:
                raise SpecError("E_BROKEN_FOREIGN_KEY", f"reference {parent}.{parent_column} does not exist", f"tables.{table_name}.columns.{column}")
            if normalized[parent]["rows"] == 0 and table["rows"] > 0:
                raise SpecError("E_BROKEN_FOREIGN_KEY", f"parent table {parent!r} has no rows", f"tables.{table_name}.columns.{column}")
            early_dependencies[table_name].add(parent)
    pending_dependencies = {name: set(parents) for name, parents in early_dependencies.items()}
    while pending_dependencies:
        ready = [name for name in normalized if name in pending_dependencies and not pending_dependencies[name]]
        if not ready:
            raise SpecError("E_BROKEN_FOREIGN_KEY", "foreign-key dependency cycle detected", "tables")
        for name in ready:
            pending_dependencies.pop(name)
        for parents in pending_dependencies.values():
            parents.difference_update(ready)

    for table_name, table in normalized.items():
        raw_constraints = table.get("constraints", {})
        if raw_constraints is not None and not isinstance(raw_constraints, dict):
            raise SpecError("E_INVALID_SPEC", "constraints must be a mapping", f"tables.{table_name}.constraints")
        raw_constraints = raw_constraints or {}
        for field in ("min_distinct", "range_coverage"):
            if field in raw_constraints and not isinstance(raw_constraints[field], dict):
                raise SpecError("E_INVALID_SPEC", f"{field} constraints must be a mapping", f"tables.{table_name}.constraints.{field}")
        if "pairwise" in raw_constraints and not isinstance(raw_constraints["pairwise"], list):
            raise SpecError("E_INVALID_SPEC", "pairwise constraints must be a list", f"tables.{table_name}.constraints.pairwise")

    def resolve_foreign_domain(table_name: str, column: str, active: set[tuple[str, str]] | None = None) -> list[Any] | None:
        domain = domains_by_table[table_name][column]
        if domain is not None:
            return domain
        config = normalized[table_name]["columns"][column]
        if _column_type(config, f"tables.{table_name}.columns.{column}") != "foreign_key":
            return None
        active = set() if active is None else active
        key = (table_name, column)
        if key in active:
            return None
        parent, parent_column = config["table"], config["column"]
        if parent not in normalized or parent_column not in normalized[parent]["columns"]:
            return None
        active.add(key)
        parent_domain = resolve_foreign_domain(parent, parent_column, active)
        active.remove(key)
        if parent_domain is None:
            return None
        parent_config = normalized[parent]["columns"][parent_column]
        parent_kind = _column_type(parent_config, f"tables.{parent}.columns.{parent_column}")
        available = parent_domain[: int(normalized[parent]["rows"])]
        if parent_kind in {"date", "datetime"}:
            if parent_config.get("format"):
                resolved = [value.strftime(parent_config["format"]) if isinstance(value, datetime) else value for value in available]
            elif parent_kind == "date":
                resolved = [value.date().isoformat() if isinstance(value, datetime) else value for value in available]
            else:
                resolved = [value.isoformat(timespec="seconds") if isinstance(value, datetime) else value for value in available]
        else:
            resolved = available
        domains_by_table[table_name][column] = resolved
        return resolved

    for table_name, table in normalized.items():
        for column, config in table["columns"].items():
            if _column_type(config, f"tables.{table_name}.columns.{column}") == "foreign_key":
                resolve_foreign_domain(table_name, column)

    forced_min_distinct: dict[str, dict[str, int]] = {name: {} for name in normalized}
    for child_name, child_table in normalized.items():
        minimum, _, pairs = _constraints_for(child_table)
        for column, config in child_table["columns"].items():
            if _column_type(config, f"tables.{child_name}.columns.{column}") != "foreign_key":
                continue
            raw_requested = minimum.get(column, 0)
            try:
                requested = int(raw_requested)
                if isinstance(raw_requested, bool) or requested != raw_requested:
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", "min_distinct must be a positive integer", f"tables.{child_name}.constraints.min_distinct.{column}") from exc
            if any(column in pair for pair in pairs if isinstance(pair, (list, tuple)) and len(pair) == 2):
                domain = domains_by_table[child_name][column]
                if domain is None:
                    raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"foreign-key column {column!r} has no finite provable parent domain", f"tables.{child_name}.constraints.pairwise")
                requested = max(requested, len(domain))
            if not requested:
                continue
            domain = domains_by_table[child_name][column]
            parent, parent_column = config["table"], config["column"]
            if domain is None or parent not in normalized or parent_column not in normalized[parent]["columns"]:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"cannot prove a finite domain for foreign-key column {column!r}", f"tables.{child_name}.constraints.min_distinct.{column}")
            if requested > len(domain) or requested > normalized[parent]["rows"]:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"foreign-key column {column!r} needs {requested} distinct parent keys, but only {min(len(domain), normalized[parent]['rows'])} are available", f"tables.{child_name}.constraints.min_distinct.{column}")
            forced_min_distinct[parent][parent_column] = max(forced_min_distinct[parent].get(parent_column, 0), requested)

    # Resolve references before ordering tables so failures have stable names.
    dependencies: dict[str, set[str]] = {name: set() for name in normalized}
    pairwise_schedules: dict[str, tuple[dict[str, Any], ...]] = {}
    for table_name, table in normalized.items():
        columns = table["columns"]
        same_table_dependencies: dict[str, set[str]] = {column: set() for column in columns}
        for column, config in columns.items():
            kind = _column_type(config, f"tables.{table_name}.columns.{column}")
            if kind == "transform":
                base = config["base"]
                if base not in columns:
                    raise SpecError("E_INVALID_SPEC", f"base column {base!r} does not exist", f"tables.{table_name}.columns.{column}")
                base_kind = _column_type(columns[base], f"tables.{table_name}.columns.{base}")
                if base_kind not in {"uniform", "normal", "integer", "transform"}:
                    raise SpecError("E_INVALID_SPEC", "transform base must be numeric", f"tables.{table_name}.columns.{column}")
                same_table_dependencies[column].add(base)
            elif kind == "text":
                slots = config.get("slots", {})
                if not isinstance(slots, dict):
                    raise SpecError("E_INVALID_SPEC", "text slots must be a mapping", f"tables.{table_name}.columns.{column}")
                try:
                    fields = [field for _, field, _, _ in string.Formatter().parse(config["template"]) if field is not None]
                except ValueError as exc:
                    raise SpecError("E_INVALID_SPEC", str(exc), f"tables.{table_name}.columns.{column}") from exc
                for field in fields:
                    if field not in slots or "." in field or "[" in field:
                        raise SpecError("E_INVALID_SPEC", f"template field {field!r} has no supported slot", f"tables.{table_name}.columns.{column}")
                for slot_name, slot in config.get("slots", {}).items():
                    if isinstance(slot, (list, tuple)) and not slot:
                        raise SpecError("E_INVALID_SPEC", f"text slot {slot_name!r} must have at least one value", f"tables.{table_name}.columns.{column}")
                    if isinstance(slot, dict) and ("values" in slot or "choices" in slot):
                        choices = slot.get("values", slot.get("choices"))
                        if not isinstance(choices, list) or not choices:
                            raise SpecError("E_INVALID_SPEC", f"text slot {slot_name!r} choices must be a non-empty list", f"tables.{table_name}.columns.{column}")
                    if isinstance(slot, str) and slot.startswith("column:"):
                        ref = slot.partition(":")[2]
                        if ref not in columns:
                            raise SpecError("E_INVALID_SPEC", f"text slot {slot_name!r} references missing column {ref!r}", f"tables.{table_name}.columns.{column}")
                        same_table_dependencies[column].add(ref)
                    elif isinstance(slot, dict) and "column" in slot and slot["column"] not in columns:
                        raise SpecError("E_INVALID_SPEC", f"text slot {slot_name!r} references missing column {slot['column']!r}", f"tables.{table_name}.columns.{column}")
                    elif isinstance(slot, dict) and "column" in slot:
                        same_table_dependencies[column].add(slot["column"])
            elif kind == "foreign_key":
                parent, parent_column = config["table"], config["column"]
                if parent not in normalized or parent_column not in normalized[parent]["columns"]:
                    raise SpecError("E_BROKEN_FOREIGN_KEY", f"reference {parent}.{parent_column} does not exist", f"tables.{table_name}.columns.{column}")
                if normalized[parent]["rows"] == 0 and table["rows"] > 0:
                    raise SpecError("E_BROKEN_FOREIGN_KEY", f"parent table {parent!r} has no rows", f"tables.{table_name}.columns.{column}")
                dependencies[table_name].add(parent)

        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(column: str) -> None:
            if column in visiting:
                raise SpecError("E_INVALID_SPEC", "cyclic same-table column dependency", f"tables.{table_name}.columns.{column}")
            if column in visited:
                return
            visiting.add(column)
            for dependency in same_table_dependencies[column]:
                visit(dependency)
            visiting.remove(column)
            visited.add(column)

        for column in columns:
            visit(column)

        minimum, ranges, pairs = _constraints_for(table)
        domains = domains_by_table[table_name]
        for pair in pairs:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                for column in pair:
                    if column in columns:
                        kind = _column_type(columns[column], f"tables.{table_name}.columns.{column}")
                        if kind not in {"categorical", "integer", "date", "datetime", "foreign_key"} or domains[column] is None:
                            raise SpecError("E_UNSATISFIABLE_DIVERSITY", "pairwise coverage requires independent finite categorical, integer, date, or datetime columns", f"tables.{table_name}.constraints.pairwise")
        for column, required in minimum.items():
            cpath = f"tables.{table_name}.constraints.min_distinct.{column}"
            if column not in columns:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"column {column!r} does not exist", cpath)
            try:
                count = int(required)
                if isinstance(required, bool) or count != required:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", "min_distinct must be a positive integer", cpath) from exc
            if count < 1 or count > table["rows"]:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"min_distinct {count} cannot be met by {table['rows']} rows", cpath)
            domain = domains[column]
            if domain is not None and count > len(domain):
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"min_distinct {count} exceeds the {len(domain)} available values", cpath)
        for column, raw_bins in ranges.items():
            cpath = f"tables.{table_name}.constraints.range_coverage.{column}"
            if column not in columns:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"column {column!r} does not exist", cpath)
            bins = raw_bins.get("bins") if isinstance(raw_bins, dict) else raw_bins
            if not isinstance(bins, int) or isinstance(bins, bool) or bins < 1 or bins > table["rows"]:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"range bins must be between 1 and row count {table['rows']}", cpath)
            config = columns[column]
            kind = _column_type(config, cpath)
            if kind not in {"uniform", "integer", "normal", "date", "datetime"}:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", "range coverage requires an ordered range generator", cpath)
            domain = domains[column]
            if domain is not None and len(domain) < bins:
                raise SpecError("E_UNSATISFIABLE_DIVERSITY", f"{bins} bins cannot be covered by {len(domain)} available values", cpath)
        pairwise_schedules[table_name] = _pairwise_schedule(pairs, domains, table["rows"], f"tables.{table_name}.constraints.pairwise")

    # Stable topological order. Cyclic joins cannot be generated without an explicit seed table.
    order: list[str] = []
    remaining = {name: set(parents) for name, parents in dependencies.items()}
    while remaining:
        ready = [name for name in normalized if name in remaining and not remaining[name]]
        if not ready:
            raise SpecError("E_BROKEN_FOREIGN_KEY", "foreign-key dependency cycle detected", "tables")
        for name in ready:
            order.append(name)
            remaining.pop(name)
        for parents in remaining.values():
            parents.difference_update(ready)
    spec = {**spec, "tables": normalized}
    return BuildPlan(spec=spec, spec_sha256=spec_sha256, table_order=tuple(order), pairwise_schedules=pairwise_schedules, finite_domains=domains_by_table, forced_min_distinct=forced_min_distinct)


def compile_file(path: str | Path) -> BuildPlan:
    spec, digest = load_spec(path)
    return compile_spec(spec, digest)

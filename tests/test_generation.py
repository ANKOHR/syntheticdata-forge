import hashlib
import json

from sdforge.generation import generate
from sdforge.io import write_tables


def _file_bytes(directory, name):
    return (directory / name).read_bytes()


def test_same_spec_and_seed_produce_byte_identical_artifacts(retail_plan, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    data_a, dedup_a = generate(retail_plan, 1234)
    data_b, dedup_b = generate(retail_plan, 1234)
    write_tables(first, retail_plan, data_a, dedup_a, 1234)
    write_tables(second, retail_plan, data_b, dedup_b, 1234)
    for name in ("accounts.csv", "accounts.jsonl", "orders.csv", "orders.jsonl", "manifest.json"):
        assert _file_bytes(first, name) == _file_bytes(second, name)


def test_dedup_reports_exact_duplicate_count():
    from sdforge.compiler import compile_spec

    plan = compile_spec({"seed": 7, "tables": {"repeated": {"rows": 5, "columns": {"marker": {"type": "categorical", "values": ["same"]}}}}}, "abc")
    rows, dedup = generate(plan)
    assert len(rows["repeated"]) == 1
    assert dedup["repeated"] == {"input_rows": 5, "output_rows": 1, "dropped_rows": 4}


def test_manifest_records_hash_seed_version_and_row_counts(retail_plan, tmp_path, retail_spec):
    import json

    from sdforge import GENERATOR_VERSION

    data, dedup = generate(retail_plan, 91)
    write_tables(tmp_path, retail_plan, data, dedup, 91)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(retail_spec.read_bytes()).hexdigest()
    assert manifest["spec_sha256"] == digest
    assert manifest["seed"] == 91
    assert manifest["generator_version"] == GENERATOR_VERSION
    assert manifest["row_counts"] == {"accounts": 8, "orders": 16}


def test_foreign_key_rows_reference_existing_parent_values(retail_plan):
    data, _ = generate(retail_plan, 4)
    parent_ids = {row["account_id"] for row in data["accounts"]}
    assert all(row["account_id"] in parent_ids for row in data["orders"])


def test_foreign_key_minimum_distinct_forces_parent_key_domain():
    from sdforge.compiler import compile_spec
    from sdforge.quality import evaluate_quality

    spec = {"tables": {
        "parent": {"rows": 2, "columns": {"key": {"type": "categorical", "values": ["a", "b"]}}},
        "child": {"rows": 2, "columns": {
            "parent_key": {"type": "foreign_key", "table": "parent", "column": "key", "diversity": {"min_distinct": 2}},
        }},
    }}
    plan = compile_spec(spec)
    data, _ = generate(plan)
    assert {row["key"] for row in data["parent"]} == {"a", "b"}
    assert {row["parent_key"] for row in data["child"]} == {"a", "b"}
    check = evaluate_quality(plan, data)["tables"]["child"]["constraints"][0]
    assert check["passed"] is True


def test_pairwise_coverage_and_deterministic_transform(retail_plan):
    data, _ = generate(retail_plan, 2)
    accounts = data["accounts"]
    observed = {(row["age"], row["segment"]) for row in accounts}
    assert observed == {(age, segment) for age in range(18, 22) for segment in ("standard", "premium")}
    assert all(row["next_year_age"] == row["age"] + 1 for row in accounts)


def test_three_column_pairwise_cover_meets_all_marked_pairs():
    from sdforge.compiler import compile_spec

    spec = {"tables": {"t": {
        "rows": 4,
        "columns": {name: {"type": "categorical", "values": [0, 1]} for name in ("a", "b", "c")},
        "constraints": {"pairwise": [["a", "b"], ["a", "c"], ["b", "c"]]},
    }}}
    plan = compile_spec(spec)
    data, _ = generate(plan)
    rows = data["t"]
    for left, right in (("a", "b"), ("a", "c"), ("b", "c")):
        assert {(row[left], row[right]) for row in rows} == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_provenance_columns_are_embedded(retail_plan):
    data, _ = generate(retail_plan, 27)
    row = data["accounts"][0]
    assert row["_spec_sha256"] == retail_plan.spec_sha256
    assert row["_seed"] == 27
    assert row["_generator_version"] == "1.0.0"


def test_normal_transform_text_and_datetime_generators_are_supported():
    from sdforge.compiler import compile_spec

    spec = {"seed": 2, "tables": {"t": {"rows": 3, "columns": {
        "x": {"type": "normal", "mean": 10, "stddev": 2},
        "x_copy": {"type": "transform", "base": "x", "scale": 2, "offset": 1, "round": 3},
        "label": {"type": "text", "template": "v={x}", "slots": {"x": {"column": "x"}}},
        "moment": {"type": "datetime", "start": "2024-01-01T00:00:00", "end": "2024-01-02T00:00:00"},
    }}}}
    plan = compile_spec(spec)
    rows, _ = generate(plan)
    assert len(rows["t"]) == 3
    assert all(row["x_copy"] == round(row["x"] * 2 + 1, 3) for row in rows["t"])
    assert all(row["label"] == f"v={row['x']}" for row in rows["t"])
    assert all(row["moment"].startswith("2024-01-") for row in rows["t"])


def test_diversity_on_correlated_column_is_met_without_breaking_transform():
    from sdforge.compiler import compile_spec
    from sdforge.quality import evaluate_quality

    spec = {"tables": {"t": {"rows": 3, "columns": {
        "doubled": {"type": "transform", "base": "base", "scale": 2, "diversity": {"min_distinct": 3}},
        "base": {"type": "integer", "min": 1, "max": 3},
    }}}}
    plan = compile_spec(spec)
    data, _ = generate(plan)
    assert [row["doubled"] for row in data["t"]] == [row["base"] * 2 for row in data["t"]]
    check = evaluate_quality(plan, data)["tables"]["t"]["constraints"][0]
    assert check["passed"] is True


def test_date_pairwise_coverage_respects_formatted_output():
    from sdforge.compiler import compile_spec
    from sdforge.quality import evaluate_quality

    spec = {"tables": {"t": {
        "rows": 4,
        "columns": {
            "day": {"type": "date", "start": "2024-01-01", "end": "2024-01-02", "format": "%Y/%m/%d"},
            "kind": {"type": "categorical", "values": ["a", "b"]},
        },
        "constraints": {"pairwise": [["day", "kind"]]},
    }}}
    plan = compile_spec(spec)
    data, _ = generate(plan)
    check = evaluate_quality(plan, data)["tables"]["t"]["constraints"][0]
    assert check["passed"] is True

import pytest

from sdforge.compiler import compile_spec
from sdforge.errors import SpecError


def test_unsatisfiable_distinct_constraint_has_named_error():
    spec = {"tables": {"t": {"rows": 3, "columns": {"x": {"type": "categorical", "values": ["only"], "diversity": {"min_distinct": 2}}}}}}
    with pytest.raises(SpecError, match="E_UNSATISFIABLE_DIVERSITY") as exc:
        compile_spec(spec)
    assert exc.value.code == "E_UNSATISFIABLE_DIVERSITY"


def test_unknown_generator_has_named_error():
    spec = {"tables": {"t": {"rows": 2, "columns": {"x": {"type": "magic"}}}}}
    with pytest.raises(SpecError, match="E_UNKNOWN_GENERATOR"):
        compile_spec(spec)


def test_broken_foreign_key_has_named_error():
    spec = {"tables": {"children": {"rows": 2, "columns": {"parent": {"type": "foreign_key", "table": "missing", "column": "id"}}}}}
    with pytest.raises(SpecError, match="E_BROKEN_FOREIGN_KEY"):
        compile_spec(spec)


def test_invalid_distribution_has_named_error():
    spec = {"tables": {"t": {"rows": 2, "columns": {"x": {"type": "uniform", "min": 3, "max": 1}}}}}
    with pytest.raises(SpecError, match="E_INVALID_DISTRIBUTION"):
        compile_spec(spec)


def test_pairwise_domain_too_large_fails_before_generation():
    spec = {"tables": {"t": {"rows": 3, "columns": {"a": {"type": "categorical", "values": [1, 2]}, "b": {"type": "categorical", "values": ["x", "y"]}}, "constraints": {"pairwise": [["a", "b"]]}}}}
    with pytest.raises(SpecError, match="E_UNSATISFIABLE_DIVERSITY"):
        compile_spec(spec)


def test_compiler_finds_smaller_pairwise_covering_array():
    spec = {"tables": {"t": {
        "rows": 4,
        "columns": {name: {"type": "categorical", "values": [0, 1]} for name in ("a", "b", "c")},
        "constraints": {"pairwise": [["a", "b"], ["a", "c"], ["b", "c"]]},
    }}}
    plan = compile_spec(spec)
    assert len(plan.pairwise_schedules["t"]) == 4


def test_constant_numeric_domain_cannot_meet_min_distinct():
    spec = {"tables": {"t": {"rows": 3, "columns": {
        "x": {"type": "uniform", "min": 2.5, "max": 2.5, "diversity": {"min_distinct": 2}},
    }}}}
    with pytest.raises(SpecError, match="E_UNSATISFIABLE_DIVERSITY"):
        compile_spec(spec)


def test_zero_weight_category_is_not_counted_as_a_reachable_value():
    spec = {"tables": {"t": {"rows": 2, "columns": {
        "x": {"type": "categorical", "values": {"reachable": 1, "unreachable": 0}, "diversity": {"min_distinct": 2}},
    }}}}
    with pytest.raises(SpecError, match="E_UNSATISFIABLE_DIVERSITY"):
        compile_spec(spec)


def test_foreign_key_tables_are_ordered_parent_first():
    spec = {"tables": {
        "child": {"rows": 2, "columns": {"parent_id": {"type": "foreign_key", "table": "parent", "column": "id"}}},
        "parent": {"rows": 2, "columns": {"id": {"type": "integer", "min": 1, "max": 2}}},
    }}
    plan = compile_spec(spec)
    assert plan.table_order == ("parent", "child")


def test_cyclic_column_dependencies_fail_during_compilation():
    spec = {"tables": {"t": {"rows": 1, "columns": {
        "a": {"type": "transform", "base": "b"},
        "b": {"type": "transform", "base": "a"},
    }}}}
    with pytest.raises(SpecError, match="E_INVALID_SPEC"):
        compile_spec(spec)

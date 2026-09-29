from sdforge.compiler import compile_spec
from sdforge.quality import evaluate_quality, marginal
from sdforge.reports import HONESTY_BANNER, render_html, render_markdown


def test_hand_computed_categorical_marginal_and_reference_distance():
    plan = compile_spec({"tables": {"tiny": {"rows": 4, "columns": {"color": {
        "type": "categorical", "values": ["red", "blue"],
        "reference": {"distribution": {"red": 0.5, "blue": 0.5}},
    }}}}})
    report = evaluate_quality(plan, {"tiny": [{"color": "red"}, {"color": "red"}, {"color": "blue"}, {"color": "blue"}]})
    detail = report["tables"]["tiny"]["columns"]["color"]
    assert detail["marginal"] == {"count": 4, "frequencies": {"blue": 0.5, "red": 0.5}, "distinct": 2}
    assert detail["reference_comparison"]["distance"] == 0


def test_numeric_marginal_matches_hand_calculation():
    summary = marginal([{"value": 1}, {"value": 3}], {"reference": {}}, "integer")
    assert summary == {"count": 2, "mean": 2.0, "stddev": 1.0, "min": 1.0, "max": 3.0}


def test_constraint_table_marks_deliberate_violation_failed():
    plan = compile_spec({"tables": {"tiny": {"rows": 2, "columns": {
        "color": {"type": "categorical", "values": ["red", "blue"], "diversity": {"min_distinct": 2}},
    }}}})
    report = evaluate_quality(plan, {"tiny": [{"color": "red"}, {"color": "red"}]})
    check = report["tables"]["tiny"]["constraints"][0]
    assert check["name"] == "min_distinct:color"
    assert check["observed"] == 1
    assert check["passed"] is False


def test_range_coverage_marks_missing_bin_failed():
    plan = compile_spec({"tables": {"tiny": {"rows": 4, "columns": {
        "score": {"type": "integer", "min": 0, "max": 9, "diversity": {"range_coverage": {"bins": 2}}},
    }}}})
    report = evaluate_quality(plan, {"tiny": [{"score": 1}, {"score": 2}]})
    check = report["tables"]["tiny"]["constraints"][0]
    assert check["passed"] is False
    assert check["observed"] == 1


def test_generated_normal_range_coverage_is_enforced():
    from sdforge.generation import generate

    plan = compile_spec({"seed": 87, "tables": {"tiny": {"rows": 12, "columns": {
        "sample": {"type": "normal", "mean": 0, "stddev": 1, "diversity": {"range_coverage": {"bins": 4}}},
    }}}})
    data, _ = generate(plan)
    check = evaluate_quality(plan, data)["tables"]["tiny"]["constraints"][0]
    assert check["passed"] is True
    assert check["observed"] == 4


def test_reports_include_honesty_boundary_and_html_banner(retail_plan):
    report = evaluate_quality(retail_plan, {"accounts": [], "orders": []})
    assert HONESTY_BANNER in render_markdown(report)
    html = render_html(report)
    assert "SYNTHETIC DATA" in html
    assert "not real user data" in html

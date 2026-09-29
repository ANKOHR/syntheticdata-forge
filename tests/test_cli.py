import json
import subprocess
import sys


def _run(*args):
    return subprocess.run([sys.executable, "-m", "sdforge", *map(str, args)], capture_output=True, text=True)


def test_build_and_quality_cli_smoke(retail_spec, tmp_path):
    output = tmp_path / "built"
    build = _run("build", retail_spec, "--out", output, "--seed", 501)
    assert build.returncode == 0, build.stderr
    assert (output / "accounts.csv").exists()
    assert (output / "orders.jsonl").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 501
    quality = _run("quality", retail_spec, "--input", output)
    assert quality.returncode == 0, quality.stderr
    assert (output / "quality-report.md").exists()
    assert (output / "quality-report.html").exists()
    assert "SYNTHETIC DATA" in (output / "quality-report.html").read_text(encoding="utf-8")


def test_cli_reports_named_validation_error(retail_spec, tmp_path):
    bad_spec = tmp_path / "bad.yaml"
    bad_spec.write_text("tables:\n  t:\n    rows: 1\n    columns:\n      value:\n        type: not_a_generator\n", encoding="utf-8")
    result = _run("build", bad_spec, "--out", tmp_path / "bad-output")
    assert result.returncode == 2
    assert "E_UNKNOWN_GENERATOR" in result.stderr


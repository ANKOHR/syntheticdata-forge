"""Command line interface for SyntheticData Forge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import GENERATOR_VERSION
from .compiler import compile_file
from .errors import SpecError
from .generation import generate
from .io import read_tables, write_tables
from .quality import evaluate_quality
from .reports import write_reports


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdforge", description="Mint deterministic synthetic fixture datasets from YAML specs.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="compile a spec and generate CSV, JSONL, manifest and reports")
    build.add_argument("spec", type=Path)
    build.add_argument("--out", type=Path, help="output directory; defaults to <spec>_output")
    build.add_argument("--seed", type=int, help="override the seed in the spec")
    quality = subparsers.add_parser("quality", help="evaluate existing CSV tables against their YAML spec")
    quality.add_argument("spec", type=Path)
    quality.add_argument("--input", "--data", dest="input_dir", type=Path, required=True, help="directory containing <table>.csv files")
    quality.add_argument("--out", type=Path, help="report directory; defaults to the input directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = compile_file(args.spec)
        if args.command == "build":
            out = args.out or args.spec.with_suffix("").parent / f"{args.spec.stem}_output"
            seed = int(args.seed if args.seed is not None else plan.spec.get("seed", 0))
            data, dedup = generate(plan, seed)
            manifest = write_tables(out, plan, data, dedup, seed)
            quality_report = evaluate_quality(plan, data)
            write_reports(out, quality_report)
            print(f"Built synthetic fixture tables in {out}")
            print(f"Generator version: {GENERATOR_VERSION}; seed: {seed}; spec SHA-256: {plan.spec_sha256}")
            print("All output is synthetic, rule-generated fixture data.")
            print(json.dumps(manifest["row_counts"], sort_keys=True))
            return 0
        data = read_tables(args.input_dir, plan)
        report = evaluate_quality(plan, data)
        out = args.out or args.input_dir
        md, html = write_reports(out, report)
        failed = sum(not check["passed"] for table in report["tables"].values() for check in table["constraints"])
        print(f"Quality report written to {md} and {html}; failed constraints: {failed}")
        return 0
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"E_BUILD: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


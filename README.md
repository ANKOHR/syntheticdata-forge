# SyntheticData Forge

SyntheticData Forge (`sdforge`) compiles a YAML specification and creates deterministic synthetic fixture datasets. The output includes CSV and JSONL tables, a provenance manifest, and Markdown and HTML quality reports.

## Quickstart

Requires Python 3.10 or later. Install the package and its test tools:

```console
python -m pip install -e ".[test]"
sdforge build examples/retail.yaml --out build/retail
sdforge quality examples/retail.yaml --input build/retail
```

The `build` command also writes the quality reports. If `--out` is omitted, artifacts are written to `<spec-name>_output`. A command-line `--seed` overrides the spec seed. Run the complete test suite with `python -m pytest`.

The output directory contains one `<table>.csv` and `<table>.jsonl` file per table, `manifest.json`, `quality-report.md`, and `quality-report.html`. The example spec sets `provenance_columns: true`, so each row includes `_spec_sha256`, `_seed`, and `_generator_version`. The manifest always records these values, row counts, and dedup statistics.

## Architecture

```text
YAML spec bytes
    |
    v
Spec loader and compiler ---- named validation errors
    |                          domains, constraints, foreign keys
    v
Seeded generator ------------ dependency-ordered tables and transforms
    |
    +---- row hash dedup ----- dropped-row counts
    |
    +---- CSV and JSONL ------ manifest with spec SHA, seed, version
    |
    v
Quality evaluator ----------- marginals and constraint results
    |
    +---- Markdown report
    +---- HTML report with synthetic-data banner
```

The compiler validates all table and column declarations, checks references and constraint feasibility, and resolves foreign-key table order before row generation starts. The generator uses Python's seeded `random.Random` and an explicit Box-Muller normal sampler. It writes stable UTF-8 output with LF newlines and no timestamps. Given the same spec bytes, seed, and generator version, tables and manifest are byte-identical.

## HONESTY BOUNDARY

All data is SYNTHETIC, produced by rule-based seeded generators (distributions, templates, deterministic transforms). It is not real user data, not scraped data, and not LLM-generated. The quality evaluation measures constraint satisfaction and statistical similarity to reference statistics, not real-world validity. A generated dataset is a fixture for development and testing, and the CLI output, reports, and README say so plainly.

## Catalog: generators

- `uniform`: floating-point values between inclusive `min` and `max`.
- `normal`: Box-Muller values using `mean` and `stddev` (or `sd`).
- `integer`: inclusive integer `min` and `max`.
- `categorical`: weighted choices using a `values` or `weights` mapping, or a list. A list gives equal weights; list objects may set `value` and `weight`.
- `text`: Python format-string template with declared `slots`. A slot may copy a same-table column, select from `values`, or provide one `value`.
- `date` and `datetime`: seeded values between ISO `start` and `end`; optional `format` controls output text.
- `transform` (alias `correlated`): deterministic `base * scale + offset`, with optional rounding and min/max clipping.
- `foreign_key` (alias `fk`): samples a value from a parent table column. Parent tables are generated first.

## Catalog: diversity constraints

Put constraints under a table's `constraints` mapping, or use a column's `diversity` mapping for per-column minimum distinct values and range bins.

```yaml
constraints:
  min_distinct: {status: 3}
  range_coverage:
    score: {bins: 4}
  pairwise:
    - [status, region]
```

The compiler raises named `E_UNSATISFIABLE_DIVERSITY` errors when a target exceeds rows or the available finite domain. Range coverage accepts numeric, date, and datetime range generators. Pairwise coverage accepts finite categorical, integer, date, and datetime domains, plus foreign-key columns whose parent domain is finite and can be forced to cover the requested keys. The compiler constructs a covering row schedule before generation. Unsupported or undersized requests fail before a row is generated.

## Catalog: provenance and deduplication

`manifest.json` contains `spec_sha256` (the SHA-256 of the exact source bytes), `seed`, `generator_version`, final `row_counts`, and per-table `input_rows`, `output_rows`, and `dropped_rows`. Optional provenance columns are enabled with `provenance_columns: true` or `include_provenance: true`. Deduplication hashes canonical JSON for each row's declared columns and keeps the first occurrence.

## Catalog: quality evaluation and output

The evaluator reports numeric count, mean, population standard deviation, minimum and maximum, or categorical frequencies and distinct count. Add a `reference` mapping to a column to compare its marginal: categorical references use `distribution`, while numeric references may set `mean` and `stddev`. Categorical distance is total variation distance; numeric distance is absolute error per declared statistic. The reports also include a pass/fail row for each diversity constraint. Quality scores describe statistical similarity to the declared reference and constraint outcomes only.

Use `sdforge quality spec.yaml --input OUTPUT_DIR` to evaluate existing CSV tables and write reports to the input directory. The HTML report always displays the synthetic-data banner.

## Catalog: named compiler errors

- `E_UNKNOWN_GENERATOR`: generator type is missing or unsupported.
- `E_INVALID_DISTRIBUTION`: numeric bounds or categorical weights are invalid.
- `E_UNSATISFIABLE_DIVERSITY`: requested coverage cannot be constructed from available rows and generator domains.
- `E_BROKEN_FOREIGN_KEY`: parent table/column is absent, empty when referenced, or participates in a dependency cycle.
- `E_DIVERSITY_COMPLEXITY`: a pairwise domain exceeds the compiler's bounded search size, so the compiler cannot produce a satisfiability proof.
- `E_INVALID_SPEC`: the YAML structure or another declaration is invalid.

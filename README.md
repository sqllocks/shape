# Shape

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Shape by SQLLocks** — Shape as Code: a portable, executable description of how
data behaves, not merely its schema.

> **Early access.** Profiling is available now; data generation and pipeline
> integration are in progress. See `docs/plans/COMPLETION_PLAN.md`.

Install with `pip install sqllocks-shape`. Python imports use `import shape`, the
command is `shape`, and artifacts use the `.shape` extension.

## Quick start

```python
import shape

p = shape.profile("customers.csv")      # also: Parquet, JSONL, Delta tables, pandas, pyarrow
shape.save(p, "customers.shape")   # the safe capture; capture="full" keeps real values
print(p.summary())                      # small JSON-safe summary per column

result = shape.check(p, {"columns": {"customer_id": {"unique": True, "nullable": False}}})
print(result.passed, result.violations)

drift = shape.diff(shape.load("customers.shape"), shape.profile("customers_next.csv"))
print(drift.drifted, drift.changes)
```

```bash
shape profile customers.csv -o customers.shape --name customers --html report.html --json summary.json
shape check customers.shape contract.json          # exit 1 if the contract fails, 2 if a rule needs
                                                     # a value the safe capture left out
shape diff customers.shape customers_next.shape --fail-on-drift
```

A Delta table can be profiled as it was: `shape profile events/ --version 3 -o v3.shape`, or
`--as-of 2026-06-02T00:00:00Z` for the newest version committed at or before that time (no zone
means UTC); the API is `shape.profile(path, version=3)` and `shape.profile(path, as_of=...)`.
The Delta version and its commit time are recorded in the `.shape` manifest
(`Profile.provenance`, printed by `shape profile` and `shape inspect`), outside the profile body,
so they do not change the content id or a diff. The data of a version must still be in the table
(not vacuumed).

Tables that use **deletion vectors** (Fabric Spark writes them by default) or **column mapping**
cannot be read by the `deltalake` package (it fails on deletion vectors, and for column mapping it
fails or returns null columns, depending on its release). Shape detects them from the table's
protocol and configuration and reads them with DuckDB `delta_scan` instead, after a one-line
`shape: note: ...` on stderr; `Profile.provenance` then also carries `reader: "duckdb"`,
`reader_features` and `fallback_reason`. The fallback is an optional extra:
`pip install 'sqllocks-shape[delta-fallback]'` (DuckDB downloads its `delta` extension on first
use; run `INSTALL delta` once on a machine with network access if yours has none). Without it,
profiling such a table stops with an error that names the feature and the extra. A table
`deltalake` can read is still read by `deltalake`, and both readers give the same profile (same
content id) for it. `--version` and `--as-of` work with the fallback (an `--as-of` time is turned
into a version from the table's history, as for any table). `delta+abfss://` tables (OneLake,
ADLS Gen2) use the same fallback with the credentials the Delta source already resolves.
`shape diff` compares types, null rates, distinct values, category mix, true rates, spread,
quantiles, ranges, patterns and string lengths, with a documented default for each; thresholds can
be set per column and columns ignored (`--ignore`, `--policy`). See `docs/DRIFT.md`, which also
covers `shape generate-drift`, daily data with planted drift and an answer key.

A folder is one table (its files are partitions) unless you pass `--dataset`, which profiles one
table per file, named by the file name: `shape profile data/ --dataset -o data.shape`. A contract
with a `tables` object is checked against such a dataset profile only; against a single table
`shape check` exits 2, and a table of the profile that the contract does not name fails it.

```bash
shape --version
shape inspect customers.shape                        # what a .shape file holds
```

## Version-controlling shapes

A shape can live in git like code: re-profiling unchanged data gives a **byte-identical**
`.shape` (fixed container, no timestamps), so `git status` stays clean, and a real change gives a
readable diff.

```bash
shape git-setup                       # once per repository
shape profile orders.csv -o orders.shape --name orders
git add .gitattributes orders.shape && git commit -m "orders shape"
# next run, after the data changed:
shape profile orders.csv -o orders.shape
git diff                              # one changed line per changed property
```

```diff
-columns.email.null_rate: 0.0474
+columns.email.null_rate: 0.2
-columns.status.cardinality: 3
+columns.status.cardinality: 4
+columns.status.enum_values.lost: 0.2512
```

- `shape git-setup` writes `diff.shape.textconv = shape cat` to the repository's local git
  config and `*.shape diff=shape` to `.gitattributes`; running it again changes nothing.
  `--pattern '*.safe.json'` adds more patterns. `shape cat FILE` prints the same text form
  (one `path: value` line per property, sorted, nothing volatile); `shape inspect --pretty FILE`
  prints it as indented JSON with sorted keys. For drift that is judged rather than listed, use
  `shape diff OLD.shape NEW.shape`.
- **The name is stable.** `--name NAME` sets the profile name. Without it, `-o` over an existing
  profile keeps that profile's name; otherwise the name is the input's file name. Re-profiling
  `orders_w2.csv` over `orders.shape` therefore does not show a rename.
- **What to commit.** The default `.shape` is the safe capture, and it is the artifact to commit:
  a sensitive column keeps statistics and formats only (no values, no raw minimum or maximum),
  and a category is listed only when every released category has at least `k` rows (default 5;
  `--k N`). Check it with `shape profile validate --safe orders.shape` (exit 0 means no leak
  found). The `--json` summary and the HTML report are redacted the same way. Keeping real values
  is an explicit choice, `--capture full`, which is recorded in the file, prints a warning, and
  fails `validate --safe`: commit such a file only to a repository as private as the data. The
  share-safe JSON, `shape profile safe orders.shape -o orders.safe.json`, is still there for the
  stricter form. A registry follows the same rule: `shape registry` takes a safe capture and
  refuses a full one (commit it with `--safe`; `docs/REGISTRY.md`), and `shape profile registry`
  stores safe captures unless you save with `--capture full` (`docs/PROFILE_REGISTRY.md`).
- Signed files (`--sign KEY`) are reproducible too: the signature covers the manifest bytes,
  not the container.

## What's in early access

Profiling, contracts (`check`) and drift (`diff`) are the supported surface, in Python
and in the `shape` CLI. `shape.profile()` reads files, Arrow tables, DataFrames and a list of row
dicts. Generating data from a profile works (`shape.generate(profile, n)`, `shape generate
--from PROFILE.shape`), and `shape plan PROFILE.shape` lists what generation keeps. `shape.query`
and `shape query` read Shape model files, not profiles, and exit 2 for a profile. Other legacy
commands (such as `fidelity`) are experimental and will change.

## What a `.shape` file contains

By default a profile is a **safe capture** (`docs/PRIVACY_MODEL.md`): counts, types, formats,
length distributions, quantiles and bounds for every column, and category values only for columns
that are not sensitive, with every released category at least `k` rows (default 5). A column is
sensitive when you declare it `CONFIDENTIAL` or higher (`--classify COLUMN=LEVEL`) or when it looks
like personal data (an email, SSN or card pattern, or nearly one distinct value per row). With
`--capture full` a profile keeps the 500 most frequent values per column with their counts and each
column's minimum and maximum; treat that file, its HTML report and its JSON summary as you would
the source data. A safe capture is data minimisation, not anonymisation.

## Design principles

- evidence carries provenance and algorithm parameters
- deterministic generation and replay
- offline by default: no network access or cloud service is required
- `.shape` readers fail closed on unsafe or corrupt containers

See `docs/PRODUCT_ARCHITECTURE.md` and `docs/specs/`.

## License

MIT. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.

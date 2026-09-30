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
shape.save(p, "customers.shape")
print(p.summary())                      # small JSON-safe summary per column

result = shape.check(p, {"columns": {"customer_id": {"unique": True, "nullable": False}}})
print(result.passed, result.violations)

drift = shape.diff(shape.load("customers.shape"), shape.profile("customers_next.csv"))
print(drift.drifted, drift.changes)
```

```bash
shape profile customers.csv -o customers.shape --html report.html --json summary.json
shape check customers.shape contract.json          # exit code 1 if the contract fails
shape diff customers.shape customers_next.shape --fail-on-drift
```

```bash
shape --version
shape profile customers.csv --spindle-compat -o customers.profile.json   # Spindle's profile JSON
shape profile capture data/ -o captured.json        # categorical shares only, no rows
shape profile diff old.json new.json --threshold 0.2   # exit 1 when shape drift exceeds it
shape inspect customers.shape                        # what a .shape file holds
```

`shape profile capture` and `shape profile diff` write and print what `spindle profile
capture` and `spindle profile diff` do, byte for byte, for CSV and Parquet input.

## What a `.shape` file contains

A profile keeps real values from your data, as Spindle's profiler does: up to the 500
most frequent values per column with their counts, and each column's minimum and maximum.
Treat a `.shape` file, its HTML report and its JSON summary as you would the source data,
and don't share one from a sensitive table. A privacy-safe profile, with rare values
suppressed, is planned.

## Design principles

- evidence carries provenance and algorithm parameters
- deterministic generation and replay
- offline by default: no network access or cloud service is required
- `.shape` readers fail closed on unsafe or corrupt containers

See `docs/PRODUCT_ARCHITECTURE.md` and `docs/specs/`.

## License

MIT. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.

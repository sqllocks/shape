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
shape inspect customers.shape                        # what a .shape file holds
```

## What's in early access

Profiling, contracts (`check`) and drift (`diff`) are the supported surface, in Python
and in the `shape` CLI. Generating data from a profile is planned but not in this release:
`shape.generate()` raises `NotImplementedError` for a profile, and the legacy CLI commands
`plan` and `query` exit 2 for one. Other legacy commands (such as `generate` and `fidelity`)
are experimental and will change.

## What a `.shape` file contains

A profile keeps real values from your data: up to the 500
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

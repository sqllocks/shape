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

s = shape.profile(rows)
shape.save(s, "customer.shape", name="customer")
again = shape.load("customer.shape")
```

```bash
shape profile customers.csv -o customer.shape
shape inspect customer.shape
```

`.shape` format v1 is a checksummed, content-addressed artifact. Treat artifacts
according to their sensitivity; they may contain value-bearing evidence.

## Design principles

- bounded, mergeable profiling rather than full-data retention
- evidence carries provenance and algorithm parameters
- deterministic generation and replay
- offline by default; network and plugin capabilities are deny-by-default
- `.shape` readers fail closed on unsafe or corrupt containers

See `docs/PRODUCT_ARCHITECTURE.md` and `docs/specs/`.

## License

MIT. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.

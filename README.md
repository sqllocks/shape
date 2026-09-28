# Shape

**Shape by SQLLocks** — Shape as Code.

Install the distribution with `pip install sqllocks-shape`. Python imports remain
`import shape`, the command is `shape`, and artifacts use `.shape`.

**Shape as Code** is a portable, executable description of how data behaves—not merely its schema.

The reference implementation captures statistical and semantic evidence, compares change, evaluates quality and privacy, generates data with relational/geographic fidelity, maintains history, processes event-time streams, and packages evidence into governed `.shape` artifacts.

## Core workflow

```bash
shape doctor
shape capture customers.csv -o customers.shape.json
shape capture customers_next.csv -o customers_next.shape.json
shape diff customers.shape.json customers_next.shape.json
shape key customers.csv customer_id
shape fd customers.csv --determinant zip --dependent state
shape privacy-k customers.csv zip age
shape conformance
```

## Design principles

- bounded/mergeable profiling rather than full-data retention
- evidence carries provenance and algorithm parameters
- deterministic generation and replay
- coherent multi-field domains such as addresses and relationships
- offline-by-default reference assets with explicit licenses/checksums
- sensitivity labels survive capture, history, diff and release
- network/plugin capabilities are deny-by-default
- `.shape` readers fail closed on unsafe or corrupt containers
- external accreditation/certification is never fabricated

See `docs/PRODUCT_ARCHITECTURE.md`, `docs/specs/`, `docs/CLOSURE_MATRIX.md`, and `docs/audit/SPINDLE_SOURCE_AUDIT.md`.

## RC-1 quick start

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

`.shape` format v1 is a checksummed, content-addressed artifact. Treat sensitive Shapes according to their classification; use privacy release/redaction controls before distributing artifacts containing value-bearing evidence.

## Platform capabilities

The RC candidate also includes local-first implementations for Shape Hub/remote registry, domain packages, Shape-aware ETL, distributed profiling/merge, joint-distribution reconstruction, temporal modeling, reference assets, lineage/blast radius, data-test generation, scenarios, observability/alerts, and a web/API service.

See `docs/PLATFORM_12.md` for architecture, API boundaries, performance qualification and the distinction between locally tested implementation and external-service qualification.

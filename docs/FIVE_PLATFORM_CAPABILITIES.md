# Shape RC-1 — Five Platform Capabilities

## 1. Contracts and compatibility
Shape contracts now enforce required columns, kinds/types, null-rate limits, uniqueness, numeric ranges and forbidden columns. Compatibility supports backward, forward and full modes with deterministic machine-readable issues.

CLI:
```bash
shape check customer.shape customer.contract.json
shape compatibility old.shape new.shape --mode backward
```

## 2. Fidelity certificates and reconstruction planning
`plan_reconstruction()` reports which evidence can be preserved and which evidence is unavailable to the current universal compiler before generation starts. `certify_shapes()` compares target and observed Shapes and emits weighted schema, null-behavior, cardinality and numeric-distribution fidelity dimensions plus degraded/unavailable evidence.

CLI:
```bash
shape plan customer.shape
shape certify-shapes target.shape generated.shape
```

## 3. Shape Query
A safe deterministic query parser allows inspection without executing Python/eval:
```text
rows
column("email").null_count
classification("email")
relationship("income","spend").rho
```
The query grammar intentionally does not execute arbitrary code.

## 4. Local Shape Registry
The registry is immutable/content-addressed. It supports commit, checkout, log, tags, named refs and promotion. `latest`, `staging`, `production`, etc. are refs to immutable content IDs.

```bash
shape registry .shape-registry commit customer customer.shape
shape registry .shape-registry tag customer v1
shape registry .shape-registry promote customer v1 production
shape registry .shape-registry checkout customer production output.shape
```

## 5. Domain SDK
The SDK supports validation, composition, extension/inheritance, portable JSON serialization, round-trip loading and row conformance testing. It builds on the versioned DomainDefinition/DomainRegistry and the coherent `us_address` domain.

These are local-first primitives. A future hosted registry/domain repository can implement the same semantics rather than changing Shape's model.

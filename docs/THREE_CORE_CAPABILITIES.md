# Shape — Three Core Capabilities Completed

## 1. Executable Shape reconstruction

`shape.generate()` now compiles portable Shape evidence into columnar synthetic data. It handles numeric marginals, categorical/text heavy-hitter evidence, null rates, declared correlations, conditional distributions and foreign-key constraints. `generate_relational()` generates multiple related tables while preserving declared FK integrity.

The compiler returns a `GenerationReport` listing evidence it preserved and any relationship it had to degrade. This is deliberate: Shape must not silently claim fidelity it could not reproduce.

Example:

```python
data, report = shape.generate(customer_shape, 1_000_000, seed=42)
```

Relationships may be stored in the Shape:

```json
{
  "relationships": {
    "correlations": [{"source":"income","target":"spend","rho":0.82}],
    "foreign_keys": [{"field":"customer_id","parent_count":100000}],
    "conditionals": [{"when":"segment","equals":"A","field":"score","mean":80,"stddev":5}]
  }
}
```

## 2. Historical Shape evolution

`ShapeTimeline` accepts any number of `VersionedShape` objects. It can retrieve an exact/previous/next/interpolated Shape at a time, generate data at a historical point, generate a sequence across a time range, and report drift between consecutive versions.

```python
timeline = ShapeTimeline([
    VersionedShape("v1", 0, january),
    VersionedShape("v2", 31, february),
    VersionedShape("v3", 59, march),
])
series = timeline.generate_range(0, 59, 1, rows_per_step=100_000)
```

This establishes the generalized foundation for recreating changing data behavior from multiple Shapes rather than a single static snapshot.

## 3. First-class domains

Domains now have a portable definition: fields, logical/semantic types, relationships, constraints, name and version. `DomainRegistry` provides registration, version lookup and generation. Domain definitions serialize to/from dictionaries so they can live in source control.

The built-in `us_address@1.0.0` domain declares coherent street/city/county/state/ZIP/latitude/longitude semantics and functional/geographic relationships.

Location scopes accept app/agent-friendly specifications:

```python
scope_from_specs([
    {"zip": "43215"},
    {"city": "Columbus", "state": "OH"},
    {"county": "Franklin", "state": "OH"},
    {"state": "OH"},
])
```

Weighted mixtures and exclusions are supported, and the existing AddressPack supplies reference-backed coherent addresses and coordinates.

## Fidelity rule

These capabilities are executable, but they do not imply mathematically perfect recovery of an unknown original joint distribution. Shape reconstructs the evidence actually captured in the artifact. Evidence that was not captured cannot be recreated without inventing information. The generation report and fidelity certificate are therefore part of the product contract.

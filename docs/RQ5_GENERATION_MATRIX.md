# RQ-5 Generation Feature/Combination Matrix

RQ-5 exercises every implemented public generation primitive plus representative combinations and Pack/LocationScope integration.

## Exercised
Constant, Sequence, weighted Choice, Uniform, Normal, Conditional, Derived, ForeignKey, FirstPerParent, Empirical, CorrelatedNormal, UniqueToken, GenerationPlan sequential/random access, parent-child generation, SCD2, composite keys, vector foreign keys, vector parent-child keys, compiled coherent addresses, categorical/numeric fidelity paths, Person Pack, Address Pack modes, ZIP/city/county/state scopes, multi-location scopes, weighted scopes, excludes, and fail-closed location resolution.

Pairwise/3-way stateless strategy combinations were exercised through generated plans. Relational, geographic, temporal/nonlinear fidelity have separate RQ-3 gauntlets.

## Bugs found
1. **Partitioned address generation was not deterministic.** `start` existed but was ignored, so independently generated partitions could not reproduce a single whole generation.
2. **Compiled address weights were ignored.** The field existed but address generation sampled geography uniformly.

Both bugs are fixed and regression-tested.

## Fix
Address generation now uses a vectorized counter-based SplitMix64-style deterministic stream keyed by `(seed, absolute row index, stream)`. This makes partition output independent of batch boundaries. Weighted geography is sampled from a validated cumulative distribution.

## Performance after correctness fix
Sustained 10M rows in 250k batches:
- address workload: ~1.89M rows/sec
- combined key + FK + address workload: ~1.80M rows/sec
- max RSS: ~219 MB

Correctness fixes therefore retain the >1M rows/sec target.

## Remaining roadblocks / boundaries
- The scalar `AddressPack` is intentionally a reference/semantic implementation and is not the million-row path.
- `FirstPerParent` is stateful: arbitrary `row_at(i)` cannot reproduce sequential first-seen semantics without partition/state context. It must not be advertised as stateless random-access deterministic.
- Production-real address validity still depends on the reference asset; synthetic benchmark geography is not a postal-deliverability claim.
- Exhaustive mathematical testing of every possible strategy composition is impossible. The matrix covers all implemented primitives, representative cross-products, property invariants, and known stateful boundaries; future strategies must add matrix/property cases.

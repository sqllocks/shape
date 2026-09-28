# RQ-4 — Generation at Scale

RQ-4 tests the requested hard workload: keys, foreign keys and coherent address components including city/state/county/postal code/latitude/longitude.

## Architecture
Generation compiles/resolves reference geography before the hot loop. Runtime address generation performs vectorized index sampling against a `CompiledAddressAsset`; it performs no network geocoding. Geographic tuples remain coherent because city/state/county/postal/lat/lon are selected through the same reference-row index.

Composite and foreign keys are generated as contiguous integer arrays. Parent/child keys are structural (`repeat`/`tile`) rather than generated then repaired.

## Local results
Single 1M-row batch:
- composite keys: ~21.3M rows/sec
- foreign keys: ~54.6M rows/sec
- coherent address components: ~2.97M rows/sec
- combined composite + FK + address workload: ~0.94M rows/sec

The one-batch combined result is slightly below 1M/sec, so it is not counted as passing the sustained requirement by itself.

Sustained 10M-row test in 250k-row batches:
- coherent address components: ~1.93M rows/sec
- combined composite + FK + coherent address components: ~1.67M rows/sec
- max RSS observed: ~212 MB

Therefore the requested **sustained 1M generated rows/sec** threshold is exceeded on this host for the tested combined columnar workload.

## Important boundary
The address benchmark generates coherent address *components* using a compiled synthetic reference asset. It does not perform live geocoding and does not claim every generated street/city/coordinate tuple corresponds to a deliverable real-world postal address. Production address fidelity depends on the licensed/reference address asset supplied to the compiled generator. Formatting a single concatenated address string can add cost and is intentionally not required in the hot kernel.

These are local benchmark results, not portable hardware guarantees.

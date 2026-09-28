# Shape GA worst-case generation qualification

The GA generation gate deliberately combines the expensive features rather than benchmarking them independently:

- three-part composite primary keys;
- three-part composite foreign keys with skewed parent selection;
- coherent reference-backed addresses;
- latitude/longitude;
- weighted multi-ZIP `LocationScope`;
- correlated/joint numeric generation;
- temporal event generation;
- scenario-mutated parameters;
- classification/release-policy evaluation;
- deterministic replay;
- FK integrity, geographic validity, finite coordinates, correlation and temporal-order correctness checks.

The hot generation path parallelizes independent domains while retaining deterministic per-domain seeds. Correctness assertions are run for every generated chunk but excluded from the throughput timer.

## Results in this environment
- 1,000,000 rows: **2.872M rows/sec median** across three runs; 1M/sec gate passed.
- 10,000,000 rows: **3.575M rows/sec median** across three runs; 1M/sec gate passed.
- Deterministic replay: passed.
- Peak RSS observed during 10M qualification: about **502 MiB**.

A 100M attempt could not be completed inside this interactive execution window, so no 100M or 1B performance claim is made. The generator is chunked specifically so those scale qualifications can run with bounded memory on a long-running release host.

# Platform 12 qualification report

## Functional qualification
- Full local suite: **163 passed**, one expected duplicate-ZIP-member warning from artifact-corruption hardening.
- Requirement registry: **73/73 valid**.
- Basic secret scan: **passed**.
- Python compile gate: **passed**.
- Existing streaming randomized fuzz: **100 trials, 0 failures**.

## Core regression performance
- Text + semantic profiling, 1M rows: **1,971,679 rows/sec median**.
- Full mixed evidence, 5M rows: **1,421,381 rows/sec median over three runs**.

## Twelve-capability performance run
- Hub protocol: 432,820 ops/sec.
- Domain package publication: 1,630 packages/sec.
- Shape-aware ETL: 910,318 input rows/sec, including before/after evidence capture.
- Distributed profiling/merge: 1,586,796 rows/sec.
- Joint numeric generation: 3,320,144 rows/sec.
- Temporal generation: 10,822,699 timestamps/sec.
- Reference indexing: 607,982 records/sec.
- Lineage traversal: 1,155,687 nodes/sec.
- Test generation: 24,692,559 generated rows/sec.
- Scenario generation: 23,459,181 rows/sec.
- Metrics: 1,019,428 updates/sec.
- Built-in local HTTP API: 326 sequential requests/sec.
- Peak RSS during combined platform benchmark: **205.3 MiB**.

Every explicit performance gate passed.

## Defect found by performance qualification
The initial deep-lineage implementation used recursive cycle checking and failed on a 10,000-node chain. Qualification exposed it. The implementation was replaced with iterative reachability/cycle detection and now handles the deep-graph test without Python recursion limits.

ETL and distributed profiling were also accelerated with an automatic stable-schema columnar path. In the final qualification they exceeded their 500K rows/sec gates.

## Meaning of “fully tested”
This report means the available implementation has functional, hardening, randomized and performance qualification in this environment. It does **not** mean every possible deployment/environment has been tested. Hosted/cloud integrations still require real external environments for production qualification: deployed Shape Hub infrastructure, Airflow/Dagster/dbt, Spark/Flink, Fabric/ADF, external alert providers, production web servers, Kafka/Event Hubs, PyArrow, cross-OS package installation and release supply-chain infrastructure.

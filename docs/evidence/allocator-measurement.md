# Allocator policy — committed product A/B measurement

Status: available.

These are recorded local runs. Commands and results are historical evidence, not commands executed by the docs harness.

Captured **2026-10-12 UTC**, Shape **0.9.1**, product base `8d10aa0a768383a947fa77d9547b066f8cfbd9cc`. The [raw measurement](allocator-measurement.json) records the complete machine label, load before every run, per-table row counts, content hashes, Arrow backend and every sample. The [reproduction script](measure_allocator.py) imports no Shape in the parent, uses fresh processes and the repository benchmark lock/quiet-load helper, and uses no network or live service.

Machine label: `owner-notes-2-linux-x86_64-2026-10-12`; **INTEL(R) XEON(R) PLATINUM 8573C**, five visible logical/affinity CPUs, cgroup quota **4 CPU**, visible RAM **18,440,136 kB**, cgroup limit **16 GiB**, Linux **6.18.44**, Python **3.12.14**, PyArrow **25.0.1**, THP **madvise**. This is a shared cloud environment, not a promise about any deployment.

Recorded command (the script now lives at [docs/evidence/measure_allocator.py](measure_allocator.py); the path below is retained from the original run):

```bash
python docs/plans/evidence/owner-notes/measure_allocator.py /tmp/owner-notes2-allocator.json
```

Workload: committed retail schema, **medium**, seed **42**, **1,965,400 rows in nine tables**. Schema SHA-256 is recorded in the raw machine record. Four cells cover both Shape-before-PyArrow and PyArrow-before-Shape, each with policy enabled and `SHAPE_MEMORY_POOL=default`. Named pool overrides are cleared only for each benchmark child. One warm-up per cell is excluded; three measured repetitions per cell are interleaved. Generation wall time excludes imports, schema loading and output hashing. Peak RSS is process peak through generation (includes imports), sampled before hashing. It is not a wall-time measurement of Python import or of the first mimalloc allocation.

| Import order | Setting | Median generation seconds | Median peak process RSS MiB |
|---|---|---:|---:|
| shape-first | Shape policy | 0.369247 | 293.53 |
| shape-first | SHAPE_MEMORY_POOL=default | 0.323949 | 322.20 |
| pyarrow-first | Shape policy | 0.337122 | 294.55 |
| pyarrow-first | SHAPE_MEMORY_POOL=default | 0.357260 | 327.27 |

All 12 measured runs have identical generated Arrow IPC bytes and per-table row counts. Python exit: **0**. The quiet-load gate is checked at 1.5 and no gate is overridden.

These are observed generation/RSS measurements for this machine and workload only. They do not establish Python import time or the first-allocation latency. The implementation’s allocator selection is described separately in the [generation engine](../GENERATION_ENGINE.md).

## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)

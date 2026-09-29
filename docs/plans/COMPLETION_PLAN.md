# Shape completion plan

Status: **approved build specification** · Supersedes `docs/plans/REMAINING_WORK.md` and
`docs/plans/NEXT_WORK_PACKETS.md`

This document is the single source of truth for finishing Shape. Every product and
technical decision is settled in §2. The work is split into numbered work packages (§6),
and each one has dependencies, deliverables and pass/fail acceptance criteria. Building
every work package to its acceptance criteria, and passing every phase gate, completes
the project. Nothing here is left open for discussion. The only items needing outside
help are the owner actions in §8, and each of those has a defined fallback, so none of
them blocks completion.

Contents:
1. How to execute this plan
2. Decisions
3. Measured baselines and targets
4. Target architecture
5. Target repository layout
6. Work packages and phase gates
7. Keep / cut
8. Owner actions (external)
9. Spindle CLI parity map
10. Status tracker

Appendix A: Verified bug register

---

## 1. How to execute this plan

### 1.1 Order

```
Phase 0 ─► Phase 1 ─► Phase 2 ─┬─► Phase 3 ─┐
                  │            └─► Phase 4 ─┼─► Phase 5 ─► Phase 6 ─► Phase 8
                  └────────────────► Phase 7 ┘
```

- Phases 0, 1 and 2 are strictly sequential.
- Phases 3 and 4 may run in parallel after Phase 2.
- Phase 5 needs both Phase 3 and Phase 4.
- Phase 7 needs Phase 1 only, and may run at any point after it.
- Phase 6 needs Phases 4 and 5.
- Phase 8 needs every other phase.
- Within a phase, the "Depends" field of each work package gives its order.

### 1.2 Definition of done for a work package

A work package is done only when all of the following are true:

1. Every acceptance criterion passes, both locally and in CI.
2. `ruff check`, `ruff format --check`, `mypy` (strict for every module the work
   package creates or rewrites) and `pytest` are green.
3. Every bug ID the work package lists has a regression test. The test is written
   first, fails before the fix and passes after it.
4. The benchmark harness shows no regression greater than 5% on any tracked number.
   This applies once the harness exists (from P0-07 onward).
5. User-facing docs touched by the change are updated in the same PR.
6. The status tracker (§10) is updated in the same PR: the status, and the PR or commit
   reference.

### 1.3 Branches and PRs

- Each work package is one PR. A tightly coupled set of work packages may share a PR, as
  long as the PR lists every ID it covers.
- The PR targets `main`.
- PR titles start with the work-package ID, for example `P1-04: sketches (HLL, KLL,
  SpaceSaving)`.
- Commit messages follow the repo's existing style.

### 1.4 Rules that are never broken

- **Equivalence before timing.** No performance number counts until the equivalence
  verifier for that workload passes.
- **Never lower a gate, and never loosen a verifier's tolerance, to get green.** If a
  gate is missed, follow §1.5.
- **Never skip, disable or quarantine a test.** Never mark a check as passed without
  running it. Never commit generated "qualification" or "evidence" files that are not
  produced by the harness in this repo.
- **No claim without enforcement.** No doc, README, CHANGELOG or docstring statement
  about performance, fidelity, privacy or security ships unless a CI test or benchmark
  enforces it.

### 1.5 When a performance gate is missed

1. Profile the workload with `py-spy record --native` and `perf`, and identify the top
   hotspots.
2. Move any per-value or per-row work found in Python into the Rust kernel, following
   §4.2.
3. Re-measure. Repeat steps 1–3 at most twice.
4. If the minimum is still not met, record the measured numbers and the hotspot
   analysis in the decision log (§2.3) and escalate to the owner. Work continues on
   other work packages in the meantime; the gate itself is not changed.

---

## 2. Decisions

Every decision below is final unless the owner changes it. Record any change in §2.3.

### 2.1 Product decisions

| ID | Decision | Rationale |
|---|---|---|
| D-01 | **Shape replaces Spindle completely.** That covers profiling, generation (all domains, strategies, chaos, simulation, incremental, transforms), Fabric integration and the MCP bridge. | Owner decision. |
| D-02 | **Streaming works in both directions.** Shape profiles streams it consumes (Kafka, Event Hubs) and emits streams during generation (Kafka, Event Hubs, Fabric Eventstream, Eventhouse, file, console). | Owner decision. |
| D-03 | **Features are plugins built on core features.** A small core exposes stable extension points, and first-party features use the same API as third parties. | Owner decision. |
| D-04 | **Performance: 10x Spindle is the minimum and 30x is the stretch goal, for both profiling and generation,** always measured 1:1 on equivalent work (§3). | Owner decision. |
| D-05 | **Realism goes beyond Spindle.** All of Spindle's distributions and temporal patterns, plus the additional families, mixtures, 80/20 helpers, date-specific holiday calendars, payday effects and trends in P4-05. | Owner decision. |
| D-06 | **Spindle profiler parity is a hard requirement:** distribution fitting, pattern detection, enum detection, and PK/FK detection including across tables, plus every other field of Spindle's `ColumnProfile` and `TableProfile`. | Owner decision. |
| D-07 | **Remove differential privacy.** Delete `privacy/advanced.laplace` and every DP claim. Keep k-anonymity, suppression, redaction, minimum-cohort enforcement and Spindle's "safe profile". | Spindle has no DP, and the current DP is fake (SEC1). Correct DP with budget accounting is a separate product, and nothing requires it. |
| D-08 | **Cut the hub, the web app and the 20 other unused or stub modules** listed in §7. Remote access is provided by the MCP plugin. | Not in Spindle; no dependents; security bugs. |
| D-09 | **Plugins are trusted, in-process code.** The subprocess "capability sandbox" is deleted along with its isolation claims. | The owner wants features as plugins built on core, not untrusted code execution. The current sandbox is cosmetic (PL1–PL4). |
| D-10 | **Spindle's reference data** (name pools, ZIP locations, domain reference files) is copied into the `shape-domains` plugin, with attribution carried into `THIRD_PARTY_NOTICES.md`. The GeoNames data is CC-BY-4.0, and Spindle's own code is MIT (same copyright holder). | Parity needs the same data, and the licenses allow it. |
| D-11 | **Holiday calendars are rule-based code,** not downloaded data. They cover fixed dates, the nth or last weekday of a month, Easter (computus) and observed-day rules. US federal and US retail calendars ship with core. Other countries are plugins in the `shape.calendars` extension point. | Offline, deterministic, no data license needed. |
| D-12 | **Streaming envelope:** the default is Spindle's `EventEnvelope` format, so existing consumers work unchanged. CloudEvents is available as an option. | Drop-in migration for Spindle users. |
| D-13 | **No `spindle` executable is shipped.** Spindle command names are accepted as aliases by `shape` wherever the meaning matches (§9), and `.spindle.json` schemas are read directly. | Avoids clashing with an installed Spindle. |

### 2.2 Technical decisions

| ID | Decision | Rationale |
|---|---|---|
| T-01 | **Runtime split: Arrow for data format and I/O, Rust for the whole per-batch data path, Python for orchestration and plugins** (§4.2). | Measured: ~7 µs per Python→native call, and Parquet writing becomes ~50% of generation time once generation is fast. Speed needs one fused native call per batch. |
| T-02 | **Rust kernel:** a single crate `rust/shape-kernel`. It is built with pyo3 and maturin as `shape._kernel`, uses abi3 wheels (cp311+), shares data with Python through the Arrow C Data Interface, and runs in parallel with rayon. | One kernel keeps the API small. abi3 cuts the wheel matrix to one wheel per platform. |
| T-03 | **A pure-Python reference implementation is kept for every kernel function** under `src/shape/kernel/reference/`. `SHAPE_KERNEL=auto|rust|python` selects the implementation (the default is `auto`: Rust if importable). Differential tests assert that the two agree. | It is the correctness oracle, and the fallback on platforms without a wheel. |
| T-04 | **Wheels:** Linux x86_64 and aarch64 (manylinux_2_28 and musllinux), macOS x86_64 and arm64, and Windows x86_64, built with cibuildwheel in CI. The sdist builds from source with a Rust toolchain. | Covers developer machines, CI and Fabric runtimes. |
| T-05 | **Build backend:** maturin, in mixed Python/Rust layout, replacing hatchling. | Needed for T-02. |
| T-06 | **Python 3.11–3.14.** CI runs the full matrix on Linux and 3.11 plus 3.14 on macOS and Windows. | 3.11 is the current floor; 3.14 is current. |
| T-07 | **Core dependencies: `numpy` and `pyarrow` only.** Remove `pydantic` (unused) and `typing-extensions`. Move `cryptography` to the `[sign]` extra, loaded lazily. `scipy` is an optional extra that adds more fitting families. | Import time and install size. |
| T-08 | **Extras.** Core `sqllocks-shape` has extras `[sign]`, `[scipy]`, `[kafka]`, `[eventhubs]`, `[fabric]`, `[sqlserver]`, `[domains]`, `[simulation]`, `[mcp]`, `[excel]`, `[delta]` and `[all]`. Each plugin extra pulls in the matching `sqllocks-shape-*` distribution. | Mirrors Spindle's extras pattern. |
| T-09 | **First-party plugins live in this repo** under `plugins/<dist-name>/`, each with its own `pyproject.toml` and version kept in lockstep with core. They are released together. | One CI, one review flow, and atomic API changes. |
| T-10 | **Package and version.** The distribution is `sqllocks-shape`, which is not yet on PyPI (confirmed 404 on 2026-09-29). The version resets to `0.9.0.devN` during the build. **1.0.0 is released at the end of Phase 8.** | Nothing has been published, so the version can honestly restart. |
| T-11 | **`.shape` format v2 and Shape model v2** with one schema for every path. There is a read-only v1→v2 migrator. v1 writing is removed. | Unpublished, so it can break freely, while the migrator protects local files. |
| T-12 | **Typing:** mypy strict for every new or rewritten module, with a per-module ratchet list in `pyproject.toml`. **All of `src/shape` must be strict by Phase 8.** | Makes `make check` truthful without a big-bang fix. |
| T-13 | **Hashing:** seeded XXH3-64 everywhere, never Python `hash()`. Values are canonicalized first: integers and integral floats hash equal, NaN is excluded, and strings are hashed as UTF-8 bytes. | Deterministic across processes and platforms (S1, P7). |
| T-14 | **Sketches:** HLL with p=14 by default (dense, with bias correction); KLL with k=200 by default; SpaceSaving with capacity 64 by default, bounded, and with a merge that keeps error terms. All are mergeable, associative and commutative, and property-tested. | Bounded, mergeable profiles (P5, P6, P9). |
| T-15 | **Exact versus sketch:** `exact="auto"` computes exact statistics when a column fits the exact budget (default 5M values per column per job) and uses sketches beyond it. The output schema is identical either way, and the method is recorded in `error_models`. | Exact where it's cheap, bounded where it isn't (P12). |
| T-16 | **Generation RNG:** counter-based Philox4x64, keyed by (seed, table, column, chunk). Any row can be generated independently, and the chunk layout never changes results. Bit-identical output to Spindle is **not** required; statistical equivalence under the standard in P4-07 is. | Parallel, deterministic random access (G1, G2). |
| T-17 | **Parquet output:** pyarrow `ParquetWriter` with snappy compression (Spindle's default, so the benchmark is fair) and dictionary encoding enabled. Tables are written in parallel, and writing is pipelined with generation (chunk *n* is written while chunk *n+1* is generated). | Parquet writing is ~50% of the time after vectorizing (§3.2). |
| T-18 | **CLI:** stays in Python with argparse and a plugin command registry. There is no native CLI binary. Start-up budget ≤300 ms (`import shape` plus dispatch), and heavy modules and plugins load lazily. `shape profile <glob|dir>` profiles many files in one process. | Measured: Shape-style start-up is ~0.24 s and Spindle's is ~1.25 s (§3.1). |
| T-19 | **Performance measurement.** The primary gate is timed in-process: start-up and imports are excluded for both tools, and the result is the median of 5 warm runs, each in a fresh process. The secondary gate is CLI end to end, with start-up included for both tools, applied to inputs of ≥1M rows. Both tools use their default threading, and single-threaded Shape numbers are reported alongside. Gates are ratios measured in the same CI job on the same runner. | Ratios hold across machines; fairness (§3). |
| T-20 | **Spindle baseline:** pinned at `sqllocks-spindle` 3.0.1, git `422e78df2267e73bb2fa976267e48cb437861e2f`. It is installed into a separate venv by `benchmarks/vs_spindle/setup_spindle.sh`, and upgraded only by an explicit PR that re-records every baseline. | Reproducible comparisons. |
| T-21 | **Equivalence standard** (from `benchmarks/retail_1to1`, which passed 60/60 columns). Shape output counts as equivalent to Spindle's when all of these hold: identical tables, columns, order, types and row counts; each column within Spindle's own seed-to-seed variation (KS for numeric and datetime, TVD for categorical, vocabulary overlap ≥0.999 for pooled strings); 100% FK integrity; matching FK fan-out; 100% of Spindle's business rules; and Spindle's `FidelityComparator` scoring the output within Spindle's own seed-to-seed range. | A proven, strict and fair standard. |
| T-22 | **Profiling parity standard.** Field-by-field against Spindle's `DataProfiler` output. Exact match for dtype, null counts, cardinality, uniqueness, enum, PK/FK and pattern. Enum weights within 1e-9. Mean and std within 1e-9 relative. Quantiles use Spindle's interpolation method. The same distribution family must be chosen, with parameters within 1e-6 relative. Any documented estimator difference must still show KS agreement. | Owner requirement D-06. |
| T-23 | **Remove the stale evidence files** from the tree rather than archiving them (git history keeps them). | Removes misleading claims; nothing is lost. |
| T-24 | **Docs:** a Markdown `docs/` tree built with mkdocs-material (Spindle's toolchain). The CLI reference is generated from argparse, and the performance page is generated from `benchmarks/vs_spindle/results.json`. | Docs cannot drift from the code. |
| T-25 | **Release:** GitHub Actions trusted publishing to PyPI (TestPyPI first), a CycloneDX SBOM and Sigstore build attestations. | Supply-chain hygiene. |
| T-26 | **Live external services** (Fabric, Azure Event Hubs, SQL Server) are tested three ways: (a) contract tests against recorded or mocked APIs on every PR; (b) local emulators in nightly CI — the Kafka container, the Azure Event Hubs emulator container and SQL Server in a Linux container; (c) live tests that run only when owner-provided secrets are present (§8). | Completion never depends on credentials. |

### 2.3 Decision log

Record every later change or escalation here, with the date, ID, change and reason.

| Date | ID | Change | Reason |
|---|---|---|---|
| 2026-09-29 | — | Plan approved | — |

---

## 3. Measured baselines and targets

All measurements were taken on 2026-09-29 on a 4-core Linux x86_64 container with
Python 3.11. Spindle is 3.0.1 at git `422e78d`.

### 3.1 Start-up

| | Measured |
|---|---|
| Spindle import (profiler or CLI; pandas and scipy) | ~1.25 s |
| Shape-style import (pyarrow and numpy) | ~0.24 s |

### 3.2 Generation: retail domain, medium scale (1,965,400 rows, 9 tables)

Source: `benchmarks/retail_1to1/bench_results.json`. Equivalence is 60/60 columns under
T-21.

| | Generate | Write Parquet | Total | Rows/s (total) | Peak RSS |
|---|---|---|---|---|---|
| Spindle 3.0.1 | 4.27 s | 0.65 s | **4.90 s** | 401k | 540 MB |
| 1:1 port (numpy + pyarrow, no Rust, single process) | 0.54 s | 0.55 s | 1.06 s | 1.85M | 424 MB |
| **Target: 10x minimum** | | | **≤ 0.49 s** | ≥ 4.0M | |
| **Target: 30x stretch** | | | **≤ 0.163 s** | ≥ 12.0M | |

What this tells us:
- Vectorizing alone gives 7.9x on generation, but only 4.6x in total, because Parquet
  writing does not speed up.
- So the 10x minimum needs the Rust generation kernel (P4-03) *and* parallel,
  pipelined writing (T-17).

### 3.3 Profiling

Source: `benchmarks/profile_1to1/`.

| Dataset | Spindle 3.0.1 (in-process) | 10x minimum | 30x stretch |
|---|---|---|---|
| 200k rows × 6 columns (D1) | 2.0 s | ≤ 0.20 s | ≤ 0.067 s |
| 2M rows × 6 columns | 18.7 s | ≤ 1.87 s | ≤ 0.62 s |
| D2 1M × 20, D3 5M × 10, D4 100k × 200, D5 multi-table | recorded by P0-07 | Spindle ÷ 10 | Spindle ÷ 30 |

### 3.4 Gates

Every gate is a ratio against Spindle measured in the same job:

| Gate | Workloads | Minimum | Stretch (tracked, not blocking) |
|---|---|---|---|
| PROF-IN | D1–D5, in-process | ≥10x each | ≥30x |
| PROF-CLI | D2, D3 (≥1M rows), CLI end to end | ≥10x each | ≥30x |
| GEN-IN | retail medium and large; later every domain at medium | ≥10x each | ≥30x |
| GEN-CLI | retail medium and large, CLI end to end | ≥10x | ≥30x |
| STREAM-EMIT | `shape stream` against `spindle stream --no-realtime` for the same table and count, file sink | ≥10x | ≥30x |
| STREAM-PROF | stream profiling of a file replay at 64k-row micro-batches, against Shape batch profiling of the same file (no Spindle equivalent exists) | ≥80% of batch throughput | ≥95% |
| START | `python -X importtime -c "import shape"` plus `shape --version` | ≤300 ms | ≤150 ms |

---

## 4. Target architecture

### 4.1 Layers

```
┌──────────────────────────── plugins (entry points, API v1) ─────────────────────────────┐
│ sources  sinks  detectors  fitters  strategies  distributions  calendars  domains  chaos │
│ emitters  stream-sources  transforms  commands  reports                                  │
└────────────────────────────────────────▲─────────────────────────────────────────────────┘
                                         │ shape.plugins.api.v1 (Protocols, batch-level)
┌──────────────────────────────────── shape (Python) ──────────────────────────────────────┐
│ io · profile · spec/artifact · compare (diff, drift, quality, contracts, fidelity)        │
│ generation (schema, engine, strategies) · streaming (consume, emit) · privacy · registry │
│ plugin host · cli                                                                        │
└───────────────▲───────────────────────────────────────────────▲──────────────────────────┘
                │ Arrow C Data Interface (zero-copy)             │
┌───────────────┴─────────────── shape._kernel (Rust) ──────────┴──────────────────────────┐
│ fused profile pass · hash · HLL/KLL/SpaceSaving · patterns · temporal histograms          │
│ Philox RNG · alias sampling · pool/string assembly · temporal/holiday sampler            │
│ row-sequential strategies (lifecycle, SCD2, state machines, self-reference)              │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                 Arrow C++ (pyarrow): CSV/Parquet/JSONL/IPC read, Parquet/IPC write
```

### 4.2 Runtime split rules

1. **Python never touches individual values on a hot path.** Every per-batch operation
   is one native call that covers every column.
2. **Everything in Rust has a Python reference twin** (T-03), and differential tests
   keep the two in agreement.
3. **Plugin hooks take whole batches:** Arrow `RecordBatch` or arrays in and out, never
   single values or rows. A plugin that needs speed may ship its own Rust extension
   against the same Protocol.
4. **Arrow is the only in-memory format.** Rows arriving as dicts or pandas frames are
   converted at the edge.
5. **There is one profile schema** for batch, stream, merged and partitioned results.

### 4.3 Plugin API v1

- **Discovery:** extension points are Python entry-point groups. Each group has a
  `typing.Protocol` in `shape.plugins.api.v1`:

  | Group | Protocol | Purpose |
  |---|---|---|
  | `shape.sources` | `Source` | Yields `RecordBatch`es from a URI or scheme (`file`, `parquet`, `kafka`, `mssql`, `abfss`, …) |
  | `shape.sinks` | `Sink` | Consumes `RecordBatch`es for a table (CSV, Parquet, Delta, SQL, Fabric, …) |
  | `shape.detectors` | `SemanticDetector` | Array → (label, confidence) |
  | `shape.fitters` | `DistributionFitter` | Sample array → fitted family, parameters and KS |
  | `shape.strategies` | `Strategy` | Column spec + generation context → Arrow array for a chunk |
  | `shape.distributions` | `Distribution` | Parameters + RNG stream → array |
  | `shape.calendars` | `Calendar` | Date range → event lifts |
  | `shape.domains` | `Domain` | Named schema + reference data + profiles + scale presets |
  | `shape.chaos` | `ChaosMutator` | Batch → mutated batch plus a report |
  | `shape.emitters` | `Emitter` | Event batches → an external stream |
  | `shape.stream_sources` | `StreamSource` | Offsets and batches from an external stream |
  | `shape.transforms` | `Transform` | Tables → tables (star, CDM, mask, …) |
  | `shape.commands` | `Command` | Adds `shape <name>` subcommands |
  | `shape.reports` | `ReportFormat` | Fidelity or profile report → bytes (JSON, MD, HTML, …) |

- **Versioning:** every plugin declares `SHAPE_API = "1.x"`. The host rejects major
  mismatches with a clear error. A plugin that fails to load never crashes core; the
  failure is reported by `shape plugins doctor`.
- **Loading:** plugins load lazily, on first use of their extension point.
- **Built-ins:** core's own CSV, Parquet, JSONL and IPC sources and sinks, detectors,
  fitters, strategies, distributions and the US calendars are registered through the
  same entry points.

---

## 5. Target repository layout

```
pyproject.toml                  maturin mixed project (core distribution)
rust/shape-kernel/              Cargo crate → shape._kernel
  src/{lib.rs, ffi.rs, hash.rs, profile/*.rs, sketch/{hll,kll,spacesaving}.rs,
       gen/{rng,alias,pool,string,temporal,sequential}.rs}
src/shape/
  __init__.py  api.py  errors.py  types.py
  kernel/         dispatch.py (auto|rust|python) · reference/ (pure-Python twins)
  io/             readers.py · writers.py · uri.py
  profile/        engine.py · infer.py · fit.py · patterns.py · keys.py · temporal.py · merge.py
  spec/           model.py (v2) · schema/shape-v2.schema.json · migrate.py · io.py
  artifact/       shape_file.py · io.py · canonical.py · sign.py
  diff/ drift/ quality/ contracts/ query/ registry/ relations/
  generation/     schema.py · spindle_import.py · ddl.py · engine.py · rules.py · compute.py ·
                  strategies/ · distributions/ · calendars/ · fidelity.py · report/
  streaming/      consume/ (windows, keyed, checkpoint, runtime) · emit/ (runtime, rate, envelope)
  privacy/        classification.py · release.py · safe_profile.py · kanon.py
  plugins/        api/v1.py · host.py · registry.py · kit.py
  cli/            main.py · commands/ · aliases.py
plugins/
  shape-kafka/  shape-eventhubs/  shape-fabric/  shape-sqlserver/
  shape-domains/  shape-simulation/  shape-mcp/
benchmarks/vs_spindle/
  setup_spindle.sh · profile_1to1/ · domain_1to1/ (generalized retail_1to1) · stream_1to1/ ·
  results.json · run.py
tests/  (mirrors src/shape and plugins/; property/, differential/, e2e/)
docs/   (mkdocs site; specs/; plans/COMPLETION_PLAN.md)
```

---

## 6. Work packages and phase gates

Format: **ID — title** · Depends · Deliverables · Acceptance · Fixes (bug IDs from
Appendix A).

### Phase 0 — Honest baseline

**P0-01 — Registry security**
- Depends: none.
- Deliverables: `registry/local.py` validates names against `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`,
  and resolves the final path under the root, rejecting anything outside it.
  `checkout(name, ref)` only resolves refs and hashes that are recorded in that name's
  log.
- Acceptance: tests show that `commit("../x")`, absolute paths and symlink escapes
  raise `RegistryError`, and that `checkout("public", <hash of secret>)` raises
  `RegistryError`.
- Fixes: SEC4, SEC5.

**P0-02 — Remove differential privacy (D-07)**
- Depends: none.
- Deliverables: delete `privacy/advanced.laplace` and every DP symbol and doc claim.
  Move the non-DP tests out of `tests/test_future_roadmap.py`.
- Acceptance: `grep -ri "differential privacy\|laplace\|epsilon" src docs README.md`
  finds nothing outside the decision log.
- Fixes: SEC1.

**P0-03 — Enforce release policy**
- Depends: none.
- Deliverables: `release_for` returns `allowed=False` when the classification exceeds
  the target, or when a cohort is below the minimum. Redaction removes every
  value-bearing key (`min`, `max`, `q*`, `topk`, `examples`, `quantiles`, `histogram`,
  `enum_values`, `pattern_examples`) from sensitive columns.
- Acceptance: a PII column released to PUBLIC contains no value-bearing key, and a
  denied release returns `allowed=False` with a reason.
- Fixes: SEC2.

**P0-04 — Cut modules (§7)**
- Depends: none.
- Deliverables: delete the modules and tests listed in §7, and edit the mixed test files
  as listed there.
- Acceptance: `python -c "import shape, shape.cli.main"` succeeds; the full `pytest`
  run is green; a check confirms that no removed module name is importable.
- Fixes: SEC6, SEC7, SEC8, and B1–B11 (deleted code).

**P0-05 — Repo honesty**
- Depends: P0-04.
- Deliverables:
  - Delete the root `*_MANIFEST.json`, `*_QUALIFICATION.json`, `GA_*`, `RC1_*`, `RQ*_*`,
    `NAMING_MIGRATION.json`, `FINAL_*` and `REPOSITORY_MANIFEST.json` files.
  - Delete `docs/` milestone files (`RQ*`, `VALIDATION_*`, `RC1_*`, `GA_*`, `*_COMPLETION*`,
    `EXHAUSTION_REPORT`, `HOSTILE_REVIEW_*`, `PLATFORM*`, `FIVE_PLATFORM*`,
    `THREE_CORE*`, `TORTURE*`, `FUTURE_ROADMAP*`, `AUTONOMOUS_BUILD_SYSTEM`,
    `V0_1_BUILD_PLAN`, `IMPLEMENTATION_STATUS`, `LOCAL_COMPLETION`, `CLOSURE_MATRIX`),
    plus `docs/qualification/`, `docs/plans/*` except this file, `rq/` and `GA_FINAL_TEST.txt`.
  - Rewrite README and CHANGELOG to the truthful current state.
  - Set the version to `0.9.0.dev0` (T-10).
  - Remove `pydantic` and `typing-extensions`, and move `cryptography` to `[sign]`
    (T-07).
  - Replace `docs/SPINDLE_MIGRATION*.md` with a pointer to §9.
- Acceptance: none of the deleted paths exist; `pip install .` works without
  cryptography; README contains no GA, "certified" or isolation claims.
- Fixes: X3.

**P0-06 — CI truthfulness**
- Depends: P0-05.
- Deliverables:
  - CI runs ruff lint and format, mypy with the ratchet (T-12), pytest with coverage,
    `compileall` and `pip-audit`, on the T-06 matrix.
  - `make check` runs the same commands.
  - The coverage floor is set to the current value and may only go up.
- Acceptance: CI is green; `make check` exits 0 locally; the coverage report is
  published, and the floor is enforced from then on.
- Fixes: X1, X2 (continued by the regression-test rule in §1.2).

**P0-07 — Benchmark harness**
- Depends: P0-05.
- Deliverables:
  - Move `benchmarks/retail_1to1` to `benchmarks/vs_spindle/domain_1to1`, and generalize
    it to take a domain name (it reads the Spindle domain from the pinned checkout).
  - Move `benchmarks/profile_1to1` to `benchmarks/vs_spindle/profile_1to1`.
  - Add `setup_spindle.sh` (T-20) and `run.py`, which runs every verifier and then
    every benchmark, and writes `results.json` (schema in `results.schema.json`).
  - Add a nightly workflow that runs everything, and PR smoke tests: D1, D2 and retail
    medium.
  - Delete `benchmarks/prototypes/` and the old `benchmarks/*.py`.
- Acceptance: `python benchmarks/vs_spindle/run.py --quick` produces `results.json`
  with Spindle and Shape numbers and verifier status for D1–D5 and retail medium, and
  the CI job uploads it.
- Fixes: none.

**Gate G0**
- CI is green on the full matrix.
- `results.json` records the baselines (with current Shape numbers, which may be below
  the gates).
- SEC1, SEC2, SEC4 and SEC5 have regression tests.

### Phase 1 — Profiling engine

**P1-01 — Rust build**
- Depends: G0.
- Deliverables:
  - maturin mixed layout (T-05) and the `rust/shape-kernel` skeleton.
  - `shape._kernel.version()`.
  - Arrow C Data Interface import and export for `RecordBatch`.
  - `kernel/dispatch.py` implementing T-03.
  - cibuildwheel workflow (T-04), with an sdist build test.
- Acceptance:
  - Wheels are built and installed on every T-04 target in CI.
  - A round trip of a 1M-row, 10-column batch through Rust is zero-copy: a
    buffer-address test proves it.
  - `SHAPE_KERNEL=python` runs the full test suite green.
- Fixes: none.

**P1-02 — Hash and canonicalization**
- Depends: P1-01.
- Deliverables: T-13, in Rust and as a Python reference.
- Acceptance: property tests on 10⁶ random values of every Arrow type show that Rust
  equals the reference; `1` and `1.0` hash equal, and NaN is excluded; results are
  identical under `PYTHONHASHSEED` 0, 1 and random.
- Fixes: P7, S1.

**P1-03 — Sketches**
- Depends: P1-02.
- Deliverables: T-14 HLL, KLL and SpaceSaving in Rust with Python references;
  serialization into the profile's `error_models`.
- Acceptance (Hypothesis property tests):
  - Merge is associative and commutative within the documented bound.
  - Profiling in one batch equals profiling in N batches, within the bound.
  - Memory is bounded: SpaceSaving state is at most its capacity after 10⁷ updates.
  - HLL relative error ≤ 1.04/√m × 3 at the 99th percentile.
  - KLL rank error ≤ 1%.
- Fixes: P5, P6, P9.

**P1-04 — Readers**
- Depends: P1-01.
- Deliverables:
  - `io/readers.py`: CSV (pyarrow.csv with inference and schema overrides), Parquet
    (row-group streaming), JSONL, IPC, `dict[str, array]`, pandas and polars (zero-copy
    where possible), and row iterables (edge adapter).
  - Glob and directory expansion.
- Acceptance: every reader yields `RecordBatch`es with correct types on a golden test
  set; the old `CSVSource` is removed.
- Fixes: P1.

**P1-05 — Type inference**
- Depends: P1-04, and the Spindle dtype vocabulary from `profile_1to1`.
- Deliverables:
  - Documented inference and demotion rules in `profile/infer.py`.
  - The Spindle dtype classes map one-to-one, including date, datetime and boolean.
  - Demotion keeps all evidence gathered so far.
- Acceptance: golden-file tests; `[1, 2, "x", 3]` gives count 4 with top-k containing
  all four; `[None, 1, 2]` is integer; `np.int64` and `Decimal` are numeric.
- Fixes: P2, P3, P4.

**P1-06 — Fused profile kernel**
- Depends: P1-03, P1-05.
- Deliverables: one Rust call per batch covering all columns: counts, nulls, NaN and
  ±inf, min/max, moments, hash→HLL, KLL, SpaceSaving, string lengths, pattern classes
  and temporal histograms, with rayon across columns.
- Acceptance:
  - Differential tests against the reference.
  - `py-spy` shows under 5% of samples in Python frames on the D2 profile.
- Fixes: P10, P11, P13.

**P1-07 — Profile engine**
- Depends: P1-06.
- Deliverables: `profile/engine.py`: the batch loop, merge, `exact="auto"` (T-15),
  bounded mode, a thread-count option, and multi-input profiling in one process.
- Acceptance:
  - Peak RSS for D3 at 5M rows and at 50M rows differs by less than 10%.
  - The exact and sketch paths produce identical output schemas.
- Fixes: P12, P17.

**P1-08 — Spindle profiler parity**
- Depends: P1-07.
- Deliverables: port `benchmarks/vs_spindle/profile_1to1/port.py` semantics into
  `profile/`:
  - distribution fitting: the same families, estimators and sampling rules as Spindle,
    in numpy, with scipy optional;
  - pattern detection;
  - enum detection with weights;
  - PK detection, and FK detection across tables;
  - outliers, quantiles (Spindle's interpolation), temporal histograms and string
    lengths;
  - every remaining `ColumnProfile` and `TableProfile` field.
- Acceptance: `profile_1to1/verify.py` passes every field (T-22) on D1–D5 against the
  pinned Spindle.
- Fixes: P16.

**P1-09 — Model and artifact v2**
- Depends: P1-07.
- Deliverables: Shape model v2 and its JSON Schema; the v1→v2 migrator; `.shape` v2
  with explicit NaN/inf encoding; all reader errors wrapped as `ArtifactError`; tuples
  round-trip.
- Acceptance:
  - Round-trip property tests.
  - Every v1 fixture in `tests/` migrates.
  - The artifact fuzz tests (existing) pass on v2.
- Fixes: P8, P18, P20.

**P1-10 — Downstream on v2**
- Depends: P1-09.
- Deliverables: port diff, drift, quality, contracts, query and relations to v2.
  - `unique` uses the exact count, or else the HLL error bound.
  - The null rate is correct when `rows = 0`.
  - `relationship()` matching is exact.
  - `shape conformance` runs the real spec suite from `docs/specs/`.
- Acceptance: unit tests for each fix; the conformance suite covers every MUST in
  `SHAPE_1_0_GA.md`, renamed to `SHAPE_2.md`.
- Fixes: P14, P15, P21, X4.

**P1-11 — CLI (profiling)**
- Depends: P1-10.
- Deliverables: `profile`/`capture` (glob/dir), `diff`, `inspect`/`show`, `validate`
  and `check`, with non-zero exit on failure; lazy imports; a start-up test in CI
  (gate START).
- Acceptance: CLI end-to-end tests; the START gate passes.
- Fixes: none.

**P1-12 — Remove legacy paths**
- Depends: P1-11.
- Deliverables: delete `capture/core.py:capture_rows` internals (rows now go through the
  edge adapter), `capture/vectorized.py`, `profile/text_vectorized.py`, the old
  `sketches.py`, the old `bounded_dependencies.py` and `kernel/batches.py`.
- Acceptance: no dead code under `vulture --min-confidence 80`; the suite is green.
- Fixes: none.

**Gate G1**
- PROF-IN ≥10x on D1–D5.
- PROF-CLI ≥10x on D2 and D3.
- START ≤300 ms.
- The P1-08 verifier is all green.
- Every P-bug has a regression test.
- Profile modules are mypy strict.

### Phase 2 — Plugin system

**P2-01 — API v1 Protocols**
- Depends: G1.
- Deliverables: `plugins/api/v1.py` with every Protocol in §4.3, documented, and a
  `SHAPE_API` constant.
- Acceptance: mypy strict; docs page generated.
- Fixes: none.

**P2-02 — Plugin host**
- Depends: P2-01.
- Deliverables: entry-point discovery, version check, lazy loading, a registry, and
  isolation of load failures.
- Acceptance: a broken test plugin does not affect core commands, and is reported by
  `shape plugins doctor`.
- Fixes: none.

**P2-03 — Plugin CLI**
- Depends: P2-02.
- Deliverables: `shape plugins list|info|doctor`, and plugin-contributed subcommands.
- Acceptance: e2e test with the example plugin.
- Fixes: none.

**P2-04 — Built-ins through the registry**
- Depends: P2-02.
- Deliverables: core sources, sinks, detectors, fitters and the address pack are
  registered through the same entry points.
- Acceptance: core has no direct imports of built-in implementations outside the
  registry (import-linter contract).
- Fixes: G8.

**P2-05 — Delete the sandbox (D-09)**
- Depends: P2-02.
- Deliverables: remove `plugins/core.py` and `plugins/runtime.py`; write
  `docs/plugins/trust-model.md`.
- Acceptance: no capability or isolation wording remains.
- Fixes: PL1, PL2, PL3, PL4.

**P2-06 — Plugin kit and monorepo**
- Depends: P2-03.
- Deliverables:
  - `shape.plugins.kit`: a conformance test kit per Protocol.
  - `examples/plugin/`: a source, a detector and a command.
  - `plugins/` skeletons for every distribution in T-09, with packaging and CI.
  - The author guide.
- Acceptance: the example plugin, installed out of tree, passes the kit; each skeleton
  builds a wheel.
- Fixes: none.

**Gate G2**
- An out-of-tree plugin adds a source, a detector and a command with no core change.
- All built-ins load through the registry.
- The Phase 1 gates still hold.

### Phase 3 — Stream profiling

**P3-01 — Stream runtime**
- Depends: G2.
- Deliverables: micro-batches go through the Phase 1 engine; tumbling, sliding and
  session windows with `snapshot()` and `restore()`; watermarks and allowed lateness.
- Acceptance:
  - A window crash-and-restore test gives output identical to an uninterrupted run.
  - Late-data cases behave as specified in `docs/specs/STREAMING_SEMANTICS.md`.
- Fixes: S3.

**P3-02 — Keyed state**
- Depends: P3-01.
- Deliverables: bounded keyed state (per-key sketches with LRU and TTL, and a hard
  memory cap); vectorized dedupe.
- Acceptance: RSS stays flat over 10⁸ events with 10⁶ keys; dedupe is correct against a
  reference.
- Fixes: S2, S5.

**P3-03 — Checkpoints and offsets**
- Depends: P3-01.
- Deliverables: offset-committed checkpoints; reconnect resumes from the committed
  offset.
- Acceptance: a fault-injection test shows no duplicated or lost batches across 100
  forced reconnects.
- Fixes: S4.

**P3-04 — Stream-source plugins**
- Depends: P3-03, P2-06.
- Deliverables: `shape-kafka` and `shape-eventhubs` consumers.
- Acceptance: nightly e2e against the Kafka and Event Hubs emulator containers.
- Fixes: none.

**P3-05 — `shape stream-profile` CLI**
- Depends: P3-04.
- Deliverables: the CLI command.
- Acceptance: e2e test; the STREAM-PROF gate.
- Fixes: none.

**Gate G3**
- STREAM-PROF ≥80%.
- Stream-replay profile equals the batch profile within the error model.
- Profiles are identical across processes.
- The S-bugs have regression tests.

### Phase 4 — Generation engine

**P4-01 — Schema**
- Depends: G2.
- Deliverables:
  - The Shape generation schema, with its JSON Schema.
  - A lossless `.spindle.json` importer covering every Spindle schema feature: model,
    tables, columns, generators, relationships, business rules, scale presets and
    modes (3nf and star).
  - `from-ddl` for the tsql, tsql-fabric-warehouse, postgres and mysql dialects.
- Acceptance:
  - Every domain schema in the pinned Spindle, and every `examples/*.spindle.json`
    there, imports and re-exports losslessly.
  - DDL golden tests.
- Fixes: none.

**P4-02 — Engine**
- Depends: P4-01.
- Deliverables: the dependency resolver (Spindle's ordering), row-count calculation
  (presets, fixed, per_parent × ratio, per_year × years), column ordering, the compute
  phase, the business-rules engine (validate and fix), chunked random access (T-16) and
  `--dry-run`.
- Acceptance:
  - Row counts equal Spindle's for every domain at every scale.
  - Chunk-layout invariance: generating with chunk sizes 1k, 64k and 1M gives
    identical output.
- Fixes: G2.

**P4-03 — Rust generation kernel**
- Depends: P4-02, P1-01.
- Deliverables: Philox streams, alias sampling, pool and string assembly (templates,
  case, joins), and temporal sampling (month, day-of-week, hour, bimodal), with
  references.
- Acceptance: differential tests; a single-threaded kernel benchmark reported in
  `results.json`.
- Fixes: G1.

**P4-04 — Strategies**
- Depends: P4-03.
- Deliverables: every Spindle strategy as a `shape.strategies` built-in:
  - `sequence`, `uuid`, `enum`/`weighted_enum`, `distribution`, `empirical`, `pattern`
  - `faker`/`native` (Spindle's pools), `formula`, `computed`, `derived`
  - `conditional`, `correlated`, `lookup`, `reference_data`, `temporal`
  - `lifecycle`, `scd2`, `first_per_parent`, `foreign_key`, `composite_foreign_key`
  - `self_referencing`/`self_ref_field`, `record_field`, `record_sample`

  The row-sequential ones (`lifecycle`, `scd2`, `self_referencing`) run in Rust.
- Acceptance: per-strategy statistical tests against Spindle's strategy on the same
  config under T-21 column criteria.
- Fixes: G7.

**P4-05 — Distributions and calendars (D-05, D-11)**
- Depends: P4-03.
- Deliverables:
  - Families: Spindle's (uniform, normal, log_normal, pareto, zipf, geometric,
    poisson), plus exponential, gamma, beta, weibull, triangular, negative binomial,
    bernoulli, power law with cutoff, truncated versions of each, mixtures, and
    empirical histograms.
  - An 80/20 helper for FK fan-out.
  - Calendars: rule engine (D-11 rules); US federal and US retail calendars; lift with
    ramp and decay; negative lifts; custom events; payday, month-end and quarter-end
    effects; trend with step and ramp regime changes.
  - Profiler detection of seasonality, holiday lift and tails, added to `profile/temporal.py`.
- Acceptance:
  - For every family, fitting a 10⁶-sample draw recovers its parameters within 2%.
  - A calendar test recovers the configured Black Friday, Cyber Monday and Christmas
    lifts within 5%.
  - The 80/20 helper yields the configured top-share within 1%.
  - Profile → generate → profile reproduces the month, day-of-week and hour weights
    with TVD ≤ 0.01.
- Fixes: none.

**P4-06 — Writers**
- Depends: P4-02.
- Deliverables:
  - Core writers: CSV, TSV, JSONL, SQL INSERT (dialects and the `--sql-ddl`,
    `--sql-drop`, `--sql-go`, `--schema-name` options, as in Spindle) and Parquet (T-17:
    parallel and pipelined).
  - Plugin writers: Excel (`[excel]`) and Delta (`[delta]`, deltalake).
- Acceptance: golden-output tests per format; Parquet written by Shape is readable by
  Spindle's readers and by pandas.
- Fixes: none.

**P4-07 — Retail domain through the engine**
- Depends: P4-04, P4-06.
- Deliverables: retail as the first `shape.domains` entry, generated by the real engine
  (not the benchmark port).
- Acceptance:
  - `domain_1to1/verify.py --domain retail` passes 60/60 (T-21) at small, medium and
    large.
  - GEN-IN and GEN-CLI ≥10x for retail medium and large.
- Fixes: none.

**P4-08 — Profile → generate**
- Depends: P4-05, G1.
- Deliverables: `shape generate --from x.shape` fits strategies from profile evidence:
  marginals, a real Gaussian copula with marginal transforms, missingness and
  seasonality. `plan` reports truthfully what will and won't be preserved.
- Acceptance: round-trip tests (profile → generate → profile within T-22 tolerances
  for the modelled fields); `plan` flags every unmodelled field.
- Fixes: G5, G6.

**P4-09 — Fidelity report**
- Depends: P4-07.
- Deliverables: `shape fidelity` (aliases `compare` and `verify`) with Spindle
  `FidelityComparator`-equivalent scoring. Reports in JSON, MD and HTML through
  `shape.reports`. Thresholds, and a non-zero exit on failure.
- Acceptance:
  - The scores for retail (Spindle vs Spindle) match Spindle's comparator within 0.5
    points.
  - A missing column scores 0, and an empty reference fails.
- Fixes: G3, G4.

**P4-10 — Generation CLI**
- Depends: P4-09.
- Deliverables: `generate`, `describe`, `list`, `validate`, `from-ddl`, `presets`,
  `composite`, and the `--mode 3nf|star`, `--scale`, `--format`, `--dry-run` and
  `--seed` options (§9).
- Acceptance: e2e tests per command.
- Fixes: none.

**Gate G4**
- Retail passes 60/60 at every scale.
- GEN-IN and GEN-CLI ≥10x on retail medium and large.
- Every strategy passes its statistical test.
- The G-bugs have regression tests.

### Phase 5 — Streaming during generation

**P5-01 — Emitter runtime**
- Depends: G3, G4.
- Deliverables: a token-bucket rate limiter (constant, burst `START:DURATION:MULT`,
  diurnal); out-of-order fraction; anomaly fraction (through `shape.chaos`); the
  Spindle envelope (D-12); backpressure; at-least-once delivery with idempotent keys;
  and a checkpoint on shutdown.
- Acceptance: the rate holds within ±5% over 10 minutes in CI and 1 hour nightly;
  events are replayed exactly after a kill -9 and restart.
- Fixes: none.

**P5-02 — Emitters**
- Depends: P5-01.
- Deliverables: console, file and JSONL (core); Kafka producer (`shape-kafka`); Event
  Hubs producer (`shape-eventhubs`); Fabric Eventstream and Eventhouse
  (`shape-fabric`).
- Acceptance: contract tests plus nightly emulator e2e; live tests when secrets are
  present (§8).
- Fixes: none.

**P5-03 — Live fidelity**
- Depends: P5-02, P3-05.
- Deliverables: a tee from the emitted stream into the stream profiler, compared live
  against the target shape, with drift alerts.
- Acceptance: the live fidelity score equals the offline `shape fidelity` score on the
  same events, within 0.5 points.
- Fixes: none.

**P5-04 — `shape stream` CLI**
- Depends: P5-03.
- Deliverables: parity with every option of Spindle's `stream` (§9), plus Shape's sinks.
- Acceptance: e2e test; the STREAM-EMIT gate.
- Fixes: none.

**Gate G5**
- STREAM-EMIT ≥10x.
- The rate is within ±5% for 1 hour (nightly).
- Live fidelity equals offline fidelity.

### Phase 6 — Spindle feature ports (plugins)

**P6-01 — `shape-domains`**
- Depends: G4.
- Deliverables: every domain in Spindle's `domains/` at the pinned commit:
  - capital_markets, education, financial, healthcare, hr, insurance, iot
  - manufacturing, marketing, pulse, real_estate, retail, supply_chain, telecom

  Plus presets and `composite`, and the reference data carried over with notices
  (D-10).
- Acceptance:
  - `domain_1to1/verify.py --domain <each>` passes T-21 at small and medium.
  - GEN-IN ≥10x for every domain at medium.
- Fixes: none.

**P6-02 — Chaos engine**
- Depends: G4.
- Deliverables: a port of Spindle's `chaos/` as `shape.chaos` built-ins; this replaces
  `scenarios/`.
- Acceptance: parity tests per mutator against Spindle, measuring mutation rates and
  types.
- Fixes: none.

**P6-03 — `shape mask`**
- Depends: G1, G4.
- Deliverables: a port of Spindle's `masker.py` semantics, as a `shape.transforms`
  built-in.
- Acceptance: parity test against `spindle mask` on the D2 dataset: same masked
  columns, same format preservation, and no original values remaining.
- Fixes: none.

**P6-04 — `shape-simulation`**
- Depends: G4, G5.
- Deliverables: IoT, clickstream, finance, state-machine, file-drop and SCD2
  simulations (Spindle's `simulation/`).
- Acceptance: parity tests per simulator under T-21.
- Fixes: none.

**P6-05 — Incremental**
- Depends: G4.
- Deliverables: `shape continue` and `shape time-travel` (Spindle's `incremental/`).
- Acceptance: parity tests covering growth, churn, update fraction and seasonality.
- Fixes: none.

**P6-06 — Transforms**
- Depends: G4.
- Deliverables: `shape transform star` and `shape transform cdm` (aliases `to-star` and
  `to-cdm`).
- Acceptance: output schemas equal Spindle's; row-level parity on the same input.
- Fixes: none.

**P6-07 — `shape-fabric`**
- Depends: G4, G5.
- Deliverables: Lakehouse files, Warehouse bulk load (COPY INTO via staging), SQL
  Database, Eventhouse, Semantic Model, OneLake paths, and the credential modes (cli,
  msi, spn, sql, device-code). Also `publish`, `notebook`, `deploy-notebook` and
  `setup-fabric`, plus the Lakehouse/Delta profiling source.
- Acceptance: contract tests against recorded Fabric REST/ABFS/TDS interactions; live
  e2e when secrets are present (§8).
- Fixes: none.

**P6-08 — `shape-sqlserver`**
- Depends: G1.
- Deliverables: database profiling with a schema walk, pyodbc and Entra auth (Spindle's
  `database_profiler`), plus the SQL Database writer helpers shared with `shape-fabric`.
- Acceptance: nightly e2e against the SQL Server container; PROF-IN ≥10x against
  Spindle's database profiler on the same database.
- Fixes: none.

**P6-09 — Validation gates and quarantine**
- Depends: G1.
- Deliverables: Spindle's `validation/` gates and quarantine, in core `quality` and
  `contracts`.
- Acceptance: parity tests.
- Fixes: none.

**P6-10 — Profile registry parity**
- Depends: G1, G4.
- Deliverables: `shape profile export|import|list|validate` and `shape registry
  list|save|delete|tag|diff|reindex|validate` (§9).
- Acceptance: e2e tests per subcommand.
- Fixes: none.

**P6-11 — `shape-mcp`**
- Depends: G4.
- Deliverables: an MCP server with the tools Spindle's `mcp_bridge` exposes, on the
  `mcp` SDK.
- Acceptance: an MCP client e2e test covering every tool.
- Fixes: none.

**P6-12 — `shape demo`**
- Depends: P6-07.
- Deliverables: `init`, `list`, `run`, `preflight`, `cleanup`, `status`, `notebook` and
  `report` (Spindle's `demo/`).
- Acceptance: e2e tests with the local sinks.
- Fixes: none.

**Gate G6**
- Every row of §9 has a passing e2e test.
- Every domain passes T-21 and GEN-IN ≥10x at medium.

### Phase 7 — Privacy and trust

**P7-01 — One classification taxonomy**
- Depends: G1.
- Deliverables: a single taxonomy (merge `privacy/policy.py` and `classification.py`);
  a safe-profile export and validator matching Spindle's `safe_profile*` and
  `safe_validator` (the `--safe` flag on `profile validate`).
- Acceptance: parity tests against Spindle's safe-profile output on D2.
- Fixes: SEC3.

**P7-02 — k-anonymity and suppression**
- Depends: P7-01.
- Deliverables: enforced minimum cohort, and suppression of small cells in top-k,
  enums and histograms.
- Acceptance: no released cell has a count below the minimum cohort (property test).
- Fixes: none.

**P7-03 — Signing**
- Depends: P1-09.
- Deliverables: Ed25519 signing through `[sign]`; `--sign` and `--verify` on write and
  read; key-handling docs.
- Acceptance: a forged artifact with rewritten hashes fails `--verify`.
- Fixes: P19.

**P7-04 — Security review**
- Depends: P7-03, G5.
- Deliverables: a refreshed threat model (plugin trust, emitter credentials, artifact
  signing); the artifact fuzzer in nightly CI; a `bandit` clean run.
- Acceptance: no high findings.
- Fixes: none.

**Gate G7**
- SEC3 and P19 have regression tests.
- Safe-profile parity holds.
- The security review has no open high findings.

### Phase 8 — Migration and release

**P8-01 — Compatibility**
- Depends: G6.
- Deliverables: every Spindle command name is accepted as an alias (§9); Spindle
  profile JSON and `.spindle.json` are read directly; a `docs/migration/from-spindle.md`
  guide.
- Acceptance: running each command from Spindle's README examples, with `spindle`
  replaced by `shape`, succeeds and produces equivalent output.
- Fixes: none.

**P8-02 — Nightly parity suite**
- Depends: G6.
- Deliverables: every domain and every profiling dataset against pinned Spindle, with
  results published to `results.json` and to the docs performance page.
- Acceptance: the nightly run is green for 7 consecutive days.
- Fixes: none.

**P8-03 — Docs site**
- Depends: G6.
- Deliverables: mkdocs site (T-24): quickstart, tutorials, CLI reference (generated),
  plugin author guide, architecture, spec, migration guide, and the performance page
  (generated).
- Acceptance: `mkdocs build --strict` passes, and a link check passes.
- Fixes: none.

**P8-04 — Release engineering**
- Depends: P8-01–P8-03, G7.
- Deliverables: T-25; version 1.0.0 for core and every plugin; a TestPyPI dry run; a
  release checklist.
- Acceptance: TestPyPI install of `sqllocks-shape[all]` on each T-04 platform passes
  the smoke suite.
- Fixes: none.

**P8-05 — Final hostile review**
- Depends: P8-04.
- Deliverables: a full independent code review of `src/`, `rust/` and `plugins/`, with
  every finding fixed or recorded in §2.3 with owner sign-off.
- Acceptance:
  - No open bugs in Appendix A.
  - Every gate G0–G7 is green on the release commit.
  - mypy strict covers all of `src/shape` (T-12).
- Fixes: all.

**Gate G8 (done)**
- Every item above holds.
- 1.0.0 is published to PyPI (owner action O-01).

---

## 7. Keep / cut

**Keep and rebuild:**
- `artifact`, `capture` (edge adapter only), `profile`, `spec`, `model`, `types`,
  `errors`, `api`, `cli`
- `diff`, `drift`, `quality`, `contracts`, `query`, `registry`, `relations`
- `streaming`, `connectors` (rebuilt as `io` plus plugins), `plugins` (rewritten)
- `generation`, `packs` (becomes the domains and address strategy), `location`,
  `geospatial`
- `privacy`, `security`, `validation`

**Delete in P0-04.** Nothing in the core, CLI or `__init__` imports these, apart from
the two edges noted.

| Module | Reason |
|---|---|
| `admin`, `ai`, `marketplace`, `federation`, `enterprise`, `governance`, `graph`, `compiler`, `execution`, `reproducibility`, `integrations` | Stubs (12–97 lines); `enterprise` hard-codes an audit key (SEC8) |
| `explain.py`, top-level `policy` | Unused; `explain` crashes (B11); `policy` duplicates `privacy.policy` |
| `history`, `lineage`, `observability`, `packages`, `reference` | Unused; bugs B6–B8. Registry covers history; plugins replace packages |
| `distributed`, `etl`, `ci` | Lossy merge (B4) and duplicate result types (B5). Replaced by core merge and `shape check` exit codes |
| `scenarios`, `temporal`, `testing`, `transform` | Replaced by chaos, strategies, distributions/calendars and transforms (Phases 4 and 6) |
| `hub`, `webapp` | D-08; SEC6 and SEC7 |

**Order:** `integrations` → `etl` → `ci`/`distributed` → the rest.

**Tests:**
- **Delete these files:**
  - `tests/test_future_roadmap.py` (move its non-DP `privacy` cases first)
  - `tests/compiler/test_compiler.py`
  - `tests/etl/test_etl.py`
  - `tests/history/*`
  - `tests/transform/test_transform.py`
  - `tests/platform12/test_integrations.py`
  - `tests/test_ci_gate.py`
  - `examples/ci_gate.py`
- **Edit these files:**
  - `tests/platform12/test_features_01_04.py`, `_05_08.py`, `_09_12.py`
  - `tests/platform12/test_hardening.py`
  - `tests/torture/test_cross_feature_torture.py`
  - `tests/privacy/test_privacy.py`
- **Import edges:** `webapp/core.py` imports `query` (goes with the webapp). `cli/main.py`
  imports `registry`, `contracts` and `query` (all kept).

---

## 8. Owner actions (external)

These need the owner's accounts. None of them blocks a gate, because each has a fallback
(T-26).

| ID | Action | Needed by | Fallback until done |
|---|---|---|---|
| O-01 | Create the `sqllocks-shape` project and every `sqllocks-shape-*` plugin project on PyPI and TestPyPI, and configure trusted publishing for this repo | P8-04 | Release artifacts built and attested in CI, but not uploaded |
| O-02 | Provide a Fabric workspace and a service principal as repo secrets (`FABRIC_*`) | P5-02, P6-07 live tests | Contract tests against recorded interactions |
| O-03 | Provide an Azure Event Hubs namespace as secrets (`EVENTHUBS_*`) | P3-04, P5-02 live tests | Event Hubs emulator container |
| O-04 | Enable branch protection on `main`, requiring the CI, gate and nightly-status checks | P0-06 | Gates enforced by CI only |
| O-05 | After 1.0.0: add a deprecation notice to Spindle's README pointing to Shape | P8-04 | — |

---

## 9. Spindle CLI parity map

Spindle 3.0.1 commands, and their Shape equivalents. Every row needs an e2e test (G6).

| Spindle | Shape (aliases in brackets) | WP |
|---|---|---|
| `generate` | `shape generate` | P4-10 |
| `describe`, `list`, `validate` | `shape describe`, `shape list` [`domains list`], `shape validate` | P4-10 |
| `presets`, `composite` | `shape presets`, `shape composite` | P4-10, P6-01 |
| `stream` | `shape stream` | P5-04 |
| `to-star`, `to-cdm` | `shape transform star\|cdm` [`to-star`, `to-cdm`] | P6-06 |
| `learn`, `export-model` | `shape profile` + `shape generate --from` [`learn`, `export-model`] | P1-11, P4-08 |
| `from-ddl` | `shape from-ddl` | P4-01 |
| `continue`, `time-travel` | `shape continue`, `shape time-travel` | P6-05 |
| `compare`, `verify` | `shape fidelity` [`compare`, `verify`] | P4-09 |
| `mask` | `shape mask` | P6-03 |
| `profile export\|import\|list\|validate\|capture\|diff` | `shape profile …` (same subcommands) | P1-11, P6-10 |
| `profile registry list\|save\|delete\|tag\|diff\|reindex\|validate` | `shape registry …` [`profile registry …`] | P6-10 |
| `publish`, `notebook`, `deploy-notebook`, `setup-fabric` | `shape fabric publish\|notebook\|deploy-notebook\|setup` [originals] | P6-07 |
| `demo init\|list\|run\|preflight\|cleanup\|status\|notebook\|report` | `shape demo …` | P6-12 |
| *(new)* | `shape stream-profile`, `shape plugins`, `shape check`, `shape diff`, `shape inspect`, `shape conformance` | P1-11, P2-03, P3-05 |

---

## 10. Status tracker

Update this table in the same PR as the work. Status values: `todo`, `wip`, `done`.

| WP | Status | PR / commit | WP | Status | PR / commit |
|---|---|---|---|---|---|
| P0-01 | todo | | P4-01 | todo | |
| P0-02 | todo | | P4-02 | todo | |
| P0-03 | todo | | P4-03 | todo | |
| P0-04 | todo | | P4-04 | todo | |
| P0-05 | todo | | P4-05 | todo | |
| P0-06 | todo | | P4-06 | todo | |
| P0-07 | todo | | P4-07 | todo | |
| P1-01 | todo | | P4-08 | todo | |
| P1-02 | todo | | P4-09 | todo | |
| P1-03 | todo | | P4-10 | todo | |
| P1-04 | todo | | P5-01 | todo | |
| P1-05 | todo | | P5-02 | todo | |
| P1-06 | todo | | P5-03 | todo | |
| P1-07 | todo | | P5-04 | todo | |
| P1-08 | todo | | P6-01 | todo | |
| P1-09 | todo | | P6-02 | todo | |
| P1-10 | todo | | P6-03 | todo | |
| P1-11 | todo | | P6-04 | todo | |
| P1-12 | todo | | P6-05 | todo | |
| P2-01 | todo | | P6-06 | todo | |
| P2-02 | todo | | P6-07 | todo | |
| P2-03 | todo | | P6-08 | todo | |
| P2-04 | todo | | P6-09 | todo | |
| P2-05 | todo | | P6-10 | todo | |
| P2-06 | todo | | P6-11 | todo | |
| P3-01 | todo | | P6-12 | todo | |
| P3-02 | todo | | P7-01 | todo | |
| P3-03 | todo | | P7-02 | todo | |
| P3-04 | todo | | P7-03 | todo | |
| P3-05 | todo | | P7-04 | todo | |
| P8-01 | todo | | P8-04 | todo | |
| P8-02 | todo | | P8-05 | todo | |
| P8-03 | todo | | | | |

| Gate | Status |
|---|---|
| G0 | todo |
| G1 | todo |
| G2 | todo |
| G3 | todo |
| G4 | todo |
| G5 | todo |
| G6 | todo |
| G7 | todo |
| G8 | todo |

## Appendix A — Verified bug register

Each entry names the file, the defect and the phase that fixes it. Every entry was
reproduced by running code during the review.

**Profiling core**
| ID | Location | Defect | Phase |
|---|---|---|---|
| P1 | `cli/main.py:26`, `connectors/files.py:8` | CSV values arrive as strings, so every column is typed `text` | 1 |
| P2 | `capture/core.py:43-50` | Demoting a numeric column to text discards prior values (`[1,2,"x",3]` → count 3) | 1 |
| P3 | `capture/core.py:35` | A leading `None` locks the column as text | 1 |
| P4 | `capture/core.py:20` | `np.int64` / `Decimal` treated as text | 1 |
| P5 | `profile/sketches.py:69-98` | SpaceSaving heap never pruned (200k updates → 200k entries) | 1 |
| P6 | `profile/sketches.py:100-105` | SpaceSaving merge drops error terms; not order-independent | 1 |
| P7 | `profile/numeric.py:30`, `sketches.py:13` | NaN fed to HLL/top-k; `1` and `1.0` hash differently | 1 |
| P8 | artifact write | Any profile containing NaN cannot be saved (`SecurityError`) | 1 |
| P9 | `profile/sketches.py:131-140` | KLL compaction of odd levels drifts total weight | 1 |
| P10 | `capture/vectorized.py:21` | `null_count` hard-coded 0; Arrow nulls become NaN | 1 |
| P11 | `profile/text_vectorized.py:10,38` | `None` becomes the string `'None'`; `null_count` 0 | 1 |
| P12 | `capture/vectorized.py:52` | Fast and row paths emit different schemas (spurious drift) | 1 |
| P13 | `capture/vectorized.py:79` | Text columns fall back to per-cell Python; vectorized text unused | 1 |
| P14 | `contracts/core.py:66` | `unique` compares HLL estimate to exact rows (unique ids fail at 5k–30k rows) | 1 |
| P15 | `contracts/core.py:35` | Null rate with `rows=0` divides by 1 | 1 |
| P16 | `profile/bounded_dependencies.py:48` | Samples per row, not per group; inflates FD confidence | 1 |
| P17 | `profile/dependencies.py`, `advanced.py`, `dependence.py` | Retain all rows despite "bounded" docstrings | 1 |
| P18 | `artifact/shape_file.py:67`, `artifact/io.py:109` | Raw `KeyError`/zlib errors escape instead of `ArtifactError` | 1 |
| P19 | `artifact/*` | `.shape` authenticity is checksum-only; `secure.py` unused | 7 |
| P20 | `api.py` | `load(save(x)) != x` (tuples → lists) | 1 |
| P21 | `query/core.py:48` | `relationship("x","x")` matches any relationship containing `x` | 1 |

**Streaming**
| ID | Location | Defect | Phase |
|---|---|---|---|
| S1 | `streaming/platinum.py:198,332` | Python `hash()`: non-deterministic across processes; fast path bins differently | 3 |
| S2 | `streaming/keyed.py:289-299` | "Bounded" state heap unbounded | 3 |
| S3 | `streaming/windows.py:62` | `TumblingWindow` has `snapshot()` but no restore | 3 |
| S4 | `connectors/qualification.py:61-70` | Reconnect replays already-yielded batches | 3 |
| S5 | `streaming/vectorized.py:166` | `deduplicate_ids` loops per row | 3 |

**Plugins**
| ID | Location | Defect | Phase |
|---|---|---|---|
| PL1 | `plugins/core.py:28-44` | Capabilities are self-declared; policy checks only the declaration | 2 |
| PL2 | `plugins/runtime.py:36` | Subprocess inherits env, filesystem and network | 2 |
| PL3 | `plugins/core.py` | `filesystem_read` never checked | 2 |
| PL4 | `plugins/runtime.py` | Output buffered fully before the size limit is applied | 2 |

**Generation**
| ID | Location | Defect | Phase |
|---|---|---|---|
| G1 | `generation/address_vectorized.py:420`, `packs/address.py:152` | Seed added to row index (seed 1 = seed 0 shifted); negative seed overflows | 4 |
| G2 | `generation/strategies.py:525-546` | `row_at` depends on call order with `FirstPerParent` | 4 |
| G3 | `generation/certificate.py:224` | Missing column scores 1.0; empty reference passes | 4 |
| G4 | `cli/main.py:192` | `certify-shapes` always exits 0 | 4 |
| G5 | `generation/future.py:11-14` | `missingness` never applied; `gaussian_copula` is plain MVN | 4 |
| G6 | `generation/fidelity.py:365-378` | `plan_reconstruction` marks everything preserved without checking | 4 |
| G7 | `packs/address.py:71-76` | O(n × reference rows) generation | 4 |
| G8 | `packs/domains.py:286` | Versions sorted as strings (1.9.0 > 1.10.0) | 2 |

**Privacy and security**
| ID | Location | Defect | Phase |
|---|---|---|---|
| SEC1 | `privacy/advanced.py:5` | Laplace noise uses fixed `seed=0` | 0 |
| SEC2 | `privacy/policy.py:84`, `privacy/release.py:37-40` | `release_for` always allows; min/max/quantiles of PII columns leak | 0 |
| SEC3 | `privacy/policy.py:6` vs `privacy/classification.py:9` | Two inconsistent classification taxonomies | 7 |
| SEC4 | `registry/local.py:45` | `checkout(name, hash)` returns objects belonging to other names | 0 |
| SEC5 | `registry/local.py:26,32` | Name path traversal writes outside the registry root | 0 |
| SEC6 | `hub/core.py:274-291,363` | Unencoded URL paths, no default auth, no body limit | 0 (cut) |
| SEC7 | `webapp/core.py:59` | Non-constant-time token compare, no default auth, exception text leaked | 0 (cut) |
| SEC8 | `enterprise/core.py:44` | Hard-coded audit key | 0 (cut) |

**Process**
| ID | Defect | Phase |
|---|---|---|
| X1 | `mypy --strict` reports 893 errors; `make check` fails; CI does not run mypy | 0 |
| X2 | Thin tests (301 asserts); no type-inference or boundedness tests | 0–1 |
| X3 | Root qualification JSON files are captured sandbox output; docs overclaim; migration matrix is wrong | 0 |
| X4 | `shape conformance` is three smoke tests (`validation/suite.py:14-36`) | 1 |

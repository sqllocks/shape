# Shape completion plan

Status: proposed · Supersedes `docs/plans/REMAINING_WORK.md` and `docs/plans/NEXT_WORK_PACKETS.md`

This plan takes Shape from its current state (a 9.3k-line prototype whose headline
claims are not yet true) to a release that replaces Spindle. It is based on a code
review of every module in `src/shape`, a feature comparison against
`sqllocks/spindle` (~90k lines), and benchmarks run on both. Every bug listed in the
appendix was reproduced by running code.

## 1. Scope decisions (from the owner)

| Question | Decision |
|---|---|
| Relationship to Spindle | **Full Spindle parity.** Shape replaces Spindle for profiling *and* generation (domains, FK-correct generation, chaos, simulation, incremental, Fabric writers). |
| Streaming | **Both directions.** Full stream *profiling* (consume) and true event *streaming during generation* (emit to Kafka / Event Hub / Eventstream / file / console). |
| Plugins | **Features are added as plugins built on core features.** A small core exposes stable extension points; first-party features (domains, writers, connectors, chaos, detectors) are built through the same plugin API. |
| Performance | **Profiling ≥10x faster than Spindle, with correct types**, measured on identical data. |

Defaults assumed where no decision was given (change any of these before Phase 1 starts):

- **Format compatibility:** there are no external users of `.shape` v1. Phase 1 moves to
  format v2 and keeps a read-only v1 migrator.
- **Python:** 3.11+ (unchanged).
- **Core dependencies:** `numpy` and `pyarrow` only. scipy, confluent-kafka, azure-*,
  pyodbc, deltalake and openpyxl stay optional extras, following Spindle's pattern.
- **Native code:** pure Python + Arrow/numpy first. A Rust sketch kernel (pyo3/maturin)
  is added only if the Phase 1 performance gate cannot be met without it.
- **Typing:** mypy runs in CI. It is strict for new and rewritten modules, a per-module
  ratchet applies to the rest, and the 893 current errors are not fixed in bulk.

## 2. Where things stand today

- **Tests:** all 593 pass and ruff is clean. The tests are thin, though: 217 test
  functions and 301 asserts. No test checks that a CSV numeric column is profiled as
  numeric.
- **Profiling is slower than Spindle and gets types wrong.** On a 200k-row × 6-column
  CSV:

  | Path | Time | Result |
  |---|---|---|
  | Spindle `DataProfiler().from_csv` | 1.96 s | correct types, distribution fits |
  | Shape CLI `shape profile` | 8.0 s | **every column typed `text`** |
  | Shape `read_csv` + `shape.profile(dict)` | 2.7–3.8 s | numeric typed correctly; text columns fall back to a per-cell Python loop |

- The 1–2M rows/s figures in the root `*_QUALIFICATION.json` files come from `rq/`
  scripts that call code no public API or CLI path uses (`profile/text_vectorized.py`,
  `streaming/full_engine.py`).
- **Several guarantees are not real:** differential privacy (fixed seed), release policy
  (always allowed), plugin isolation (the plugin declares its own capabilities and
  inherits env/fs/net), artifact authenticity (checksums only), and registry isolation
  (path traversal, cross-name reads).
- **Several features report success without doing the work:** `plan_reconstruction`,
  the fidelity certificate, the `certify-shapes` exit code, `conformance`, and
  `generation/future.py`.
- **Against Spindle:**
  - Spindle features missing from Shape: 13 domains, chaos, simulation, masking,
    incremental/time-travel, star/CDM, DDL import, Parquet/Delta/Excel/SQL writers,
    Fabric writers, stream emission, MCP bridge, and ~20 generation strategies.
  - `docs/SPINDLE_MIGRATION_MATRIX.md` wrongly claims mask and Parquet/Delta support.
- **Unused modules:** 22 packages are stubs or unused, and nothing in the core
  imports them.

## 3. Target architecture

```
                         ┌───────────────────────── plugins (entry points) ─────────────────────────┐
                         │ sources  sinks/writers  detectors  fitters  strategies  domains  chaos     │
                         │ stream-emitters  stream-sources  transforms  cli-commands  report-formats │
                         └───────────────▲───────────────────────────▲───────────────────────────────┘
                                         │ stable, versioned plugin API (shape.api.v1)
┌──────────────────────────────────────── shape core ────────────────────────────────────────────────┐
│ io: Arrow RecordBatch readers/writers (CSV, Parquet, JSONL, Arrow IPC)                              │
│ profile engine: typed columnar kernels + bounded mergeable sketches (HLL, KLL, SpaceSaving)        │
│ spec: Shape model v2 (one schema for batch, stream and distributed output) + .shape artifact       │
│ compare: diff / drift / contracts / quality / fidelity report                                      │
│ generate engine: schema graph, dependency order, FK integrity, chunked, deterministic random access│
│ stream runtime: consume (windows, checkpoints) + emit (rate limit, envelope, backpressure)         │
│ plugin host: discovery, API-version negotiation, registry, `shape plugins list/doctor`            │
└─────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Rules:

1. **Arrow is the single in-memory format.** Every source yields `pyarrow.RecordBatch`,
   and every profiler, generator and writer consumes or produces batches. The row-dict
   path (`capture_rows`) becomes a thin adapter that builds batches.
2. **There is one profile output schema,** whatever the path: batch, stream,
   partition-merge or distributed. Exact-versus-sketch is recorded in `error_models`,
   never in the shape of the output.
3. **Sketches are bounded, mergeable and deterministic across processes.** A seeded
   64-bit hash is used everywhere, never Python `hash()`, and merges are associative
   and commutative, which property tests prove.
4. **First-party features are built through the plugin API.** Domains, Fabric writers,
   Kafka/Event Hub, chaos and masking register through entry points exactly as a third
   party would. The core ships only what the CLI needs to profile, diff and generate
   from CSV/Parquet.
5. **No claim without a gate.** Every performance, fidelity or security claim in the
   docs is backed by a test or benchmark that runs in CI and fails when the claim stops
   being true.

### Plugin API (Phase 2)

- **Discovery:** each extension point has its own `importlib.metadata` entry-point
  group (`shape.sources`, `shape.sinks`, `shape.detectors`, `shape.fitters`,
  `shape.strategies`, `shape.domains`, `shape.chaos`, `shape.emitters`,
  `shape.transforms`, `shape.commands`, `shape.reports`).
- **Contracts:** each extension point is a `typing.Protocol` in `shape.api.v1`. Every
  plugin declares `api_version`, and the host refuses incompatible versions with a
  clear error.
- **Trust model:** plugins are trusted, in-process Python code, like pytest or Spindle
  strategies.
  - The current subprocess "capability sandbox" (`plugins/core.py`, `plugins/runtime.py`)
    is removed, along with its isolation claims.
  - A real OS-level sandbox for untrusted plugins is out of scope unless requested
    later.
- **Tooling:**
  - `shape plugins list|info|doctor`.
  - A cookiecutter-style example plugin under `examples/plugin/`.
  - A conformance test kit (`shape.testing.plugin_kit`) that third-party plugins can
    run in their own CI.
- **Packaging:** first-party plugins live in this repo under `plugins/<name>/`, each as
  its own distribution:
  - `sqllocks-shape-fabric`
  - `sqllocks-shape-kafka`
  - `sqllocks-shape-eventhubs`
  - `sqllocks-shape-domains`
  - `sqllocks-shape-sqlserver`

  `sqllocks-shape[all]` pulls them all in.

## 4. Phases

Sizes: S ≈ a few days, M ≈ 1–2 weeks, L ≈ 3–5 weeks, XL ≈ 6+ weeks of focused work.
Phases 0–2 are sequential and block everything else. Phases 3–8 can overlap once
Phase 2 lands.

### Phase 0 — Honest baseline (S)

Goal: the repo tells the truth, CI enforces what `make check` claims, and the
dangerous bugs are closed.

1. **Security hotfixes.** These are small, and each one gets a regression test:
   - SEC4 and SEC5: registry path traversal and cross-name reads.
   - SEC1: Laplace noise must come from `secrets`/OS randomness by default, with a
     fixed seed allowed only through an explicit, clearly named test hook.
   - SEC2: `release_for` must deny when policy says so, and redaction must strip
     min, max and quantiles from sensitive columns.
2. **Remove the unused modules** in the cut list (§5), together with their tests
   (§5 lists exact files).
3. **Archive the generated evidence.**
   - Move the ~35 root `*_MANIFEST.json`, `*_QUALIFICATION.json`, `GA_FINAL_TEST.txt`,
     `RC1_*` and `RQ*_*` files to `archive/cursor-build/`, or delete them.
   - Move the milestone docs (`docs/RQ*`, `docs/VALIDATION_*`, `docs/RC1_*`, `docs/GA_*`
     and similar) to the same place.
   - Keep: README, INSTALL, QUICKSTART, TUTORIAL, the architecture doc, `specs/`,
     SECURITY, THREAT_MODEL and this plan.
4. **Correct the claims.**
   - Rewrite `CHANGELOG.md` 1.0.0 → `0.x (pre-release)`.
   - Remove "fidelity-certified", "GA", "production streaming" and the
     plugin-isolation claims.
   - Replace `docs/SPINDLE_MIGRATION_MATRIX.md` with the parity table from §6, marked
     honestly.
5. **CI.**
   - Add `mypy` with a per-module strictness ratchet (`[[tool.mypy.overrides]]`).
   - Add a coverage report.
   - Mark the `rq/` scripts as non-evidence, or delete them once the Phase 1 benchmark
     suite replaces them.
6. **Benchmark harness (`benchmarks/vs_spindle/`),** built now so every later phase is
   measured against it:
   - **Datasets** (all generated with a fixed seed):
     - D1: 200k × 6 mixed CSV.
     - D2: 1M × 20 mixed CSV (int, float, decimal, string, date, timestamp, bool,
       nulls).
     - D3: 10M × 10 Parquet.
     - D4: 100k × 1,000 wide CSV.
     - D5: 5 related tables with PK/FK.
   - **Method:** in-process timing of read + profile, median of 5 warm runs, on the
     same machine for both tools. CLI end-to-end time and peak RSS are reported
     separately.
   - **Baseline:** Spindle `DataProfiler.from_csv` / `from_parquet`, and Spindle
     generation for the retail domain at medium scale.
   - **Output:** `benchmarks/vs_spindle/results.json`, committed per release. CI runs D1
     and D2 and fails if Shape regresses by more than 15% or drops below the gate.

**Exit:**
- CI is green with mypy included.
- The four security hotfixes are merged with tests.
- The harness runs and reports the current (slower) numbers.

### Phase 1 — Profiling engine rewrite (L) · the heart of the project

Goal: correct types and ≥10x Spindle on D1–D4.

1. **Readers → Arrow.**
   - Formats: CSV (`pyarrow.csv` with type inference and user-overridable schema),
     Parquet, JSONL, Arrow IPC.
   - In-memory: `dict[str, array]`, pandas/polars DataFrames (zero-copy where possible),
     and row iterables (adapter only).
   - Replace `connectors/files.py:CSVSource`.
2. **Type system.**
   - Supported types: int, float, decimal, bool, string, date, timestamp (with tz),
     time, binary.
   - Inference runs on the Arrow type first. Strings that look numeric or date-like are
     inferred per batch, and the rules are documented.
   - Type demotion keeps what has already been seen: a mixed column becomes `string`
     and keeps its count, null, top-k and cardinality evidence (fixes P2–P4).
3. **Columnar kernels (`profile/kernels/`),** one per type family and all vectorized:
   - counts, nulls, NaN/±inf
   - min/max, moments merged Welford-style
   - KLL quantiles fed from sorted batch samples
   - HLL fed by a vectorized seeded 64-bit hash:
     - numeric columns hash the value bits;
     - strings hash the Arrow offsets/data buffers, looping by position rather than by
       row;
     - canonicalize so that `1 == 1.0` and so that NaN is excluded (P7).
   - SpaceSaving updated with batch value counts (`pyarrow.compute.value_counts`), with
     a bounded heap and a correct merge that keeps error terms (P5, P6)
   - string length stats
   - regex/pattern classes
   - date/time part histograms (hour, day of week, month)
4. **Spindle profiler parity:**
   - enum detection, PK detection, cross-table FK inference (D5), outliers (IQR / MAD)
   - `pattern` for strings
   - best-fit distribution (normal, lognormal, exponential, uniform, gamma, beta,
     Poisson) with a KS statistic, fitted on a bounded reservoir sample. Closed-form MLE
     runs in numpy; scipy is an optional extra that adds more families.
5. **Exact versus sketch.** An `exact=True/False/auto` switch: `auto` is exact below a
   row threshold and uses sketches above it. The output schema is identical either way
   (P12), and the chosen algorithm is recorded in `error_models`.
6. **Bounded mode.** Batch streaming with constant memory for data larger than RAM
   (Parquet row groups, chunked CSV). Merging partition profiles is core behaviour,
   which replaces the lossy `distributed/` (B4).
7. **Downstream fixes.**
   - Contracts: `unique` uses exact counts when available, and otherwise compares
     against the HLL error bound (P14). Fix the null rate at 0 rows (P15).
   - Artifact reader: wrap `KeyError` and zlib errors in `ArtifactError` (P18).
   - Allow NaN/inf in artifacts through an explicit encoding (P8).
8. **Shape model v2 and `.shape` v2,** with a v1 → v2 read migrator and an updated
   JSON Schema.
9. **Tests.**
   - Hypothesis property tests: merge associativity and commutativity for every sketch;
     batch-split invariance (profiling in 1 batch equals N batches, within the error
     model).
   - Type-inference golden files.
   - Accuracy checked against exact values on D1–D4.

**Exit (gate):**
- On D1–D4, Shape's median read + profile time is **≤ 1/10 of Spindle's**. This is
  the hard gate; the stretch goal is ≤ 1/20.
  - **Evidence:** an Arrow prototype (exact value counts, top-k, min/max, moments,
    quantiles, string lengths) measured ~15x Spindle at 200k rows (0.13 s vs 2.0 s) and
    ~11x at 2M rows (1.7 s vs 18.7 s).
  - The prototype does not yet include sketches, pattern detection or distribution
    fits. Reaching 20x needs parallel column kernels, or the Rust kernel.
- Every column type matches Spindle or is more precise.
- HLL error is within the documented bound, and KLL rank error ≤ 1%.
- Peak RSS in bounded mode is constant as N grows. The check is D3 at 10M versus 100M
  rows.
- If the gate is missed after the Arrow/numpy kernels land, add the Rust kernel for the
  hash, HLL and KLL (the scope is limited to `profile/kernels/`).

### Phase 2 — Plugin host and API v1 (M)

1. Build `shape.api.v1` Protocols, entry-point discovery, version negotiation, a
   registry, and the `shape plugins` CLI (see §3).
2. Remove `plugins/core.py` and `plugins/runtime.py`, together with their capability
   claims (PL1–PL4). Document the trust model.
3. Port existing built-ins behind extension points, so core uses its own plugin API:
   - CSV, Parquet and JSONL sources and sinks
   - semantic detectors (email, phone, zip and similar)
   - distribution fitters
   - the address pack
4. Build the plugin conformance kit, add an example plugin, and write plugin author
   docs.

**Exit:**
- An out-of-tree example plugin adds a source, a detector and a CLI command without
  touching core.
- All built-ins are loaded through the registry.

### Phase 3 — Stream profiling hardening (M)

1. `streaming/platinum.py`: replace `hash()` with the core seeded hash, and use one
   binning scheme for the fast path and `update()` (S1).
2. Bound the heaps in `streaming/keyed.py` (S2). Add `restore()` for windows (S3).
3. Reconnect must resume from the committed offset instead of replaying batches (S4).
   Vectorize dedupe (S5).
4. Rebuild stream profiling on the Phase 1 kernels. A micro-batch goes through the same
   kernels as a file, and the profile is emitted per window, with checkpointing.
5. Move the Kafka and Event Hub consumers into the `sqllocks-shape-kafka` and
   `sqllocks-shape-eventhubs` plugins.
6. Tests:
   - replay determinism across processes (`PYTHONHASHSEED` varied)
   - crash/restore equivalence
   - late-data and watermark cases

**Exit:**
- A stream profile of a replayed file equals the batch profile of the same file,
  within the error model.
- Profiles are identical across processes.

### Phase 4 — Generation engine to Spindle parity (XL)

1. **Schema model.**
   - Import `.spindle.json` as-is into the Shape spec. This is the migration path for
     existing Spindle users.
   - Validate against `docs/specs`.
   - `shape from-ddl` (SQL DDL → schema).
2. **Engine.**
   - Topological table order, FK integrity (including composite and self-referencing
     keys), and chunked generation.
   - Deterministic random access: a row is a pure function of (seed, table, index).
     Fix G1 by mixing the seed through a hash instead of adding it to the index, and
     G2 (`row_at` must not depend on call order).
   - Arrow output batches.
3. **Strategies.** Implement all of Spindle's strategies as `shape.strategies` plugins,
   against the core API:
   - sequence, uuid, enum, distribution, empirical, pattern, faker-like, formula
   - computed, derived, conditional, correlated, lookup, reference_data, temporal
   - lifecycle, SCD2, first_per_parent, foreign_key, composite_foreign_key
   - self_referencing, record_field, record_sample, native
4. **Profile → generate loop.** `shape generate --from customer.shape` fits strategies
   from profile evidence, using the Phase 1 distribution fits and joint/copula
   evidence. Replace the fake `gaussian_copula` and the no-op `missingness` (G5).
5. **Fidelity report.** This replaces Spindle's `compare` and `profile diff`.
   - Per-column 0–100 scores, JSON / Markdown / HTML output, and non-zero exit on
     failure.
   - Fix G3 and G4: a missing column scores 0, and an empty reference fails.
   - `plan_reconstruction` must check what it claims (G6).
6. **Writers** (sinks): CSV, TSV, JSONL, SQL INSERT, Parquet as core; Delta and Excel as
   plugins.
7. **Performance gate** (target not yet confirmed by the owner):
   - **Baseline:** Spindle retail medium (1.97M rows, 9 tables), generated and written
     to Parquet, runs at **~300k rows/s** on a 4-core machine (measured 2026-09-29). The
     21k rows/s figure in `benchmarks/baseline-v2.3.0.json` is stale.
   - **Prototype:** a vectorized Arrow/numpy two-table prototype (customers + orders
     with a Pareto-skewed FK, derived emails, dates and lognormal amounts) runs at
     ~1.6M rows/s to Parquet on a single core. That is ~5x, on a simpler schema.
   - **Proposed:** ≥5x Spindle (≥1.5M rows/s) as the hard gate, and ≥10x (≥3M rows/s)
     as a stretch goal, using multi-core chunked generation. Row-sequential strategies
     (lifecycle, SCD2, state machines) are measured separately.

**Exit:**
- Every Spindle example schema (`sqllocks_spindle/domains/*`) generates in Shape with
  FK integrity 100%.
- The fidelity report passes against Spindle's own output on the same schema.
- The performance gate is met.

### Phase 5 — Streaming during generation (M)

1. Build the emitter runtime:
   - rate limiter (token bucket; constant, burst and diurnal profiles)
   - event envelope (CloudEvents-compatible)
   - backpressure, at-least-once delivery with idempotent keys
   - graceful shutdown with a checkpoint
2. Build the emitter plugins:
   - core: console, file, JSONL
   - plugins: Kafka producer, Event Hub producer, Fabric Eventstream, Eventhouse
3. Anomaly injection during streaming, using chaos plugins (Phase 6).
4. **Profile while generating:** tee the emitted stream into the Phase 3 stream
   profiler, so fidelity is checked live against the target shape, with alerts on
   drift.
5. CLI: `shape stream <schema|domain> --sink kafka://… --rate 5000/s --duration 1h`.

**Exit:**
- Sustained emission holds its target rate within ±5% for 1 hour against a local
  Kafka (docker in CI, nightly).
- The live fidelity check matches the offline fidelity report.

### Phase 6 — Spindle feature ports, as plugins (XL, parallelizable)

Each item is its own plugin package, built only against `shape.api.v1`:

| Feature | Spindle source | Shape plugin |
|---|---|---|
| 13 calibrated domains + presets + composite | `domains/` (13k), `presets/` | `sqllocks-shape-domains` (one entry point per domain) |
| Chaos engine (nulls, duplicates, schema drift, corrupt values) | `chaos/` | `shape.chaos` built-ins; replaces `scenarios/` |
| Masking (`mask`) | `inference/masker.py` | `shape.transforms` built-in |
| Simulation (IoT, clickstream, finance, state machines, file drops) | `simulation/` | `sqllocks-shape-simulation` |
| Incremental `continue` / `time-travel` | `incremental/` | core generation feature + CLI |
| Star schema / CDM transforms | `transform/` | `shape.transforms` |
| Fabric: Lakehouse, Warehouse, SQL DB, Eventhouse, Semantic Model, notebook deploy | `fabric/` | `sqllocks-shape-fabric` |
| SQL Server / Fabric SQL profiling (pyodbc + Entra) | `inference/database_profiler.py` | `sqllocks-shape-sqlserver` source |
| Lakehouse / Delta profiling | `inference/lakehouse_profiler.py` | `sqllocks-shape-fabric` source |
| Validation gates / quarantine | `validation/` | core `quality` + `contracts` |
| MCP bridge | `mcp_bridge*.py` | `sqllocks-shape-mcp` |
| Demo | `demo/` | `examples/` |

**Exit:** every Spindle CLI command has a Shape equivalent (§6), and each has at least
one end-to-end test.

### Phase 7 — Privacy and artifact trust (M)

1. **Differential privacy:** correct Laplace and Gaussian mechanisms using OS
   randomness, with budget accounting. Otherwise remove every DP wording.
2. **Release policy:**
   - one taxonomy (merge `privacy/policy.py` and `privacy/classification.py`; SEC3);
   - enforced minimum cohort;
   - redaction of every value-bearing key.
3. **Signing:** wire `artifact/secure.py` into `write_shape`/`read_shape`. Add
   `--sign/--verify` and document that checksums alone prove integrity, not origin.
4. **Threat-model refresh** for the plugin trust model and emitters (credentials handled
   through Azure identity and env/keyring, never written to artifacts).

### Phase 8 — Migration and release (M)

1. **Compatibility layer:**
   - `shape` accepts Spindle command names as aliases where semantics match;
   - reads `.spindle.json` schemas and Spindle profile JSON.
2. **Parity suite:** run Spindle and Shape on the same schemas and datasets, and compare
   profiles and generated-data fidelity in CI (nightly, pinned Spindle version).
3. **Docs:** migration guide, CLI reference, plugin author guide, and a performance page
   generated from `benchmarks/vs_spindle/results.json`.
4. **Release:**
   - TestPyPI, then PyPI, with a real 1.0 only after Phases 0–8 exit;
   - SBOM and signed provenance (the existing `release*.yml` workflows, re-validated);
   - deprecation notice in the Spindle README pointing to Shape.

## 5. Keep / cut

**Keep and rebuild:**
- `artifact`, `capture`, `profile`, `kernel` (folded into the profile engine), `spec`,
  `model`, `types`, `errors`, `api`, `cli`
- `diff`, `drift`, `quality`, `contracts`, `query`, `registry`, `relations`
- `streaming`, `connectors`, `plugins` (rewritten)
- `generation`, `packs`, `location`, `geospatial`
- `privacy`, `security`, `validation`

**Cut in Phase 0.** Nothing in the core, CLI or `__init__` imports these, apart from
the two noted. The cut order is `integrations` → `etl` → `ci`/`distributed`.

| Module | Reason |
|---|---|
| `admin`, `ai`, `marketplace`, `federation`, `enterprise`, `governance`, `graph`, `compiler`, `execution`, `reproducibility`, `integrations` | Stubs (12–97 lines); `enterprise` hard-codes an audit key |
| `explain.py`, top-level `policy` | Unused, crashes / duplicates `privacy.policy` |
| `history`, `lineage`, `observability`, `packages`, `reference` | Unused, and bugs B6–B8. Registry covers history; plugins replace packages |
| `distributed`, `etl`, `ci` | Lossy merge (B4) and duplicate result types (B5). Replaced by core partition-merge and `shape check` exit codes |
| `scenarios`, `temporal`, `testing`, `transform` | Replaced by the chaos, strategy and transform plugins in Phases 4 and 6 |
| `hub`, `webapp` | Not in Spindle, and have security bugs (SEC6, SEC7). The MCP bridge covers remote access |

Update the tests when cutting:
- **Delete these files:**
  - `tests/test_future_roadmap.py` (move its `privacy.advanced` cases first)
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
- **Code imports to remove:** `cli/main.py` imports `registry`, `contracts` and `query`
  (all kept). `webapp/core.py` imports `query`, and goes with the webapp.

## 6. Spindle CLI parity map

| Spindle | Shape | Phase |
|---|---|---|
| `generate`, `list`, `describe`, `validate`, `composite`, `presets` | `shape generate`, `shape domains list/describe`, `shape validate` | 4, 6 |
| `stream` | `shape stream` | 5 |
| `learn`, `export-model` | `shape profile` + `shape generate --from` | 1, 4 |
| `compare`, `verify` | `shape fidelity` (JSON/MD/HTML, exit codes) | 4 |
| `mask` | `shape mask` | 6 |
| `from-ddl` | `shape from-ddl` | 4 |
| `continue`, `time-travel` | `shape continue`, `shape time-travel` | 6 |
| `to-star`, `to-cdm` | `shape transform star/cdm` | 6 |
| `profile capture/diff/export/import/list/validate` | `shape profile`, `shape diff`, `shape inspect`, `shape validate` | 1 |
| `profile registry list/save/delete/tag/diff` | `shape registry …` | 1 |
| Fabric publish / notebook commands | `shape fabric …` (plugin command) | 6 |
| — (new) | `shape stream-profile`, `shape plugins`, `shape check` | 2, 3 |

## 7. Working rules

- Every bug in the appendix gets a failing test first, then the fix, in the same PR.
- No doc, changelog or README statement about performance, fidelity or security ships
  without a CI test or benchmark that enforces it.
- The benchmark harness runs on every PR touching `profile/`, `capture/`, `generation/`
  or `streaming/`.
- PRs stay phase-scoped. The phase exit criteria are the definition of done.

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

# Shape completion plan

Status: **approved build specification, v3.2** (2026-09-30) · Supersedes
`docs/plans/REMAINING_WORK.md` and `docs/plans/NEXT_WORK_PACKETS.md`

This document is the single source of truth for finishing Shape. Every product and
technical decision is settled in §2. The work is split into numbered work packages
(§7), each with dependencies, deliverables and mechanically checkable acceptance
criteria. Building every work package to its acceptance criteria, and passing every
phase gate, completes the project.

v3.1 includes every finding of three independent adversarial reviews. It also includes
`docs/plans/refengine_coverage.tsv`, a machine-checked map from every file in RefEngine's
package to the work package that ports it. Line references were re-verified against the
code on 2026-09-29.

Contents:
0. Builder guide (read first)
1. Environment setup
2. Decisions
3. Measured baselines and targets
4. Target architecture
5. Target repository layout
6. Execution rules
7. Work packages and phase gates
8. Keep / cut / delete lists
9. Owner actions (external)
10. RefEngine CLI parity map
11. Status tracker
12. Fabric demo track (48 hours)

Appendix A: Verified bug register

---

## 0. Builder guide (read first)

This plan is designed to be executed by an AI coding agent working in fresh sessions.
Follow this protocol exactly.

### 0.1 At the start of every session

**If your session was started as a §12 demo lane (L1, L2 or L3), follow §12 instead
of §11 when choosing work, and edit only your lane's paths (§12.5).** Everything else
in §0 and §6 still applies.

1. Read this section, §1 (Environment setup) and §6 (Execution rules) in full.
2. Read §11 (Status tracker). The **next work package** is the first one, in tracker
   order, whose status is `todo` and whose `Depends` are all `done`.
3. Run the environment setup (§1). Before touching code, confirm that
   `make check` or the existing test suite runs.
4. Read the whole entry for the next work package in §7, together with every bug in
   Appendix A that it lists under "Fixes".

### 0.2 While working on a work package

1. **Write the acceptance tests first,** from the acceptance criteria. Every bug ID
   gets a regression test that fails before the fix.
2. Implement until every acceptance criterion passes.
3. Satisfy the definition of done (§6.2).
4. Commit on the session branch (§6.3). Update the tracker row in the same commit.
5. Push, then move to the next work package.

Keep each work package self-contained. Do not start the next one while the current
one is failing.

### 0.3 What you may decide yourself

- **Implementation details inside a work package:** names of private functions, file
  splits within the §5 layout, algorithms that meet the acceptance criteria.
- **Fixing a factual error in this plan:** a wrong path, a wrong line number, or a
  RefEngine fact that turns out different. Fix it in a separate commit prefixed
  `plan-fix:`, put the evidence in the commit message, and add a row to the decision
  log (§2.3).

### 0.4 Stop and escalate: never improvise

Stop, record the problem in §2.3 with evidence, and report to the owner when any of
these happens:
- An acceptance criterion appears impossible to meet as written.
- A gate is still missed after the protocol in §6.5.
- A change would alter a decision in §2 (any D-xx or T-xx), a gate, or a tolerance.
- A work package needs something that doesn't exist and that this plan doesn't say
  how to create.
- An owner action (§9) is needed for the current step, and its fallback doesn't
  apply.

After escalating, continue with the next work package that doesn't depend on the
blocked one.

### 0.5 Reporting

At the end of each session, report:
- the work packages completed, with their commits;
- the gate status;
- any escalations;
- the next work package.

Never report something as done or passing unless you ran the check in that session
and saw it pass.

---

## 1. Environment setup

Every path used by the harness comes from these environment variables, which live in
`scripts/env.sh` (created by P0-00).
- **Shell state does not persist between an agent's tool calls.** Every command that
  uses these variables must start with `source scripts/env.sh &&`, run from the repo
  root.
- Harness scripts fall back to the same defaults when a variable is unset.
- **Nothing in the repo may hard-code a machine path** (P0-07 enforces this).

```bash
export SHAPE_ROOT="$PWD"                                  # repo root
export REFENGINE_ROOT="${REFENGINE_ROOT:-$HOME/refengine}"      # pinned RefEngine checkout
export REFENGINE_VENV="${REFENGINE_VENV:-$HOME/.venvs/refengine}"
export REFENGINE_PY="$REFENGINE_VENV/bin/python"
export SHAPE_VENV="${SHAPE_VENV:-$HOME/.venvs/shape}"
export BENCH_DATA_DIR="${BENCH_DATA_DIR:-$HOME/bench-data}"  # generated datasets (not in git)
export BENCH_OUT_DIR="${BENCH_OUT_DIR:-$HOME/bench-out}"     # scratch output (not in git)
```

### 1.1 Shape development environment

1. Python 3.11+ is required (`python3 --version`).
2. Rust stable, 1.85 or newer (required by `arrow-array` 59), is required from P1-01a onward:
   `curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal && . "$HOME/.cargo/env"`.
3. `python3 -m venv "$SHAPE_VENV" && "$SHAPE_VENV/bin/pip" install -U pip`
4. Before P1-01a: `"$SHAPE_VENV/bin/pip" install -e ".[dev]"`
5. From P1-01a onward: `"$SHAPE_VENV/bin/pip" install -e ".[dev]"` (maturin builds the
   extension), or `maturin develop --release` after changing Rust code.

### 1.2 Pinned RefEngine baseline (T-20)

RefEngine is public at `https://github.com/sqllocks/refengine`, and an anonymous clone
works. The pinned commit is 3.0.1, which is newer than the 3.0.0 on PyPI, so always
use git.

```bash
git clone https://github.com/sqllocks/refengine "$REFENGINE_ROOT"
git -C "$REFENGINE_ROOT" checkout 422e78df2267e73bb2fa976267e48cb437861e2f
python3 -m venv "$REFENGINE_VENV"
"$REFENGINE_PY" -m pip install -U pip
"$REFENGINE_PY" -m pip install "pandas==3.0.6" "numpy==2.4.6" "scipy==1.17.1" \
    "pyarrow==25.0.1" "click==8.5.0" "requests==2.34.2" "python-dateutil==2.9.0.post0" \
    "pyyaml>=6.0,<7" "scikit-learn>=1.3,<2" "psutil"
"$REFENGINE_PY" -m pip install --no-deps -e "$REFENGINE_ROOT"
"$REFENGINE_PY" -c "import sqllocks_refengine; print('refengine ok')"
```

- From P0-07 onward, `benchmarks/vs_refengine/setup_refengine.sh` performs exactly these
  steps, and writes `pip freeze` to `$BENCH_OUT_DIR/refengine_freeze.txt`.
- `pyyaml` is needed by RefEngine's packs and GSL, and `scikit-learn` by its fidelity
  tiers. Neither is in RefEngine's core dependencies, so without them those features
  silently degrade and parity checks pass vacuously.
- Run RefEngine's CLI as `"$REFENGINE_VENV/bin/refengine" …`. The console script comes
  from the editable install and is not on `PATH`.
- **Never modify files under `$REFENGINE_ROOT`.**
- `tests/demo/fabric` also needs the system unixODBC runtime (`libodbc.so.2`, Debian/Ubuntu
  package `libodbc2`): without it `import pyodbc` fails at collection (INT-18 shards R1, P2).

### 1.3 Docker and external services

- Emulator-backed tests (Kafka, the Azure Event Hubs emulator with Azurite, and SQL
  Server) run **only in GitHub Actions nightly jobs**, using
  `ci/emulators/docker-compose.yml`. Cloud builder sessions usually have no Docker
  daemon.
- Locally, run the contract tests only, with `pytest -m "not emulator and not live"`.
  Check the emulator results from CI.
- To read CI results in a session without the `gh` CLI, use the GitHub MCP tools:
  `actions_list`, `actions_get` and `get_job_logs`.

### 1.4 Benchmark hygiene

- Take every timed benchmark under an exclusive lock:
  `flock "$BENCH_OUT_DIR/bench.lock" …`.
- Check the load average first. If it is above 1.5 on the 4-core baseline machine,
  wait before timing.
- Never keep persistent caches between timed runs: delete
  `$BENCH_OUT_DIR/<run>` before each one.

---

## 2. Decisions

These decisions are final. Record changes only in §2.3, and only on the owner's
instruction.

### 2.1 Product decisions

| ID | Decision | Rationale |
|---|---|---|
| D-01 | **Shape replaces RefEngine completely.** That covers profiling, including the advanced tiers; generation (every domain, strategy, chaos mutator, simulation, incremental mode, transform, scenario pack and GSL spec); the scale router (local, multiprocess, Fabric Spark); Fabric integration; the JSON bridge; MCP; and the demo. **Nothing in RefEngine is out of scope:** every file in its package is mapped to a work package in `docs/plans/refengine_coverage.tsv`. | Owner decision. |
| D-02 | **Streaming works in both directions.** Shape profiles streams it consumes (Kafka, Event Hubs) and emits streams during generation (console, file, Kafka, Event Hubs, Fabric Eventstream, Eventhouse). | Owner decision. |
| D-03 | **Features are plugins built on core features.** First-party features use the same plugin API as third parties. | Owner decision. |
| D-04 | **Performance: 10x RefEngine is the minimum and 30x is the stretch goal, for profiling and generation,** measured 1:1 on equivalent work (§3, T-19). | Owner decision. |
| D-05 | **Realism goes beyond RefEngine.** All of RefEngine's distributions and temporal patterns, plus the additions in P4-05: more families, mixtures, 80/20 helpers, date-specific holiday calendars, payday and period effects, and trends. | Owner decision. |
| D-06 | **RefEngine profiler parity is a hard requirement:** every field of RefEngine's `ColumnProfile` and `TableProfile`, including distribution fitting, pattern detection, enum detection, and PK/FK detection including across tables. | Owner decision. |
| D-07 | **Remove Shape's fake DP now; port RefEngine's DP correctly later.** Phase 0 deletes `privacy/advanced.laplace`, which uses a fixed seed (SEC1). P4-11 ports RefEngine's experimental `inference/tier3_research.DifferentialPrivacy` (Laplace and Gaussian mechanisms) with RefEngine's API. Noise comes from OS randomness unless a seed is passed explicitly, and the docs describe it as RefEngine does, as experimental. Keep k-anonymity, suppression, redaction, minimum-cohort enforcement and safe-profile parity. | Full parity (D-01), but never with deterministic noise. |
| D-08 | **Delete the hub, the web app and 25 other unused or stub modules** (§8.1). Remote access is provided by the JSON bridge (`shape bridge`); the MCP server is a separate private commercial component (2026-10-02, §2.3). | Not in RefEngine; no dependents; security bugs. |
| D-09 | **Plugins are trusted, in-process code.** Delete the subprocess "capability sandbox" and its claims. | The sandbox is cosmetic (PL1–PL4). |
| D-10 | **Copy RefEngine's reference data** (name and street pools, ZIP locations, domain reference files) into `shape-domains`. Carry the GeoNames CC-BY-4.0 attribution into `THIRD_PARTY_NOTICES.md`. | Parity needs the same data. RefEngine is MIT and has the same copyright holder. |
| D-11 | **Holiday calendars are rule-based code:** fixed dates, the nth or last weekday of a month, Easter by computus, and observed-day shifts. US federal and US retail calendars ship in core, and other countries come as `shape.calendars` plugins. | Offline and deterministic. |
| D-12 | *(Revised 2026-09-30, §2.3: `_shape_*` field names, no `--envelope refengine`.)* **Stream output defaults to RefEngine's flat-row format.** Each event is the row's columns plus `_refengine_table` and `_refengine_seq`, and `_refengine_event_time` when the table has a datetime column (RefEngine `streaming/streamer.py:212-233`). `--envelope refengine` produces RefEngine's `EventEnvelope` (as used by its Eventstream client), and `--envelope cloudevents` produces CloudEvents. The idempotency key is `(_refengine_table, _refengine_seq)`. | Drop-in for existing consumers; deterministic replay. |
| D-14 | **Pipeline integration comes first after the plugin system.** Fabric first (Python and PySpark notebooks, User Data Functions, pipelines with Notebook and Functions activities), then Synapse and ADF. Phase F runs after G2, before Phases 3 and 4. A 48-hour Fabric demo track (§12) comes before everything. | Owner: RefEngine is retired; profiling and generation must run inside ADF, Synapse and Fabric pipelines. |
| D-15 | **Shape is open source under the MIT license** (the same as RefEngine), replacing the Apache-2.0 `LICENSE` inherited from the earlier build. The `sqllocks/shape` repo goes public; releases go to PyPI as `sqllocks-shape`. The repo flips to public only after DM-00 has removed the false claims (§12). | Owner decision (2026-09-30). A full-history secret scan found only deliberate test fixtures. |
| D-13 | *(Revised 2026-09-30, §2.3: no RefEngine aliases and no RefEngine schema or profile input.)* **No `refengine` executable is shipped.** `shape` accepts RefEngine command names as aliases wherever the meaning matches (§10), and reads RefEngine schemas and profiles. | Avoids clashing with an installed RefEngine. |

### 2.2 Technical decisions

| ID | Decision | Rationale |
|---|---|---|
| T-01 | **Runtime split: Arrow for data format and I/O, Rust for the entire per-batch data path, Python for orchestration and plugins** (§4.2). | Measured: ~7 µs per Python→native call, and Parquet writing is ~50% of vectorized generation time. |
| T-02 | **Rust kernel:** a single crate `rust/shape-kernel`, exposed as `shape._kernel`. Pinned versions: `pyo3 = { version = "0.29", features = ["abi3-py311", "extension-module"] }`, `pyo3-arrow = "0.19"`, `arrow-array`/`arrow-buffer`/`arrow-schema = "59"` (the versions `pyo3-arrow` 0.19 requires), `rayon = "1.12"`, `xxhash-rust = { version = "0.8", features = ["xxh3"] }` (`xxh3_64_with_seed`), maturin 1.15. Zero-copy exchange uses the Arrow PyCapsule interface (pyarrow ≥14). | A known-compatible set. |
| T-03 | **Every kernel function has a pure-Python reference twin** under `src/shape/kernel/reference/`. `SHAPE_KERNEL=auto|rust|python` selects the implementation (default `auto`). Differential tests assert that the two agree. The Python reference hashes through the `xxhash` package (≥3.5), which is a `[dev]` dependency and is imported only on the reference path. If it is missing, the reference path raises a clear `ImportError`. | Correctness oracle and fallback. |
| T-04 | **Wheels built with `PyO3/maturin-action`:** manylinux_2_28 and musllinux_1_2 for x86_64 (native) and aarch64 (native on `ubuntu-24.04-arm`); macOS arm64 and x86_64 (cross-compiled on `macos-14`); win_amd64. They are abi3 wheels, one per platform, test-installed on Python 3.11 and 3.14. The sdist builds with Rust ≥1.85. | Avoids redundant builds per Python version and slow QEMU emulation. |
| T-05 | **Build backend:** maturin, with a mixed Python/Rust layout (`python-source = "src"`, `module-name = "shape._kernel"`). | Needed for T-02. |
| T-06 | **Python 3.11–3.14.** Tests run on the full matrix on Linux, and on 3.11 plus 3.14 on macOS and Windows. | Current floor and current release. |
| T-07 | **Core dependencies are `numpy>=2.0,<3` and `pyarrow>=14.0.1` (no upper bound), plus `tzdata` on Windows only (`tzdata; sys_platform == "win32"`, owner 2026-10-02: named time zones work out of the box where the OS has no time-zone database).** Remove `pydantic` (unused) and `typing-extensions`. Move `cryptography` to `[sign]`, and make `shape.security` import `crypto` lazily. Remove the `pyarrow<24` pin. The install range must admit the Fabric UDF SDK, which pins `pyarrow>=19.0.1,<20` (`fabric-user-data-functions` 1.0.x), and Fabric runtimes' preinstalled pyarrow; 14.0.1 is the first release without CVE-2023-47248. The benchmark harness still pins pyarrow 25.0.1 in both venvs (§1), so every performance number uses the same pyarrow. CI tests both the newest pyarrow (main matrix) and 19.x (the `fabric-demo` job). | Import time, install size, Fabric UDF compatibility, and a fair Parquet comparison. |
| T-08 | **Extras:** `[sign]`, `[scipy]`, `[kafka]`, `[eventhubs]`, `[fabric]`, `[sqlserver]`, `[domains]`, `[simulation]`, `[dbt]`, `[healthcare]`, `[excel]`, `[delta]` and `[all]`. Each plugin extra depends on the matching `sqllocks-shape-*` distribution. `[dev]` adds pytest, pytest-cov, hypothesis, ruff, mypy, pip-audit, build, maturin, xxhash, import-linter, vulture, bandit and py-spy. | Mirrors RefEngine's extras. |
| T-09 | **First-party plugins live under `plugins/<dist-name>/`,** each with its own `pyproject.toml`. Their versions are kept in lockstep with core, and they are released together. | One CI and atomic API changes. |
| T-10 | **Package and version.** `sqllocks-shape` is not on PyPI (confirmed 404 on 2026-09-29). The version is `0.9.0.devN` during the build and becomes **1.0.0 at G8**. | Nothing published yet. |
| T-11 | **Shape model v2 and `.shape` format v2,** with one schema for every path. There is a read-only v1→v2 migrator, and v1 writing is removed. | Unpublished, so it can break freely. |
| T-12 | **mypy strict for every module a work package creates or rewrites.** A ratchet list in `pyproject.toml` tracks the rest. All of `src/shape` must be strict by G8. | Makes `make check` truthful. |
| T-13 | **Hashing:** seeded XXH3-64 everywhere, never Python `hash()`. Values are canonicalized first: integers and integral floats hash equal, NaN and null are excluded, strings are hashed as UTF-8 bytes, and timestamps as int64 in their unit, normalized to µs. | Deterministic across processes and platforms. |
| T-14 | **Sketches:** HLL with p=14 (dense, with bias correction), KLL with k=200, and SpaceSaving with capacity 64. All are bounded and mergeable, and SpaceSaving's merge keeps its error terms. | Bounded memory. |
| T-15 | **Two modes, one output schema.** `exact=True` is the default for files and tables, and computes every statistic exactly, as RefEngine does. `exact=False` (bounded mode) uses the T-14 sketches, for streams and larger-than-RAM data. `error_models` records which one was used. **Parity (T-22) and every PROF gate run with `exact=True`,** on the same output that was timed. Bounded mode is validated only against its error bounds. | Fairness: Shape never does less work than RefEngine when being timed. |
| T-16 | **Generation RNG: Philox4x64-10, implemented in `rust/shape-kernel/src/gen/rng.rs`** (there is no maintained crate). The known-answer oracle is `numpy.random.Philox(key=…, counter=…).random_raw()`. Streams are keyed by (seed, table, column, chunk). Random access by row, and results independent of chunk layout. Bit-identical output with RefEngine is **not** required; statistical equivalence under T-21 is. | Parallel, deterministic generation (G1, G2). |
| T-17 | **Parquet output:** pyarrow `ParquetWriter`, snappy compression (RefEngine's default), dictionary encoding on. Tables are written in parallel, and writing is pipelined with generation (chunk *n* is written while chunk *n+1* is generated). | Writing was ~50% of the port's time (§3.2). |
| T-18 | **The CLI stays Python,** with argparse and a plugin command registry, and there is no native binary. Start-up budget: ≤300 ms. Heavy modules and plugins load lazily. `shape profile <glob|dir>` handles many files in one process. | Measured start-up (§3.1). |
| T-19 | **Performance measurement:**<br>• **Primary gate (in-process):** timed inside the process, with start-up and imports excluded for both tools. Each measurement is the median of 5 runs, each in a fresh process after 1 warm-up (3 runs on PR smoke jobs).<br>• **Secondary gate (CLI end to end):** start-up included for both tools, for inputs of ≥1M rows.<br>• **Threading:** both tools use their default threading. Shape's default is all cores, and single-threaded Shape (`SHAPE_THREADS=1`) is always reported alongside.<br>• **Gates are ratios** measured in the same job on a **4-vCPU** runner, the same size as the baseline machine.<br>• **PR regression threshold:** 10% against the last nightly median. | Fair, repeatable and noise-tolerant. |
| T-20 | **RefEngine baseline:** pinned at git `422e78df2267e73bb2fa976267e48cb437861e2f` (3.0.1), with the §1.2 environment. It is upgraded only by an explicit PR that re-records every baseline. | Reproducible comparisons. |
| T-21 | **Generation equivalence standard** (the rule implemented in `benchmarks/retail_1to1/verify.py`, which passes 60/60 columns). Shape output is equivalent to RefEngine's (reference seed 42, Shape seed 1042, RefEngine baseline seeds **exactly** 43, 44, 45 and 46) when all of the following hold. The seed set is fixed: verifiers must refuse to run with any other baseline set, and a verdict from any other set counts for nothing. Some columns vary heavily between RefEngine's own seeds; at large scale, `order.order_total` KS ranges 0.14–0.37 across RefEngine seeds 43–47, because a handful of popular products dominate. A smaller baseline gives false failures, and a different one invites seed-shopping.<br>(a) identical table names, column names and order, Arrow types (`large_string` ≡ `string`) and row counts;<br>(b) per column, the null rate is within max(5σ, 1.5 × RefEngine's seed-to-seed drift);<br>(c) numeric and datetime columns have KS ≤ max(critical value at α=0.001, 1.5 × RefEngine's maximum seed-to-seed KS + 0.002);<br>(d) low-cardinality categorical columns have TVD ≤ max(3 × multinomial noise, 1.5 × RefEngine's seed-to-seed TVD + 0.002), and vocabulary overlap ≥0.999;<br>(e) pooled high-cardinality strings have vocabulary overlap ≥0.999 against RefEngine's output plus its pools, or parse at component level into pool members;<br>(f) 100% FK integrity, and FK fan-out within RefEngine's seed-to-seed range;<br>(g) 0 violations from RefEngine's `BusinessRulesEngine.validate`;<br>(h) **asserted per table:** RefEngine's `FidelityComparator` score for Shape ≥ (min over seeds 43–46 of RefEngine vs RefEngine) − 0.5. | The proven standard, restated exactly. |
| T-22 | **Profiling parity standard,** checked field by field against RefEngine's `DataProfiler` (`inference/profiler.py:191`) with `exact=True`:<br>• **Exact match:** dtype, null counts, cardinality, uniqueness, enum flags, `enum_values`/`value_counts_ext` (top 500), PK/FK and pattern.<br>• enum weights within 1e-9;<br>• mean and std within 1e-9 relative;<br>• quantiles using RefEngine's interpolation method, within 1e-9 relative;<br>• the same distribution family, with parameters within 1e-6 relative.<br>It applies to every dataset produced by `benchmarks/vs_refengine/profile_1to1/datasets.py`: D1, D2, D3, D4, MT (multi-table) and every EDGE variant. | The 1:1 port meets it; D-06. |
| T-23 | **Delete the stale evidence files** rather than archiving them; git history keeps them. | Removes misleading claims. |
| T-24 | **Docs:** mkdocs (`mkdocs>=1.6,<2`, `mkdocs-material>=9.5,<10`). The CLI reference is generated from argparse, and the performance page from `benchmarks/vs_refengine/results.json`. | Docs can't drift from the code. |
| T-25 | **Release:** GitHub Actions trusted publishing (TestPyPI, then PyPI), a CycloneDX SBOM, and Sigstore build attestations. | Supply-chain hygiene. |
| T-26 | **External services are tested at three levels:**<br>(a) contract tests with recorded or mocked APIs (marker `contract`), on every PR;<br>(b) emulators (marker `emulator`), nightly in GitHub Actions: `confluentinc/cp-kafka`, `mcr.microsoft.com/azure-messaging/eventhubs-emulator` with an Azurite sidecar and a `Config.json` (amd64 only), and `mcr.microsoft.com/mssql/server:2022-latest` (`ACCEPT_EULA=Y`, with `msodbcsql18` installed) — all defined in `ci/emulators/docker-compose.yml`;<br>(c) live tests (marker `live`), run only when owner secrets exist (§9). | Completion never depends on credentials. |
| T-27 | **Lint and format scope:** `src tests plugins benchmarks/vs_refengine rust` (plus `cargo fmt --check` and `cargo clippy -D warnings` for Rust). Run `ruff format` once, in P0-06. | `ruff check .` currently reports 762 errors, all in files that are being deleted or moved. |
| T-29 | **Dual packaging.** Every release ships (a) platform wheels with the Rust kernel (T-04) and (b) a **pure-Python `py3-none-any` wheel** of the same version, built by `scripts/build_pure_wheel.py`, which uses the T-03 reference kernels. The pure wheel stays under 28.6 MB and needs only numpy, pyarrow and pandas from PyPI. pip prefers the platform wheel when one matches; Fabric UDF private libraries require the pure one. | Fabric UDF private libraries must be platform-independent and under 28.6 MB (§12.1). |
| T-28 | **Profiling:** `py-spy record` for hotspots. Use `--native` or `perf` only where ptrace and `perf_event_paranoid` allow; otherwise fall back to `cProfile` plus Rust `tracing` spans. | Works in restricted containers. |

### 2.3 Decision log

| Date | ID | Change | Reason |
|---|---|---|---|
| 2026-10-05 | INT-21a, E1, E2 | **Lead decisions on INT-19's escalations E1 and E2** (contradictions between two lanes' tests; landed as INT-21a, branch `fix/E1-E2`). **E1 (#283, #396): the refusal is correct.** The raw-profile sniff (`is_raw_profile`, `profile_capture`) refuses a `.shape` container whose manifest no Shape reader accepts with `RegistryError` (`not a Shape container: manifest too large`), the safer behaviour, and never inflates it; `tests/registry/test_aud_privacy.py::test_the_manifest_sniff_does_not_inflate_a_bomb` now expects that refusal and keeps its peak-memory bound (16 MiB); no other assertion changed. **E2 (#281): refuse, as INT-20 decision 1 (refusal over a silent rename).** `PackRunner.run` refuses a domain name that is a path (`is_safe_name`) before anything is generated or written, with an error naming the name; `tests/scenario/test_run_paths.py::test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output` is kept as is, and AUD-scenario's test that asserted the sanitised run id now asserts the refusal (`tests/scenario/test_audit.py::test_281_a_domain_name_with_a_path_is_refused_and_nothing_is_written`, failing before the fix). A scale name keeps its plain-name run id (not part of the decision). `tests/registry`, `tests/scenario`, `tests/security`, `tests/cli`: 1833 passed in each kernel; `make check`'s static steps clean. No gate, tolerance, D-xx or T-xx changed; T-07's floors stay. | Lead decisions 2026-10-05 |
| 2026-10-05 | INT-20 | **Integration INT-20 (with INT-19) merged into build/main-plan** (668b6b10). INT-20: AUD-chaos, AUD-io, AUD-pluginfw, AUD-stream, AUD-tests, BUGS-builtins-1, BUGS-contracts, BUGS-demo-2, BUGS-demo-3 (#308, #309, #310), BUGS-gen-1, BUGS-kernel-2, BUGS-packaging-1, BUGS-plugins-2, BUGS-portability-1, BUGS-profile-1, BUGS-stream-1, BUGS-tests-1, BUGS-721 (#721), SEC-high, HUNT2 (cli, io, kernels, plugins, privacy, profile, quality, scenario, generation, streaming), OWN-425, OWN-429, G6-eval, G7-eval (gates G6 and G7 stay `todo`), P8-03, PF-05-fin, W7-01 (wip), W7-03, W7-05b, W8-01 (wip), W8-02, W8-03, W8-06 (#766). W1-15 and W8-04 were merged and reverted before landing (below). **Sharded verification** of 35619600 on `verify/INT-20-P1`..`P4`, `R1`..`R3` (`docs/plans/evidence/INT-20/shard_*.md`). Shard P4's NEW failures are fixed before landing: shape-dbt built the DDL parser's columns with `raw_type`, which AUD-gen removed (0f2f75c6); the docs site nav named `GENERATION_STABILITY.md`, which the W1-15 hold removed (3eb47e21); the faker test of `tests/plugins/test_optional_plugin_pieces.py` needs the domains plugin, so it moved into `plugins/shape-domains/tests` (not skipped). shape-dbt's suite with dbt installed and the moved test, in both kernels: `docs/plans/evidence/INT-20/dbt_and_plugins.md`. Shards P3 and R2 (and R3): the streamed-CSV schema probe of INT-18's c1999097 lacked the lanes' duplicate-name refusal (#734) and missing-column `ReaderError` (#499) (c5f9a21f), and `reconcile` reported the same infinity on both sides as a difference, which pyarrow 19's per-key max of a NaN-only group exposed (#572; 9e24dfe9, test first). Two pyarrow 19 failures are the pre-existing #76 class. Still failing, as on int/INT-19: its escalated E1 (#283: `tests/registry/test_aud_privacy.py::test_the_manifest_sniff_does_not_inflate_a_bomb` expects `False`, the #283 fix refuses) and E2 (#281: `tests/scenario/test_run_paths.py::test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output` expects a refusal, the merged tree makes the name safe), contradictions between two lanes' tests that wait for the lead. **T-07 kept** (numpy>=2.0,<3, pyarrow>=14.0.1); the floor raise stays an open owner escalation. Merges, conflicts, fixes and checks: `docs/plans/lane_status/INT-20.md`. | Lead decisions 2026-10-05; shard evidence on `verify/INT-20-*` |
| 2026-10-05 | INT-19 | **Integration INT-19 landed on build/main-plan inside INT-20** (int/INT-20 merged int/INT-19 at 3a51d44d; 668b6b10): P3-04, P4-10r2, G1-mt, W3-01 (#99), W3-03 (#101), W3-05 (#102), W5-03 (#233), W5-08 (#84, wip), W6-03 (#235), W7-04 (#142), HUNT2-fabric, BUGS-cli-1 and the audit lanes AUD-bridge, AUD-builtins, AUD-ci, AUD-design, AUD-docs, AUD-fabric, AUD-gen, AUD-kernel, AUD-packaging, AUD-portability, AUD-privacy, AUD-profile, AUD-scenario, AUD-security2. W1-17 is not merged as a lane (its code came with INT-18 through lane/W7-05; it stays wip). Merges, fixes and checks: `docs/plans/lane_status/INT-19.md`. | Lead decisions 2026-10-04 and 2026-10-05 |
| 2026-10-05 | W1-15, W8-04, #768 | **Lead: W1-15 and W8-04 are both held after the cross-CPU proof failed** (INT-20 decision 8 (b)). W1-15 was re-applied on INT-20 and its pinned fixtures and W8-04's golden byte corpus were run with numpy's AVX-512 paths disabled: 33 of 405 tests failed in each kernel (the pinned dataset ids of the seven continuous distribution families, drawn through numpy's CPU-dispatched transcendental functions, and the byte corpus). Both are reverted on INT-20 (134b3c1c, 535e9274) and wait for a lane that makes the distribution generators bit-identical across CPUs; then W8-06's faker generator version 2 (b72c69d5) and the SQL `format_version` 2 corpus (87d706c3) come back with them. #768 is decided as this hold and stays open until that lane lands. | Lead decision 2026-10-05; `docs/plans/lane_status/INT-20.md` |
| 2026-10-05 | #721 | **Owner: Fabric User Data Functions withhold the raw `min`/`max` of classified columns by default** (lane BUGS-721). Check violations and diff changes carry no raw value of a classified column; `include_raw_values`/`includeRawValues` (default false, added last to each binding) opts in; unclassified columns are unchanged. | Owner decision 2026-10-04 |
| 2026-10-05 | #395 | **Lead: the safe profile withholds value statistics below k** (SEC-high): a column with fewer non-null rows than its k gets null `mean`, `std`, `quantiles`, `bounds` and `distribution_params` (`--unsafe-full-fidelity` keeps them), with the validator rule `small-cohort-statistic`. The safe-profile parity harness records this as one named difference, checked on both sides; `safe_profile_1to1/verify.py` exits 0. | Lead decision 2026-10-05 |
| 2026-10-05 | #535 | **Lead: bridge `verify` withholds the range gate's actual extreme only for classified columns** (unless `include_raw_values`); an unclassified column keeps the frozen 1.1 text. The message reads `(actual max withheld)` and the gate says `"redacted": true`; `docs/BRIDGE.md` states it. | Lead decision 2026-10-05; `docs/plans/lane_status/INT-20.md` |
| 2026-10-05 | W8-06, #766 | **Owner: one switch turns on realistic identifiers for a whole run** (`identifiers="realistic"`, `--identifiers`, a schema default); reserved identifiers stay the default. A column's own `domains`/`range` key wins; both kernels give the same values. | Owner request 2026-10-04 |
| 2026-10-05 | P6-01, T-21 | **Lead: P6-01's domain verifiers may record a reserved-identifier column as a named allow-list entry under the owner's 2026-10-01 policy, for the `vocab` check only** (as retail `customer.email` in `domain_1to1/domain_differences.py`: reserved values cannot be in the baseline's vocabulary; a stale entry fails the run). No other clause (distinct ratio, clause (h)) is allow-listed; P6-01-fin's escalations (telecom `usage_record.destination`, marketing `web_visit`) stay with the owner. | Lead decision 2026-10-05; owner policy 2026-10-01 |
| 2026-10-05 | INT-20 decisions 1-5 | **Lead answers on INT-20's conflicts** (`INT-20.md`, decisions 1-5): (1) #724 kept: a T-SQL name with a line break is refused and a value is split only at a `GO` line; (2) W7-03's mixtures and seasonality are opt-in with the univariate depth (T-19 profile cost, as for W3-07 and W3-08); (3) #541: the bridge `demo_*` commands run from `api_version` 1.2, a 1.0/1.1 request gets its old answer, so the frozen vectors hold; (4) #650: a joint block is withheld whole when every column it covers is above the target, otherwise filtered, then small cells are suppressed; (5) #167 kept: a repeated CSV header gets unique names (`a`, `a.1`). | Lead decision 2026-10-05; `docs/plans/lane_status/INT-20.md` |
| 2026-10-05 | INT-20 decisions 7, 9, 10 | **Lead answers on INT-20's conflicts** (`INT-20.md`): (7) #701 is registered, not reverted: `shape demo cleanup --dry-run` leaves a local file that is already gone alone, recorded as the named demo-parity difference `dry-run-judges-files` with a probe of both sides and negative-control cases (db2ffccb; owner's 2026-10-01 policy); (9) SEC-high's fixes merged with the same issues' earlier fixes: the SQL sink follows decision 1 with SEC-high's `tsql_text` for `GO`-line values and PostgreSQL `E''` literals, #535 as above, both redirect fixes of #275 on, #650 as decision 4 then SEC-high's suppression; (10) BUGS-demo-3 merged in round 3; P6-01-fin not merged (its status does not say complete). Decision 6 is the T-07 escalation already logged. | Lead decision 2026-10-05; `docs/plans/lane_status/INT-20.md` |
| 2026-10-05 | INT-17 | **Integration INT-17 landed on build/main-plan inside INT-18** (int/INT-18 started from int/INT-17 at 8683435d; 4c064997): W1-18 (#94, plugin allow-list and reach statement), W2-06 (#95, wip: `hidden: true` gap), W2-08 (#96, Snowflake, Databricks and Synapse sinks; live tests wait on secrets, §9), W3-02 (#100, rule proposals), W5-04 (#80, contract emission), W5-10 (#86, fingerprints and share bundle), W6-04 (#87, VS Code extension, data dictionary), BF-76 (min-versions CI job), ISS2-bugs. | Lead decisions 2026-10-04 and 2026-10-05 |
| 2026-10-05 | INT-18 | **Integration INT-18 (with INT-17) merged into build/main-plan** (4c064997). INT-18: W1-10 (#88), W1-11 (#89), W1-12 (#90), W1-13 (#230), W1-14 (#91), W2-07 (#231), W2-09 (#97), W2-10 (#98), W3-07 (#103), W3-08 (#232), W3-11 (#104), W3-12 (#105), W3-13 (#106), W4-01 (#79), W5-05 (#81), W5-06 (#82), W5-07 (#83), W5-09 (#85), W6-01 (#234), W7-02 (#141, wip), W7-05 (#227, wip, carrying W1-17, W3-01, W3-03, W3-05 and W7-04 as wip), W7-06 (#143), W7-07 (#145), AUD-api, AUD-cli, AUD-quality, AUD-dbplugins, AUD-perf, BF-223, DEMO-REHEARSAL, BUGS-contracts (#462-#464), BUGS-win (#769, #770), PF-05-fin. W1-15 was merged and reverted before landing (below). Integration fixes, checks, sharded verification (P1-P4, R1-R3) and the benchmark gate: `docs/plans/lane_status/INT-18.md`. The PostgreSQL COPY and MySQL append fixes (#767) were confirmed by the databases-e2e job of Nightly run 37251176423 on int/INT-18 (ae836806). **Shard P1 failed (NEW, #771) and is fixed before landing (c1999097):** with the Python kernel the heavy `test_bounded_mode_memory_does_not_grow_with_rows` saw the 24M-row peak 10-20% above the 48M-row peak; the cause was ISS2-bugs' identifier scan (#46) opening readers of the CSV whose background read-ahead outlived them (80-140 MB more early peak). The schema and the first-block scan now come from the file's first blocks in memory; the test (unchanged, bound unchanged) passed twice in a row on the fixed tree (1 h 40 min, then 1 h 48 min). **T-07 kept:** BF-76's raise of the core floors to numpy>=2.3 and pyarrow>=19.0.1 is not landed (f5dd2f62); `pyproject.toml` keeps T-07's numpy>=2.0,<3 and pyarrow>=14.0.1, and BF-76's finding (pyarrow 14 cannot be imported with numpy 2; Shape needs pyarrow 19.0.1 features; bitwise kernel equality from numpy 2.3) is escalated to the owner. | Lead decisions 2026-10-04 and 2026-10-05; shard evidence on `verify/INT-18-*` |
| 2026-10-05 | W1-15, W8-04 | **Lead: W1-15 is held for W8-04 (#567).** Its pinned dataset ids differ on macOS (40 tests on the CI macOS legs, #768), which is W8-04's byte-identical-output scope. The INT-18 merge of lane/W1-15 is reverted (bea22e4a); W1-15 stays `todo` and lane/W1-15 is merged again with or after W8-04. W8-04's `Depends: W1-15` therefore means lane/W1-15's code, not a `done` row. | Lead decision 2026-10-05; CI run 37233900952 |
| 2026-10-05 | P6-12, DEMO-REHEARSAL, #304, #307 | **Lead: two named demo-parity differences approved** under the owner's 2026-10-01 policy (fix baseline behaviour that misleads; record each difference narrowly with checks on both sides): `rows-are-what-runs` (#304: the baseline's estimate and dry-run line print `--rows`, its run writes the scale preset's rows; Shape prints and estimates the preset's rows) and `description-names-what-runs` (#307: the baseline's `adventureworks` description names four tables its run never generates). Each has a probe that confirms the baseline still behaves so and Shape does not, the baseline's text is rewritten only from its own outputs, and the negative control tampers with both. `demo_1to1/verify.py --negative-control`: VERDICT PASS (79379652). | Lead decision 2026-10-05; owner policy 2026-10-01 |
| 2026-10-05 | W5-05, #767 | **Lead: `shape seed --mode append` that would duplicate a primary key fails before anything is written** (W5-05's docs said only "append adds the rows"). The error (exit 1) names each clashing table, its key and a value, and never skips rows. Applied with one factual correction: the message suggests `--mode truncate` (there is no `replace` mode) or an empty database, and not "a different `--seed`", because the generated keys do not depend on the seed (retail's are sequences from 1). The emulator test now expects the refusal and unchanged contents, plus an append into missing tables (f05d6a81). | Lead decision 2026-10-05; Nightly 37233890609 databases-e2e |
| 2026-10-05 | W5-05, #767 | **Lead: the PostgreSQL COPY fix is committed** (956f78fa: a floating column declared `integer` reaches the database sinks as int64; a value that is not whole is refused with the column named). Confirmed by the databases-e2e job of Nightly run 37251176423 (success). | Lead decision 2026-10-05 |
| 2026-10-05 | T-07, BF-76 | **Escalation (lead): BF-76's raise of the core dependency floors is not landed.** BF-76 (INT-17) set numpy>=2.3,<3 and pyarrow>=19.0.1 because a minimum-versions run showed T-07's floors do not work together (pyarrow 14 cannot be imported with numpy 2; `pyarrow.concat_batches` needs 19; Delta reads need 19.0.1; Rust-vs-numpy bitwise equality from numpy 2.3). T-07 is fixed, so `pyproject.toml` keeps numpy>=2.0,<3 and pyarrow>=14.0.1 (f5dd2f62); BF-76's code and test fixes stay; `ci/constraints-min.txt` keeps numpy 2.3.0 and pyarrow 19.0.1 as the oldest versions the min-versions job runs. The owner decides whether T-07 changes. | Lead decision 2026-10-05; BF-76 lane status |
| 2026-10-05 | CI, W5-10 | The zero-network job installs DuckDB's delta extension (as root, the user the tests run as) before the network namespace is emptied, so the delta fallback and fingerprint-preservation tests load it offline (c4769255). | Lead, landing INT-18 |
| 2026-10-04 | INT-18, verification | **Lead (owner OK): sharded verification.** Each integration ref is verified at a fixed SHA by eight parallel sessions (S0 static and verifiers; P1 the Python-kernel bounded-memory test; P2/R1 streaming and demo; P3/R2 the rest of `tests`; P4/R3 plugin suites, R3 with the coverage step), each writing `docs/plans/evidence/<INT>/shard_<S>.md` on `verify/<INT>-<S>` and classifying every failure against build/main-plan; a landing needs every shard green or every failure classified pre-existing with evidence. | Lead decision 2026-10-04 |
| 2026-10-04 | INT-16 | **Integration INT-16 merged into build/main-plan:** CI-FIX (Windows and 3.12 CI fixes), BF-77 (#77), W1-01 (#55, `shape.compat`, `shape migrate`, time-capsule tests), W1-04 (#59, `shape.yml`, `shape init`), W1-05 (#60, plugin API v1 promise), W1-06 (#64, generation spec JSON Schema and edit API), W2-01 (#61, mergeable profiles), W2-02 (#52, fabric-mirror sink), W3-04 (#70, `shape explain`, notebook display), W3-06 (#71, scorecards), W3-09 (#72, duplicate detection and entity resolution), W3-10 (#73, reconciliation and time-series checks), W4-03 (#74, locale packs), W5-01 (#62, `shape.masking`), W1-16 (#68, `shape doctor` checks), PLUG-INT (`shape-dbt`, `shape-behavior`, `shape-healthcare-codes`, `shape-healthcare-standards`; extras `[dbt]`, `[healthcare]`, `[all]`), BF-78 (#78 part), W1-09 (round 2), P6-01a-e with the seed study P6-01e-seed, and NIGHTLY-FIX. Lead fixes in INT-16, each with a test that fails first: `emit --to file://` keeps the built-in file emitter when a plugin emitter also lists `file`; the fabric commands parity harness accepts exactly W1-01's `shape_version` and `min_shape_version` manifest keys with exact-value checks and three new negative controls; the bridge `list` describes each domain by its own description (bridge harness BR-8, a named baseline defect checked both ways); the scorecard reads column owners from W1-04's project layout; an existing table in create mode is a WriteError in the SQL writers; `verify_types` skips the datasets on which the baseline raised; the demo parity probe expects the healthcare scenario to run the healthcare domain; **safe-profile parity checks ISS-profile's PII rates on both sides:** the safe profile keeps `pattern_rates` and `pattern_contains_rates` (#2, integrated in INT-13; aggregate shares that drive the PII gate, never values, so a sparse SSN leak stays visible), and `safe_profile_1to1/verify.py` names them, requires them absent from every baseline column and equal to the raw profile's rates in every product column, and compares every other field as before (it failed every mapper case on build/main-plan 5c91ea50 too, and its early return on the key mismatch hid the other fields; negative control: a tampered rate is caught). Verified by the lead before merging (91 steps, all exit 0 except `verify_types`): ruff, format (1344 files), mypy (476 files), compileall, vulture, import contracts, requirement, secret, user-facing (tree and wheel), shipped-data, plugin-skeleton and conformance checks, bandit, cargo; `make check` on the landed commit, exit 0 (8477 passed, coverage 92.74%; heavy 42; Python kernel 265; one earlier run of it failed only the realtime `test_bursts`, a second off by 16%, the residual host-stall class of the 2026-10-02 P5-01b row; it passed in the five other runs that include it and three times alone); full suite in both kernels (8791 passed each, 16 deselected); the eleven plugin suites (behavior 86, databases 155, dbt 115, domains 7, eventhubs 42, fabric 465, healthcare-codes 100, healthcare-standards 138, kafka 46, simulation 156, sqlserver 150); demo 368; plugins 197 (installed, after uninstall, reinstalled) and every plugin kit; strategy baselines, plan fixtures, row counts, coverage map, profile verifiers in both kernels and CLI, DDL, db parity, retail export and T-21 small, tiers, simulation files and patterns with negative control, incremental, pack, stream profile and stream parity, chaos parity (20,083 runs per tool), bridge parity with negative control (64/64), demo, learn, mask, safe profile, transform, writers, `shape verify` parity (20/20 scenarios), scale, fabric commands parity with negative control; start-up 53-81 ms. **Pre-existing, not changed:** `profile_1to1/verify_types.py` reports 3 dtype-inference differences (`x_csv_mixed_chunks.bool_then_ints`, `x_csv_numbers.big_int`, `x_pq_types.bin_digits`), the same 3 on build/main-plan 5c91ea50; #76 (three tests fail with pyarrow < 25, BF-76; the lead's venv has pyarrow 25.0.1, where they pass). Environment notes: `verify_1to1` needs the reference-port retail run (regenerated with `domain_1to1/generate.py`); the shape-dbt `dbt` tests ran against a local package mirror (`SHAPE_DBT_PACKAGES_FILE`) because the hub's tarball host is blocked here. **Nightly run 37202803596** (int/INT-16 at 4a5e2214): 16 of 18 jobs passed, kafka-e2e, sqlserver-e2e, eventhubs-e2e, azurite-e2e, emit-rate-soak and the live jobs among them; fabric-demo-windows (Spark on Windows, #763, W1-09 stays wip) and fabric-emit-e2e (Eventhouse writer against the Kusto emulator, #764) failed and are filed. Tracker: W1-01, W1-04, W1-05, W1-06, W1-16, W2-01, W2-02, W3-04, W3-06, W3-09, W3-10, W4-03, W5-01 done; P6-01a-e and W1-09 stay wip; P8-01 and W4-02 recorded as rejected (2026-10-03 rows); P8-04 no longer depends on P8-01; owner actions O-10 to O-13 and roadmap rows W8-01 to W8-05 added. Issues #67, #76 and #78 stay open. | Lead verification logs; Nightly run 37202803596 |
| 2026-10-04 | W2-07 | Lead accepts the lane's 11 recorded decisions (docs/PROFILING_NOTES.md; unsampled profiles unchanged apart from the new sampling/type_inference keys, pinned by tests/profile/test_w2_07_compat.py; type proposals opt-in; decision file stays v1). Python-kernel heavy suite and tracked bench.py run at INT-18 integration. | Lead |
| 2026-10-03 | W1-10, issue #88 | **Lead: the CLI stable set is the 24 commands the lane built** (`capture`, `cat`, `check`, `compatibility`, `describe`, `diff`, `doctor`, `drift`, `generate`, `git-setup`, `inspect`, `keygen`, `list`, `pack`, `plan`, `plugins`, `presets`, `profile`, `registry`, `show`, `sign`, `validate`, `verify`, `version`); every other core command (`fidelity`, `chaos`, `emit`, `bridge`, `demo`, `report-card` among them) stays experimental and nothing else is promoted. `tests/cli/cli_surface_v1.json` changes only through `scripts/cli_surface.py --write` after merging build/main-plan, and `--check` exits 0. Confirmed as built: deprecation window of at least one minor release; `--json` keys promised, their snapshot is W1-14's; no deprecation-warning helper until something is deprecated. The `ci.yml` lines (`cli_surface.py --check`, `check_v1_done.py`) are added by the lead at integration. | Lead decision under the owner delegation: issue #88 asks for the levels but names no set; the set covers the whole core workflow of `docs/V1_DONE.md`, and a narrower promise is safe because promotion is additive and demotion is breaking. Evidence: `docs/plans/lane_status/W1-10.md`. |
| 2026-10-03 | W3-05, issue #102 | **Lead: the report card's interpretations are confirmed.** `report_card` keeps a keyword-only `require=()` (the issue's signature unchanged when omitted); the membership test uses `memorization.max_rows` and the gate's standardised distance, and the only new configuration keys are `privacy.max_membership_auc` (0.6) and `privacy.seed` (0), `shape-verify-config` staying version 1; `--require S` fails when section S is `not_run` or any test in it that applies to the request was not run (membership without `--holdout` included; a tier not asked for does not count). Also confirmed: membership `not_run` with a reason below 10 rows a side, with no shared numeric column or no holdout table; `--tiers` 1 and 2 only; no `--input-format`. The six suite failures in the lane status are environment-caused and must pass in the §1 environment (pyarrow 25.0.1, shape-fabric installed), not be skipped. | Lead decision under the owner delegation: issue #102 item 5 ("CI cannot pass a card that skipped a section silently") and §6.4 (no privacy claim without enforcement). Evidence: `docs/plans/lane_status/W3-05.md`. |
| 2026-10-03 | W5-09, issue #85 | **Lead: the known-answer and drift report design decisions are confirmed.** `--registry NAME` requires `--registry-root DIR` (no default root); registry mode and file inputs read raw profiles only, a safe profile exits 2 (never a silent "no drift"); a measure is sliced only by its own table or one reached many-to-one, other pairs and two-path slices are listed under `skipped` in `answers.json` with the reason, counted on stderr and explained in `docs/plugins/fabric-commands.md`; run dates, `--since` and the `threshold` column as built; plant bounds from the generator's declared bounds (0 as the lower bound when none is declared, documented); float sums at the answer's places, counts, min, max and integer or decimal sums exact; the exporter's default output unchanged. The lane runs the fabric commands parity harness where the pinned baseline can be built (§1.2). | Lead decision under the owner delegation: issue #85 is silent on these; a two-path DAX slice has no single correct answer, and a drift report from a safe profile would be a false claim (§2.3 2026-10-01 trust decisions, §6.4). Evidence: `docs/plans/lane_status/W5-09.md`. |
| 2026-10-03 | W7-01, issue #139, nightly | **Lead: the `fabric-git-sync-live` nightly job (W7-01 item 2) is added to `.github/workflows/nightly.yml` by the lead at integration**, exactly as in the lane status, after `abfss-live` and before `sqlserver-e2e`, gated on its own secrets (`HAVE_GIT_SYNC`, never a skipif) and uploading the `shape-live-check` v1 result. Item 1 stays open, and #139 with it, until a passing run is recorded from that job or by hand; the O-02 secrets plus `FABRIC_GIT_REMOTE` and `FABRIC_GIT_TOKEN` are an owner action. Accepted: item 1 works on the workspace's connected branch (it creates no branch, removes its commits with a new commit, deletes its items); `GA` inferred from the absence of a Learn preview notice, noted on the page. Not changed in W7-01 (issue out of scope), follow-up issues by the lead: Runtime 1.3 (retired 2026-09-30) named under `integrations/fabric/**`, and the run-notebook call's query-parameter endpoint form. | Lead decision under the owner delegation: lanes do not edit workflows (§2.3 2026-10-01 parallel lanes); T-26 (c) live tests only with owner secrets; issue #139 acceptance and §6.4 (no fabricated run). Evidence: `docs/plans/lane_status/W7-01.md`. |
| 2026-10-03 | W6-01 | **Lead decision on the lane's four questions:** (1) the self-test workflow stays as `docs/plans/lane_status/W6-01.action-selftest.yml`; the lead moves it to `.github/workflows/action-selftest.yml` at integration and verifies #234 item 4's pull-request run there; (2) SHA pins verified by anonymous `git ls-remote`: `actions/checkout` v4.2.2 = `11bd71901bbe5b1630ceea73d27597364c9af683`, `actions/setup-python` v5.6.0 = `a26af69be951a213d495a4c3e4e4022e16d87065` (lightweight tags); (3) `scripts/check_shipped_data.py` `FETCH_MODULES` gains exactly `shape/cli/prbot.py` and `shape/cli/notify.py` (stdlib `http.client` only; zero-network suite must pass; a test proves no socket without configured notifications); (4) the `nightly` marker stays as #234 specifies; the lane writes exact diffs for `ci.yml` (`and not nightly`) and `nightly.yml` (`-m nightly`) into lane_status for the lead. The lane's 13 silent-spec decisions are accepted; full suite re-run after the last commit on the §1.1 pyarrow. | Lanes never edit `.github/workflows`; #234 items 2, 4, 6, 7; pins match upstream tags. |
| 2026-10-03 | AUD-io | **Lead decision:** fix #506 (a one-file source is named after the file minus its format and compression suffixes; `my.data.csv` -> `my.data`; single-dot names unchanged; CHANGELOG notes the rename); fix finding 23 (`reconnecting_batches` gains keyword-only `backoff`, default 0.5 s doubling, capped at 30 s, `backoff=0` = old behaviour, injectable `sleep`); finding 24 left as-is (exactly-once dedupe set; docstring states memory grows with distinct ids); fix finding 25 (the existing per-member zip-bomb rule also applied to the archive total, no new constants); equivalence verifiers run after setting up the pinned baseline per §1.2 (`profile_1to1/verify.py --impl shape` must exit 0), else recorded as not run with the error. | Defects against reasonable expectation are fixed with tests; bounding the dedupe would silently re-admit duplicates; equivalence before timing (§6.4). |
| 2026-10-03 | AUD-chaos | **Lead decision on finding 11 (#408):** `duplicates` with a column is an error (`Corruption`/`Corruption.parse` raise a ValueError naming the corruption: it applies to whole rows). `tests/diff/test_drift_sweep.py:222` drops the ignored column; the sweep's planted output is shown identical and its false-positive bound is unchanged in both kernel modes; other uses fixed the same way; `docs/CHAOS.md` updated; chaos 1:1 verifier re-run. The CLI seed note is left as-is. | #408's expected behaviour; an explicit error is better than a silently ignored argument; no gate or tolerance changed. |
| 2026-10-03 | W7-03 | **Lead decision:** mixture sample cap stays 4,000 (within #226's "at most 100,000"), drawn by W3-07's deterministic sampling rule and documented; cost checked after `profile_1to1/verify.py --impl shape` exits 0 against §6.2(4) (no tracked benchmark >10% slower) and the 10x gate (D-04); a miss follows §6.5 and is escalated with numbers, never a gate change. The KS and strength-move noise guards on `mixture_change`/`seasonality_change` are accepted (W1-08 false-positive bound, W3-07 precedent), with tests that #226 item 4's planted true changes are still reported. Result shape accepted: `strength`/`warnings` only when a contract declares `strength` (byte-identical otherwise, #226 item 6). Decisions 1, 3, 6, 7, 8 accepted; the full `make check` and the demo tests are run. | #226 bounds; speed gates fixed (D-04, §6.2(4)); byte-identical requirement. |
| 2026-10-03 | #166 (AUD-stream) | **Lead decision: fix (defect).** CloudEvents 1.0 requires `time` in RFC 3339 with an offset. The envelope's `time` is written as RFC 3339: a zone-less timestamp gets `Z` (Shape's zone-less event times are UTC), a date becomes midnight `T00:00:00Z`, a zoned timestamp keeps its offset. `data` (and `_shape_event_time` in it, and the flat envelope) are byte-unchanged. `tests/streaming/emit/test_formats.py:93` changes its expectation to `2024-01-02T03:04:05.678901Z` with a comment citing #166; no other assertion weakened; new tests for date and zoned columns. `simulation_1to1/verify.py` (CloudEvents `time` is form-checked there) is run and must exit 0. Lane BUGS-stream-2. | Spec conformance; strict CloudEvents SDKs reject the current value. |
| 2026-10-03 | #522 (AUD-scenario) | **Lead decision: fix (defect).** `FidelityReport.overall_score()` returns `0.0` when no column was compared (a 100% claim needs at least one compared column); `render()` prints `Fidelity score: n/a (no columns compared)` in that case; the inference mode reports the same. `tests/demo_cmd/test_inference_streaming.py:145` changes `== 1.0` to `== 0.0`, documented; the other assertions are unchanged. `demo_1to1/verify.py` is run and must exit 0; if it fails on this case, stop and escalate (§0.4), never edit the verifier. Lane BUGS-demo-2. | §6.4 "no claim without enforcement": nothing compared is not full fidelity. |
| 2026-10-03 | #541 (AUD-bridge) | **Lead decision: fix.** P6-12 is integrated, so `demo_list`, `demo_run`, `demo_status`, `demo_cleanup` call the matching `shape.demo.api` functions, as every other bridge command calls its CLI's function. The two tests that pinned the interim refusal (`test_a_valid_demo_request_waits_for_shape_demo`, `test_the_demo_commands_are_marked_pending_...`) are retired and replaced by tests of the wired behaviour (and that no command is still marked pending); the schema index drops `"pending": "P6-12"`; `docs/BRIDGE.md` updated. The published request/response schemas are not changed. Lane BUGS-demo-2. | The refusal was an interim state tied to P6-12, which is done. |
| 2026-10-03 | #352 (AUD-docs) | **Lead decision: fix (defect).** For a `--universal` lock, `check_lock` requires a declared dependency unless its marker is false for every supported platform/Python (evaluate with `sys_platform` in `linux`, `darwin`, `win32` and each T-06 Python), not only on the host. `tests/release/test_offline_lock.py::test_check_directory_flags_dropped_dependency[tzdata]` changes to expect the finding, documented; the other parametrisations unchanged. Lane BUGS-plugins-2. | docs/INSTALL.md promises a universal lock covers all platforms; T-07's Windows tzdata must be in it. |
| 2026-10-03 | #425 (AUD-fabric) | **Lead decision: real defect in code, fix blocked; §0.4 escalation, nothing changed.** Duplicate measure names make the retail `.bim` undeployable. Any fix renames retail measures, and `fabric_commands_1to1/verify.py` compares `export-model retail` field by field with the baseline, which has the same duplicate names; making it pass would need a new allowed difference in the verifier (`differences.py` and a probe), i.e. editing an equivalence verifier to let code pass, which §6.4 forbids. Escalated to the owner: either the verifier gains an owner-approved allowed difference (table-qualified name on collision only) or the defect stays. Issue stays open. | §6.4; verifiers are never edited to make code pass. |
| 2026-10-03 | #429 (AUD-fabric) | **Lead decision: real defect; the behaviour fix is escalated (§0.4); the false claim is fixed now.** Making old rows survive a failed truncate/replace changes the commit sequence that the recorded contract tape `plugins/shape-fabric/tests/fixtures/sql_replace_with_key.json` pins; tapes are not changed to make code pass, so that part is recorded for the owner (re-record the tapes from the corrected scenario, or keep the behaviour). Now: the `sqldb.py` / warehouse docstrings and `docs/plugins/fabric-writers.md` say plainly that `truncate` and `replace` commit before the insert and that a failed write leaves the table empty (truncate) or absent (replace); only `create`/`append` roll back. A test asserts that the documented statement matches the behaviour of the fake service (no tape changed). Lane BUGS-plugins-2. Issue stays open for the escalated part. | The issue's own alternative expectation; §6.4 no claim without enforcement; fixtures unchanged. |
| 2026-10-03 | #547 (AUD-kernel) | **Lead decision: fix, without a new crate and without changing native output.** The native kernel's behaviour (Rust `char::is_alphanumeric` word rule, Rust std's Unicode 16 case tables) is the contract. A generator script (in `scripts/`, run with the pinned toolchain) emits a compact table of every non-ASCII code point whose word class or case mapping the twin needs; it ships as package data and the twin (`src/shape/kernel/reference/gen.py`) uses it instead of `str.isalnum`/`str.upper`/`str.lower`/`str.title` outside ASCII, so the twin no longer depends on the Python's Unicode version. ASCII paths unchanged. A test sweeps all code points in all three modes, native vs twin, on every T-06 Python; a test fails if the table is stale against the native kernel. T-21 verifiers run in both kernel modes before any timing and must exit 0. No D-xx/T-xx change (T-02's crate set unchanged). Lane BUGS-kernel-2. | GENERATION_KERNEL.md promises bit-equal strings; native output (and the bytes the verifiers compare) stays the same. |
| 2026-10-03 | #294 (AUD-security2) | **Lead decision (technical, not owner-reserved): fix, secure by default.** For a non-loopback host, the PostgreSQL sink defaults to `sslmode=verify-full` and the MySQL sink to verified TLS (`ssl={"check_hostname": True}` with the system CA store, or `ssl_ca` when given). Loopback hosts (`localhost`, `127.0.0.0/8`, `::1`) and Unix sockets keep today's behaviour, so local and emulator use is unchanged. Opt-out only explicitly in the URI (`sslmode=disable`/`prefer` for PostgreSQL; `ssl=false` or `ssl-mode=DISABLED` for MySQL). A TLS failure raises a ShapeError naming the host and the opt-out. Docs and CHANGELOG note the default. Lane BUGS-plugins-2. | Matches the SQL Server sink (`Encrypt=yes`); no cost or legal impact. |
| 2026-10-03 | #506 (AUD-io) | **Confirmed, no new action:** decided in AUD-io round 3 (single-file sources named after the file minus format and compression suffixes; CHANGELOG notes renamed tables); carried on lane/AUD-io. | Already decided. |
| 2026-10-03 | #334 (AUD-tests) | **Lead applies at integration:** the `database-plugins` job also runs `tests/cli/test_generate_to.py` and `tests/streaming/emit/test_table_sink.py` (diff in `docs/plans/lane_status/AUD-tests.md` on lane/AUD-tests); noted in int18_candidates. | Lanes never edit `.github/workflows`. |
| 2026-10-03 | #54 | **Out of scope for the open engine; closed as not planned.** | Scope of this repository (the open engine). |
| 2026-10-03 | P8-01, P8-04, D-13, §10 | **P8-01 rejected** (superseded by D-13): its deliverables are §10 aliases (retired), reading baseline profile JSON and `.refengine.json` (D-13: Shape reads only its own formats) and a user-facing migration page naming the baseline. A generic "import profiles from other tools" path is not kept: it would be the same baseline reader under another name, against D-13. P8-04's `Depends` loses P8-01. Rows 82 and P8-04 updated by the lead at the next landing. | D-13 (owner, 2026-09-30). |
| 2026-10-03 | P8-02, P8-03, P8-04, P8-05 | **Kept, unchanged in scope, waiting on their gates** (P8-02/03: G6; P8-04: P8-03, G7, GF, PF-06; P8-05: P8-04). Clarifications under D-13 and owner reservations: P8-02's published performance page and the P8-03 site name no competitor (results labelled against "the baseline" only, or the comparison kept in `docs/plans/`/benchmarks); P8-04 builds the T-25 workflows, SBOM and attestations, but the 1.0.0 version change and any TestPyPI/PyPI publish are owner actions (O-01) and are not done by a lane; P8-05 unchanged. No lane launched (gates G6/G7/GF not done). | §6.1 order; D-13; owner-reserved versions and PyPI. |
| 2026-10-03 | #425 (AUD-fabric), §6.4 | **Owner approval (2026-10-03, ~21:30 UTC: "You are approved to complete all tasks to take this to 100%") of the §0.4 escalation.** `fabric_commands_1to1/verify.py` gains exactly one documented allowed difference: a semantic-model measure name is qualified with its table name only when two measures would otherwise share a name. Any other difference still fails (negative control: a non-colliding measure rename is caught). Code table-qualifies colliding measure names; regression test fails before, passes after; verifier exits 0. Lane OWN-425. | Owner-approved verifier difference (§0.4, §6.4); retail `.bim` is otherwise undeployable. |
| 2026-10-03 | #429 (AUD-fabric), §6.4 | **Owner approval (2026-10-03, ~21:30 UTC, same approval) of the §0.4 escalation.** Truncate/replace + insert become transactional in every affected SQL writer (a failed insert leaves the old rows); the affected SQL scenario tapes (incl. `plugins/shape-fabric/tests/fixtures/sql_replace_with_key.json`) are re-recorded from the corrected scenario with the repo's recording tooling, never hand-edited; every other tape byte-identical; the docs added for #429 updated to the new behaviour. Lane OWN-429. | Owner-approved tape re-record (§0.4, §6.4); data-loss defect. |
| 2026-10-03 | W3-13 (#106) | **Lead decisions.** (1) `--scaled` compares each table's share of total rows by absolute difference against `--row-tolerance`: accepted (it is the issue's wording). (2) Absolute mode `abs(B - A) / A`, and an empty A passes only an empty B: accepted. (3) Distribution check = the drift engine's column comparison with the documented exclusions (fitted-family name flips; unseen categories under the `category_tvd` share; `new_categorical_values` on id-like columns; with `--scaled` also distinct counts and extremes): accepted only if every excluded or scale-dependent metric is reported `not measured` with its reason, never as a pass and never left out silently, and `docs/PARITY.md` lists each one. (4) `--baseline` does not change the exit code. It only adds `new`, `broken_by_change` and `already_failing` markers: accepted (the issue says "also lists"). (5) `contract_not_applicable` fails that consumer (exit 1), not exit 2: accepted. (6) The W1-04 schema thresholds and the W1-03/W1-04 `--source` collision are already resolved in int/INT-16 and INT-18 (b2bda02c, 207f452b, b453dcfc, 49131528). The lane merges `origin/int/INT-18` (a merge commit) and does not add `shape.yml` keys. (7) Install `plugins/shape-fabric` for the demo_cmd tests. Any remaining failure must fail identically on int/INT-18 in the same venv, with evidence. | The issue text decides (1), (4) and (5). Explicit "not measured" beats a silent pass. The integration conflicts are already fixed upstream. |
| 2026-10-03 | AUD-pluginfw | **Lead decisions.** (1) Tuple-to-list on `save_domain`/`load_domain`: accepted as JSON's representation, with no conversion on load. `docs/plugins` or the packs docs say that sequence constraints load as lists. A test pins `domain_to_dict(load_domain(p)) == domain_to_dict(d)`. (2) The domain JSON is a persisted format, so it must declare `format` and an integer `version` (plan §13 and `docs/specs/STATE_AND_COMPATIBILITY.md`). `save_domain` writes `{"format": "shape-domain", "version": 1, "domain_version": <d.version>, ...}`. `load_domain` reads v1 and the legacy form (no `format` key, string `version` = domain version), following the read-old promise. Another `format`, or a `version` that is not 1, raises `ValueError` naming the file. A JSON Schema goes in `src/shape/schemas/`, plus a compatibility test with frozen legacy and v1 files and a CHANGELOG line. (3) `SHAPE_API` must be `"MAJOR.MINOR"` (plan §4 "1.x", `docs/plugins/authoring.md`). `check_api("1")` raises `PluginLoadError` saying `declare SHAPE_API as "MAJOR.MINOR", for example "1.0"`. No plugin in the repo declares `"1"`. Failing test first. | Every persisted format declares format and version. Prefer an explicit error over silently accepting an undocumented form. Nothing outside the audit area. |
| 2026-10-03 | W7-01 (#139) | **Lead decisions.** (1) Nightly job: the lead pastes `fabric-git-sync-live` at integration (saved copy of the status-file diff). The lane never edits `.github/workflows`. If the job's env changes, the lane updates the exact diff in `docs/plans/lane_status/W7-01.md`. (2) The live test never uses the workspace's working branch. It creates a throwaway branch `shape-livecheck-<UTC yyyymmddThhmmss>-<8 hex>` on `FABRIC_GIT_REMOTE` (using `FABRIC_GIT_TOKEN`), connects the dedicated test workspace to it, runs both directions, then disconnects and deletes the branch and the items it created in a `finally`. A cleanup failure fails the test and names what is left behind. If the workspace is connected at start to any branch not prefixed `shape-livecheck-`, the test fails by name without touching it. Any new secret (for example a Fabric Git connection id) is named in the status file and job diff for the owner (secrets are the owner's). Each new Fabric call gets its inventory row first, as the guard requires. (3) The "GA inferred" note, Runtime 1.3 text outside the guard's scope, and the run-notebook query form: accepted as recorded, with no change. (4) #139 stays open for the live run. | The lead's ruling on the branch. A shared working branch is never written by a test. |
| 2026-10-03 | AUD-stream (#153, #166) | **Lead decisions.** (1) #166 is fixed by lane BUGS-stream-2 (RFC 3339, `Z` for zone-less, as in the row above). AUD-stream does not change `formats.py` CloudEvents `time` or `tests/streaming/emit/test_formats.py:93`. (2) #153 fix accepted: windows written at Ctrl-C carry `"partial": true` and are replaced by the complete window on restart, so a resumed run's windows equal an uninterrupted run's (STREAMING_SEMANTICS §6). An uninterrupted run's file is byte-unchanged (pinned by a test), and the optional key is documented. (3) The lane sets up `$REFENGINE_ROOT` at the pinned commit per §1 and runs `benchmarks/vs_refengine/stream_1to1/verify.py` itself; it must exit 0 (§6.4). (4) The deselected `test_bounded_mode_memory_does_not_grow_with_rows` (python kernel) is run alone on the lane and on int/INT-18 with a sufficient background timeout. If it hangs on both, record that with evidence and file or cite an issue. It is never deselected silently. (5) Merge `origin/int/INT-18`. | One owner per fix (no double change to #166). The resume semantics are the spec. Verifiers before acceptance. |
| 2026-10-03 | W3-12 (#105) | **Lead decisions.** (1) `iso-4217` and `iso-639` are not shipped. The issue's acceptance says that an unconfirmed licence means not shipped and escalated, and licence confirmation is counsel, which stays the owner's: owner item "confirm SIX ISO 4217 / LoC ISO 639-2 redistribution terms". The build script stays, for a locally built pack. The `iso4217` validator with no pack installed fails with an explicit error naming the pack and the build command (exit 2), never a silent pass. `docs/REFERENCE_PACKS.md` and the status file record this. (2) IBAN: every same-kind substitution and every adjacent transposition is rejected, except transpositions whose MOD 97-10 digit expansion is unchanged (a letter next to a digit, for example `1B` and `B1`). The test asserts the survivors are exactly that set. Documented as the issue's own letter/digit exclusion applied to transpositions. No tolerance involved. (3) `iban-lengths` as a separate pack shipped in core, with its source and licence: accepted (it is "a table in the core pack" with provenance). (4) The `us_zip` validator accepts integers 1–99999 (a ZIP read as a number) as the zero-padded 5-digit form, checks text exactly (5 ASCII digits), and treats 0, negatives, non-integers and anything else as invalid. Documented. (5) Re-run the full suite in both kernel modes in a clean venv (no extra `--no-deps` plugins), after merging `origin/int/INT-18`. | Licence questions are counsel (owner-reserved). The acceptance already names the not-shipped path. Mathematical impossibility recorded, not hidden. |
| 2026-10-03 | P6-01a | **Superseded; session archived.** lane/P6-01a code through round 2 (26decde6) is in int/INT-18 via lane/P6-01-int. Only 72320f94 (evidence logs) is outside. The owner accepted the seed-1042 T-21 misses as documented chance (2026-10-02, §2.3), and GEN-IN round 3 runs in lane/P6-01-perf-domains and lane/P6-01-perf-engine. The gate is unchanged. No new session. | Already decided upstream. |
| 2026-10-03 | Legacy need_input (23 sessions) | **Archived.** Each branch head (lane/W1-01, W3-09, W3-06, W3-04, W1-05, P6-01c, P6-01b, P4-04b, G1-rerun, P2-05, P2-03, PF-03, P6-09, build/main-plan, talk/shape-v1-tech-talk, demo/l3-content) verified with `git merge-base --is-ancestor <head> origin/int/INT-18`. Their questions are obsolete. | Integrated. |
| 2026-10-03 | INT-15 | **Integration INT-15 merged into build/main-plan:** ISS2-sinks round 2, W1-02, W1-03, W2-03, W5-02, W6-02, W1-08, P6-11 (`shape bridge`) and P6-12 (`shape demo`). Lead fixes in INT-15: the `delta_scan` SQL `# nosec B608` with its escaping (see the row below), the publish test key set, and the fabric commands parity harness: W1-03 adds four declared keys to the run manifest (`format`, `version`, `reproducibility`, `dataset_id`); the harness now accepts exactly those four as Shape-only keys, requires every other key to match the baseline as before, checks the format declaration, and gained four negative controls (a Shape key removed, the declaration changed, a non-integer version, an unknown key added); with them its negative control passes. Verified by the lead before merging (54 steps, all exit 0): ruff, format, mypy, full suite in both kernels, heavy suites, demo, profile verifiers in both kernels, DDL, plugins (install, tests, kit, uninstall), db parity, domains, retail export and verify, tiers, simulation files and patterns with negative control, incremental, pack, stream profile and stream parity, fabric plugins, fabric commands parity with negative control, chaos parity. **Decisions:** every ported domain ships, listed by maturity (no top-ten cut; register R8-03); the lead tags `demo-2026-10-06` at the freeze once the owner sets the version (owner action). | Lead verification logs |
| 2026-10-03 | §13 lanes launched; W4-02 rejected; W1-07 done; early-start dependencies | **Owner: speed is all that matters.** Every remaining §13 package without an open chain dependency now has a lane, specified by issues #79 to #106 (W1-10, W1-11, W1-12, W1-14, W1-15, W1-17, W1-18, W2-06, W2-08, W2-09, W2-10, W3-01, W3-02, W3-03, W3-05, W3-07, W3-11, W3-12, W3-13, W4-01, W5-04 to W5-10, W6-04). Lanes start from int/INT-15 and merge the finished, not yet integrated lanes they depend on (W1-01, W1-04, W1-05, W1-06, W2-01, W3-06, PLUG-INT); a lane merges into build/main-plan only after its dependencies have. **W4-02 rejected:** the open side already imports Synthea modules (`shape behave import-gmf`); a runner needs a Java runtime (poor fit for air-gapped use) and served calibration, which is not open work. **W1-07 done:** `docs/BRANDING.md` already carries it on build/main-plan (9e87cd8, c6b5d17). **W2-06 is open:** the sempy source plugin profiles semantic models; report and visual usage lookup is out of its scope. **W1-11:** safe capture becomes the default as §13 says (superseding SAC-01), redacting only what is written or printed, so the verifiers and T-22 parity are unchanged. **W1-17** is built now and merged after the 2026-10-07 demo. Held until their dependencies land: W1-13 (W1-12), W2-07 (ISS2-bugs), W3-08 (W3-07), W5-03 (W1-11), W6-01 (W1-14), W6-03 (W5-05); W2-05 waits on O-07. | Lead; issue specs written from the plan and the code |
| 2026-10-03 | #46 vs ISS2-joint tests; #76; #78; INT-15 | **ZIP-as-text wins over the integer reading in five ISS2-joint tests:** with #46 (identifier columns stay text, owner request) the `city_zip` fixture's `zip` column is text, so its placeholder is `'00000'` and `02134` and `2134` are different values; ISS2-bugs round 5 updates exactly those five expectations (recomputed, each citing #46) and nothing else. **#76 is a pyarrow-floor question, not numpy:** the three failures occur with pyarrow < 25 (pulled in by `tests/demo/fabric/requirements.txt`) and are raised by the tests' own pyarrow calls; the core floor `pyarrow>=14.0.1` stays unless Shape's own code fails at it; BF-76 makes the tests build their data in a version-independent way with every assertion unchanged, and adds a minimum-versions check (a floor rises only on evidence that Shape fails below it). **#78 filed** (nightly sqlserver-e2e: pyodbc `Row` compared with a tuple; fabric-emit-e2e) with lane BF-78; W1-09 round 2 fixes the Fabric demo tests on Windows from nightly run 37110403910 (kafka-e2e passed there). **INT-15 lead fixes:** `delta_scan` SQL marked `# nosec B608` with the escaping it relies on (bandit gate; W2-03's tests pin the SQL text, so parameter binding was not used); the P6-07c publish test's exact manifest key set now includes W1-03's `format`, `version`, `reproducibility`, `dataset_id`, as W1-03's own runner test already does. | Lane reports ISS2-bugs round 4, BF-76 round 1; nightly run 37110403910 |
| 2026-10-03 | INT-14, W1-05, issues #76 #77 | **Integration INT-14 merged into build/main-plan:** ISS2-joint round 2 (held decisions (1) and (6) built, G1 re-measured), P6-07c (`shape fabric publish\|notebook\|deploy-notebook\|setup\|export-model` with top-level aliases) and W2-04 (Excel named ranges, tables, merged cells, autofilter; Delta reader 1 / writer 2). Verified by the lead before merging: ruff, format, mypy, full suite in both kernels (5637 passed each), heavy suites, demo, profile verifier, plugins, domains, retail, tiers, fabric, simulation, incremental, pack, stream and chaos parity, and fabric commands parity with its negative control (all exit 0). The fabric commands parity harness needs the baseline installed in its venv (it reads the baseline's own package metadata for the SBOM comparison); the lead's local check installs a copy of the pinned checkout into a separate venv, the pinned checkout itself is untouched. **W1-05 deprecation window accepted:** nothing in plugin API v1 is removed in 1.x; a deprecated member keeps working until the next major version. **Issues #76 and #77 filed:** three tests fail with numpy 2.5.3 (allowed by `numpy>=2.0,<3`; fix lane BF-76, high), and an order-dependent credential-reference test (BF-77, low); the matching failures that lanes W3-04, W3-06 and W3-09 reported are these issues, not lane defects. build/main-plan CI is red on 3d65c49 for reasons outside INT-14 (bench-quick reference_port profile verifier, macOS kill-9 restart test, an intermittent abort at interpreter exit); CI-FIX round 5 owns them. | Lead integration; lane reports; local reproduction of #76 (numpy 2.4.6 passes) |
| 2026-10-03 | CI, realtime tests | **Realtime timing tests (marker `realtime`, including the 10-minute soak) run on Linux only, now also excluded on Windows** (the 2026-10-02 owner decision excluded macOS). No bound or assertion changed; every other test still runs on Windows; G5's 1-hour soak is a Linux nightly. Evidence (lane CI-FIX round 4, run 37100249946): on the hosted Windows runner the whole process stopped for about 4 s (a probe thread that only sleeps woke 1.66 s and 0.97 s late; no garbage collection over 0.1 s), intermittently (3.11 two of two runs, 3.14 one of two); the rate outside the stall and the overall rate were exact. | Lead decision under the owner delegation, same reasoning as the macOS decision. |
| 2026-10-03 | W2-04, Delta output | **Shape's Delta writer stores time-zone-less timestamps as UTC timestamps** (the wall-clock value is kept and reads back as UTC), so Shape's Delta tables stay at reader version 1 / writer version 2 with no table features: `deltalake` 1.6.6 otherwise adds the `timestampNtz` feature (reader 3 / writer 7), which not every Fabric engine reads. Documented as a behaviour change of Delta output; a regression test asserts the protocol. | Lead decision on the lane's finding; Fabric compatibility (readers 1 / writers 2 for Python notebooks, pipelines and Eventstreams). |
| 2026-10-03 | INT-13, P6-07b, issues | **Integration INT-13 merged into build/main-plan:** CI-FIX round 3 (profile JSON depth limit, POSIX OneLake paths, domains in the bench-quick venv, synthetic-secret tests under `tests/security/`), ISS-profile (#2, #21, #22, #23, #24, #37), ISS-gen (#11, #12, #17, #18, #19, with #9 and #10 temporal and spec-key fixes), EXCEL (#50 Excel source, #51 multi-sheet workbook sink), FIN-CAP (financial simulator full-span window; `shape capture` reads every profile input), P6-07b (Fabric `--auth` modes and credential references; P6-07b done) and NIGHTLY-FIX (nightly soak and emulator jobs). Conflicts resolved by the lead keeping both sides: ISS-cli's `errors.guarded` plus ISS-gen's one-line program crash message; P6-07b's sign-in options passed through the shared `open_sink`, and its redaction moved into `errors.fail` so every one-line error is redacted; FIN-CAP's `load_table` with Excel workbook and CSV options. Three lead fixes found by verification: `spec_keys` lists the digits providers' `width`; `pack_1to1` applies the deliberate reserved-email difference already listed in `domain_differences.py` (only when `vocab` is the sole failing check and every address is at a reserved domain); the simulation fixtures state their two-day range with an inclusive end date (#10). Lead verification, every step exit 0: static checks, cargo debug and release, strategy baselines, full suite both kernels (5496 each), heavy, demo, profile verify both kernels, ddl, plugins and their kits, SQL Server parity, domains and export, retail T-21, tiers, fabric plugins, simulation file and pattern parity with negative controls, incremental, pack, stream and stream-profile parity, heavy streaming, simulation plugin (156), chaos parity. | Lead. |
| 2026-10-03 | Owner delegation, §13 | **The owner delegated decisions to the lead** (2026-10-03): the lead decides with best practice and records each decision here; actions that spend money, publish packages to a public index or make a repository public, create legal obligations, contact outside parties, or delete repositories or rewrite published history stay with the owner. New §13 adds the roadmap work packages W1-01 to W5-02 (issues #52, #53, #55 to #65), with P6-11 specified further by #56. | Owner, 2026-10-03. |
| 2026-10-03 | Decisions held since 2026-10-02 | Lead decisions under the delegation: (1) joint profiling (#47) is off by default for multi-table profiles, `--joint` turns it on (single tables keep it on); (2) `shape-dbt`, `shape-behavior`, `shape-healthcare-codes` and `shape-healthcare-standards` join the first-party plugins (T-09, §5) with extras `[dbt]` and `[healthcare]` (T-08 amended); (3) #39 non-local destinations: `--yes`, or `SHAPE_CONFIRM_REMOTE=1` for notebooks and pipelines, built after the 2026-10-07 talk; (4) NCPDP output and the NUCC taxonomy are bring-your-own only, nothing licensed ships; (5) this repository's healthcare scope is the behavior framework, the code-set loaders and the X12, FHIR and OMOP emitters with an NCPDP bring-your-own layer; other healthcare domain content is not part of this repository; (6) new optional contract rules are additive keys in contract v1 and an older reader refuses an unknown rule; (7) the new plugins' slow suites run nightly. | Owner delegation, 2026-10-03; recommendations of 2026-10-02 8:32 PM EDT. |
| 2026-10-03 | INT-12, P6-04, issues | **Integration INT-12 merged into build/main-plan:** P6-04 (lanes P6-04a, P6-04b: simulation) and the issue lanes ISS-gaps (#13, #15, #16), ISS-sign (#38), ISS-stream (#33, #34, #36), ISS-verify (#7, #31, #32), ISS-cli (#6, #8, #27, #28, #29; #30 waits on a spec) and ISS-diff (#3, #4, #5, #14, #20, #34, #35). Hand-merged conflicts: `cli/main.py` (ISS-cli error handling kept with the warnings restore, `--version/--as-of`, `--fail-on-empty`), `registry/local.py` (raw-profile refusal plus atomic writes). Lead verification of the integrated tree, every step exit 0: static checks, debug and release `cargo test`, strategy baselines, full suite in both kernel modes (5212 each), heavy, demo, profile verify both kernels, ddl, plugins (install, tests, kit, uninstall), SQL Server plugin parity, domain tests and export, retail T-21 small, tiers, simulation file and pattern parity with negative controls, incremental, pack, stream and stream-profile parity, heavy streaming, simulation plugin kit, chaos parity (20,083 runs per tool, identical cell for cell). Each lane's status file names what it left undone; no gate, tolerance or D-xx/T-xx decision changed. | Lead. |
| 2026-10-02 | P6-11, T-08, D-08, PR #48 | **The MCP server moves to a separate, private commercial component; it is not cancelled.** The `plugins/shape-mcp` skeleton (never published) is removed from this repository (sqllocks/shape#48), P6-11 keeps `shape bridge` (the JSON protocol the commercial MCP server builds on) and drops the `shape-mcp` deliverable and its MCP client e2e test, T-08 drops the `[mcp]` extra, and the first-party plugin list (T-09, §5, `scripts/check_plugin_skeletons.py`) has six distributions. No public MCP server is re-added. The skeleton stays in git history and was MIT licensed while public. | Owner. |
| 2026-10-02 | T-07, issue #21 | **Owner: add `tzdata` as a Windows-only core dependency** so named time zones work out of the box on Windows (Linux and macOS use the OS database; unchanged). T-07 amended. | Owner, 2026-10-02: "Add tzdata on Windows". |
| 2026-10-02 | T-22, issue #24 | **Owner: decimal columns keep `dtype: float`** with `precision` and `scale` beside it (no new `decimal` dtype; T-22 vocabulary unchanged). | Owner, 2026-10-02. |
| 2026-10-02 | P6-01e, T-21 | **Owner: investigate the composites' seed-1042 misses before accepting them** (18 cells), as was done for the single domains. Lane `lane/P6-01e-seed`. | Owner, 2026-10-02: "Investigate first". |
| 2026-10-02 | CI | **Lead: full CI runs on `main`, `build/main-plan` and `lane/CI-FIX` pushes (plus PRs and manual runs), with superseded runs cancelled per branch.** Every lane push ran the full matrix; the queue reached 179 runs and held the G5 soak for hours. Lanes are verified locally by the lead before merging; build/main-plan keeps the full matrix after every merge. The stream-plugins job now also tests the SQL Server and Fabric plugins. | Lead. |
| 2026-10-02 | G5 | **Phase 5 done (P5-01..P5-04); G5 waits only on the 1-hour realtime soak** (nightly `emit-rate-soak`, run by hand on build/main-plan because the scheduled nightly runs on `main`). STREAM-EMIT 14.27x in-process (lead), live fidelity within 0.0072 of `shape fidelity`. The G5-gated packages P6-04, P6-07a, P6-13 and P7-04 started in parallel before the soak result, under the owner's standing "whatever can run in parallel, do it" (as PF-02 started before G1); G5 is marked done only when the soak passes. | Lead. |
| 2026-10-02 | SAC-01, issue #1 | **Owner's issue sqllocks/shape#1 (Shape as Code) built as work package SAC-01**: byte-reproducible `.shape` containers (fixed member timestamps, order and attributes; members **stored, not deflated**, because deflate output differs between zlib builds and only stored members give identical bytes across machines; git compresses them itself; old deflated artifacts still read), `shape cat` and `shape git-setup` (one changed line per changed property in `git diff`), `shape profile --name` and a kept name when overwriting an existing `.shape`, docs naming `shape profile safe` output as the committable artifact (`--json` and the raw `.shape` hold real values, lead decision kept), and a real-git e2e test. Artifact spec rules unchanged (writer convention 9 added). | Owner, issue #1; lead. |
| 2026-10-02 | P5-01b | **Realtime pacing under load:** a realtime run `gc.freeze()`s the objects that exist when it starts and writes checkpoints on a writer thread, removing two stall sources that broke `test_realtime_rate_within_five_percent` inside the full suite. Test and ±5% criterion unchanged. A residual whole-process host stall (no Python thread runnable) can still exceed 50 ms on a busy VM; if CI shows it, the options are the owner's (a quiet runner for the realtime tests, or a restated CI criterion). | Lead. |
| 2026-10-02 | P6-01, T-21 | **Owner accepts the seed-1042 T-21 misses as documented chance** (P6-01a–d: clause (h) cells in capital_markets, education, financial, insurance, supply_chain, real_estate, iot, manufacturing, marketing, and healthcare `provider.last_name` distinct ratio). Evidence (`lane/P6-01-seed`, `docs/plans/lane_status/P6-01-seed.md`): 30 seeds per tool for every cell (150 for the two closest), per-table and per-column Mann-Whitney and KS with Bonferroni: no table or column differs; the generation rules are the same; a fresh baseline seed misses its own clause-(h) floors on 8.5 of 130 tables on average, as Shape's seeds do (p = 0.93); seed 1042 is Shape's worst of 30. No seed, floor, tolerance, case or test changed; T-21 unchanged. GEN-IN ≥10x at medium remains open (round 3). | Owner, 2026-10-02: "we can accept". |
| 2026-10-02 | PF-06b, §12.3 | **Owner: a contract may check a subset of a dataset's tables** (option (a)). PF-06b keeps its fixes (a `tables` contract against a single-table profile raises `ContractError`; a named table the profile lacks is a `table_exists` violation; `shape profile FOLDER --dataset`; artifact folders unique to the microsecond) and drops its new default that flagged every unnamed profile table as `extra_table`, which broke existing partial contracts (§12.3: every rule is optional, format final for 1.0). | Owner, 2026-10-02: "A". |
| 2026-10-02 | P6-01, T-21 | **Owner: investigate the seed-1042 T-21 misses before accepting any** (P6-01a–d: clause (h) cells and healthcare `provider.last_name`). Lane `lane/P6-01-seed`: many-seed distributions for both tools, per-column decomposition, generation-rule comparison; fix real differences. No seed, floor, tolerance or case changes. | Owner, 2026-10-02: "Investigate further first". |
| 2026-10-02 | P6-01a, allocator | **Owner: keep the Arrow memory-pool / THP setting on `import shape`** (documented; `SHAPE_MEMORY_POOL=default` opts out). | Owner, 2026-10-02. |
| 2026-10-02 | P6-01, T-21, GEN-IN | **Lead notes on the P6-01 domain lanes.** (1) P6-01c's harness change to `domain_1to1/verify.py` clause (e) is accepted: the vocabulary of a `faker` provider is its full pool (`company`, `sentence`; `uri` by component), and of an `enum`/`weighted_enum` its declared value set, instead of the values the baseline drew at seed 42; no tolerance changed and values outside the vocabulary still fail. (2) P6-01a round 2 sets the Arrow memory pool (or turns off THP) on `import shape`, documented with an opt-out (`SHAPE_MEMORY_POOL=default`); the owner was told and may ask to limit it to the command line. (3) GEN-IN at medium after round 2 is 2.65x-8.1x across the 13 domains; round 3 runs as two lanes (per-domain hot paths; engine-wide writer, scheduler and native core). Gate unchanged. | Lead; investigated; owner accepted the seed-1042 misses as chance (2026-10-02). |
| 2026-10-02 | P6-02, P6-10 | **Lead acceptance notes.** P6-02: `shape.plugins.kit.check_chaos` assumes a mutation keeps the batch schema; five of the six categories change it by design (schema, value `wrong_types`, volume, file). P6-02's acceptance does not use the kit check, so the kit is left as is (a follow-up, no contract change). P6-10: `tests/privacy/test_safe_validator.py::test_cli_input_errors_exit_2` changed one assertion to the new contract (`shape profile validate FILE` without `--safe` is the structural check: 0 valid, 1 missing), as P6-10's deliverable requires; nothing skipped. | Lead. |
| 2026-10-02 | P6-01a, GEN-IN | **P6-01a GEN-IN at medium missed after round 1** (capital_markets 5.20x, education 7.82x, financial 7.15x; gate 10x). Following the owner's standing "keep optimising" direction (P4-10, G1): round 2 on `lane/P6-01a` with the lane's option (b) (process-wide Arrow allocator choice, native pandas-free generation path) and, if needed, (c) (parallel Parquet encoding); retail must not regress. Gate unchanged. P6-01b/c/d start from `lane/P6-01a` in parallel. T-21 clause (h) misses at seed 1042 in 6 of 60 cells are escalated to the owner (no seed, floor or case changed). | Lead; owner ruling on clause (h) pending. |
| 2026-10-02 | PF-06 | **Trust fixes from PF-06's findings** (owner's standing decision, 2026-10-01): `shape check` must not pass a multi-table contract against a single-table profile, and `shape profile <folder>` must not silently read a folder as one table; artifact folders named by the second must not collide. Lane `lane/PF-06b`. | Lead, applying the owner's 2026-10-01 decision. |
| 2026-10-02 | P4-10, GEN-CLI | **Owner decision on the P4-10 GEN-CLI miss** (retail medium 6.83x, gate 10x; large 19.8x; equivalence passes): keep optimising (§6.5 round 2, lane `lane/P4-10r2`); the gate is unchanged. | Owner, 2026-10-02: "Keep optimizing". Evidence: docs/plans/lane_status/P4-10.md. |
| 2026-10-02 | P4-08, P4-10, P4-11, D-13 | **Plan text superseded by D-13 (2026-09-30) in three work packages:** P4-10 `shape validate` dispatches on Shape generation schemas and contracts only (no baseline schema input); P4-11 adds no baseline library-name aliases to `shape.fidelity`; P4-08 has no `--refengine-json` output (Shape's `learn` writes its own schema; equality with the baseline's `learn` is checked in `benchmarks/vs_refengine/learn_1to1/`, outside the package). | Lead, applying the owner's D-13 decision. |
| 2026-10-01 | P1-18, T-22 | **Owner: fix the enum rule.** The profiler marks a column `is_enum` (and lists every value in `enum_values`) when it has fewer than 200 distinct values, or fewer than 50,000 at a cardinality ratio under 0.30, so every column of a table under 200 rows is an "enum", unique keys and free text included (the SQL Server plugin: at most 50 distinct). New work package **P1-18**: a column is an enum only if, in addition, its values repeat (distinct values at most half of the non-null values; a unique column is never an enum); both kernels and the SQL Server plugin. T-22 parity for `is_enum`, `enum_values` (and fields derived from them) becomes a narrow named allow-list; every other field still equal. | Owner, 2026-10-01: "We should probably fix that too right?" |
| 2026-10-01 | P4-01c, P6-08b | **Owner: also fix the further copied baseline behaviours that harm user trust (round 2).** Lead's call on which: P4-01c F6 undeclared FK guesses point at the parent's primary key (never a missing column), F7 generated strings fit declared lengths (CHAR(2)/CHAR(3) codes), F8 `CustomerId`-style keys recognised; P6-08b FIX-4 deterministic spread sample instead of the first N rows, FIX-5 unsampled statistics are null not 0.0, FIX-6 null-aware `is_unique` with a minimum sample, FIX-7 undeclared columns still inferred beside declared keys (evidence marked), FIX-8 whole-word `id`/`key` name matching, FIX-9 a guessed primary key never picks a foreign-key column. Same acceptance as round 1 (test per fix, narrow named parity allow-list, everything else equal). Not changed: `is_enum` (<= 50 distinct), the core profiler's rule under T-22 parity (flagged to the owner). | Owner, 2026-10-01: "Fix those too if they harm user trust". |
| 2026-10-01 | P4-01b, P6-08, T-22, P4-01c, P6-08b | **Owner decision: fix the baseline bugs that P4-01b and P6-08 reproduced for parity.** New work packages **P4-01c** (DDL import) and **P6-08b** (SQL Server profiling). Their acceptance replaces exact equality with the baseline, for these behaviours only, by: a test asserting the correct behaviour per bug, and the parity harness listing each one as an intentional, documented difference (every other field still equal). | Owner, 2026-10-01: "Fix the bugs. Those are not acceptable and would harm user trust in Shape." |
| 2026-10-01 | P4-04c | **Owner accepted** the changed case `conditional/is_null_fixed` (null rate 0.4 -> 0.5 after a chance clause-(d) failure at seed 1042; the original passed at seeds 1043-1049). No seed or tolerance changed. | Owner, 2026-10-01: "Accept is fine". |
| 2026-10-01 | G1 | **Owner decision on the second G1 miss (re-run on c7bd366: only PROF-IN MT 9.1x < 10x):** option (a), keep optimising; the gate is unchanged (10x, D-04). Lane `lane/G1-mt` (started 8:59 AM EDT) targets the MT path, equivalence first, with no other workload allowed below 10x. Nothing merges to main until G1 and G2 pass. | Owner, 2026-10-01: "A". Evidence: docs/plans/evidence/G1-rerun/. |
| 2026-10-01 | G2, PF-01, P3-01, P4-01a, P6-08 | **Early start extended (lead, under the owner's 'run everything that can run in parallel', 2026-10-01).** G2's first two checks pass (an out-of-tree plugin adds a source, a detector and a command without core change; `shape plugins list` shows all 25 built-ins); its third check is 'the G1 gates still pass', so G2 waits on G1. The work packages that depend only on G2 (PF-01, P3-01, P4-01a, P6-08) start now as lanes, under the same rule as the G1 early start: they merge into `build/main-plan`, nothing merges to `main` until G1 and G2 pass. P1-17 left MT at 7.5x (D1 and D4 CSV above 10x on its VM); G1 is re-run after P1-16. | Owner instruction |
| 2026-10-01 | G1, P1-15, P1-16, P2-01, P7-01, P6-09, PF-03 | **Owner decision on the G1 escalation:** options (a) and (b), in parallel; the gate is unchanged (10x, D-04). New work packages **P1-15** (exact-mode `shape.profile` on the fused Rust kernel, as P1-08 intended) and **P1-16** (bitwise-exact, cheaper likelihood evaluation); G1 is re-run after both merge. **Early start:** P2-01, P7-01, P6-09 and PF-03 may start before G1 is `done` (owner, 2026-10-01); they run as lanes and merge into `build/main-plan`, but nothing merges to `main` until G1 passes. | Owner decision |
| 2026-10-01 | §6.1, §6.3 | **Parallel lanes (owner: run everything that can run in parallel).** Work packages whose `Depends` are met run concurrently, each in its own builder session on its own branch `lane/<WP>` cut from `build/main-plan`. A lane builder edits only its work package's paths, never the §11 tracker or §2.3, and records progress and evidence in `docs/plans/lane_status/<WP>.md`. The lead verifies each lane against §7, merges it into `build/main-plan` (merge commit) and updates §11. The tracker shows a lane's WP as `wip (lane/<WP>)`; a builder never picks a WP marked that way. Gates stay sequential, each measured in one dedicated run. | Owner decision |
| 2026-10-01 | G1, P1-08 | **Escalation (builder): the G1 speed gates are missed after §6.5 round 1.** Parity and START pass; PROF-IN is 6.5x-11.2x (d1.parquet 6.5x, d2.csv 9.6x, d3.parquet 6.7x, d4.csv 8.8x, d4.parquet 9.4x, mt 5.1x below 10x) and PROF-CLI D2 is 8.8x (D3 10.8x). Tables, hotspots and harness outputs: `docs/plans/demo_status/G1-evidence.md`. Round 1 moved the lognormal likelihood to reused buffers and numpy's array log (D4 fit 13.0 s -> 5.4 s, parity exit 0). One harness change, for the owner to confirm: `bench.py`'s Shape worker now imports `shape.profile`'s implementation before the timer (T-19: imports excluded for both tools; RefEngine's worker already imports its profiler); without it every fresh-process run carried about 0.4 s of lazy imports (as-found column kept in the evidence). Finding: the product `shape.profile` is the numpy reference profiler with only fitting in Rust, so P1-08's "on the Rust kernel" was not realized; the fused engine is slower than it in exact mode. The remaining gap (D1, MT, D3 parquet, D4, D2 CLI) is not tuning-sized. Options for the owner: (a) a native exact-mode profile on the fused kernel as P1-08 intended (a new work package, then re-run G1), (b) a second tuning round on first-appearance ordering, value counts and correlation (unlikely to reach 10x on D1, MT and D3 parquet). Gate unchanged; G1 stays `todo`. | Builder, §6.5 round 1 |
| 2026-09-30 | P1-07, P1-14 | **Resolved:** the Python-kernel bounded-mode growth was in `finalize`, not the batch loop (`np.repeat(lengths, counts)` made one float64 per row; RSS 231 -> 584 MB in the last second at 24M rows). `_histogram_quantile` computes p95 from the histogram with numpy's linear interpolation (0 mismatches on 60k random histograms); regression test `test_python_text_finalize_memory_does_not_scale_with_rows` fails on the old code. `test_bounded_mode_memory_does_not_grow_with_rows` (unchanged, 10% limit) passes with `SHAPE_KERNEL=python` (63 min) and with Rust (88 s); all 40 heavy kernel/profile tests pass on Rust. P1-14 acceptance, in this session: `check_user_facing` clean (CI runs it on the tree and the wheel); `profile_1to1/verify.py --impl shape` exit 0 on all default datasets against the pinned RefEngine 3.0.1 (built here by `setup_refengine.sh`, checkout untouched); `pytest tests/demo` 201 passed (separate venv with pyarrow 19, as the `fabric-demo` job); the rest of the suite 1026 passed in both kernel modes. Not covered by CI: the heavy memory test on the Python kernel (about 1 h); a nightly job is suggested. | Finding FINDING-P1-07 |
| 2026-09-29 | — | Plan v1 approved | — |
| 2026-10-01 | G1, PROF-IN, PROF-CLI | **Escalation (builder): G1 is not met after both §6.5 rounds; the gate is unchanged.** Equivalence first: `profile_1to1/verify.py --impl shape` exits 0 (49/49 PASS, runs 1 and 2). Measured on the pinned baseline (§1.2), 4 vCPU Xeon 2.80 GHz, exclusive lock, 5 runs each in fresh processes. **Run 2 (final, 12:38 AM EDT 2026-10-01):** PROF-IN D1 csv 10.3x, D1 pq 10.9x, D2 csv 12.3x, D2 pq 12.5x, D3 csv 11.4x, **D3 pq 8.8x**, **D4 csv 9.9x**, D4 pq 10.8x, **MT 5.4x**; PROF-CLI **D2 9.8x**, D3 10.9x; START 43 ms. Evidence: `docs/plans/evidence/G1/`. Round 1 (previous builder, d651afe, 271a5d5): the lognormal likelihood reuses buffers and takes both logs through numpy's array log (D4 fit 13.0 s to 5.9 s). Round 2 (this builder): the top-value scan searches only keys with count at or above the threshold (D2 csv 3.4 s to 1.9 s), `_clean` fast path, and the bench worker pre-imports `shape.api` and the kernel (T-19: imports excluded; the first run's timed call included about 0.4 s of lazy imports). Not kept: chunked parallel numpy log in `fit.rs` (no gain; GIL contention and a deadlock risk). Remaining hotspots (py-spy): D3 pq: `latency_ms` full-column lognormal refit about 3.8 s, about 20 evaluations of 5M rows, each two numpy-SVML log passes plus a sequential `pairwise_sum`, and the numpy `partition`/sort calls in the Python profiler; D4: eight lognormal columns whose Nelder-Mead fallback runs about 600 evaluations over the full column; MT: three small tables, so thread and pool start-up and per-column Python dominate (0.26 to 0.55 s against 2.4 s). The product path for `shape.profile` is still the numpy reference profiler with only the fitting in Rust; P1-06's fused kernel (`profile/engine.py`) is not on that path, so P1-08's "on the Rust kernel" is not met. Options for the owner: (a) move the per-column work (sorting, first-seen top values, patterns, quantiles) onto the fused kernel behind the same T-22 output; (b) make the likelihood evaluation bitwise-exact but cheaper (parallel `pairwise_sum` over its fixed block tree, a Rust port of numpy's AVX-512 log); (c) accept different gates for D4 and MT, which only the owner may decide. G1 stays `todo`. This row supersedes the round-1 escalation row above (its numbers are the run before round 2; `docs/plans/demo_status/G1-evidence.md`). | Builder, G1 |
| 2026-09-30 | P1-07, P1-14 | **Escalation (lead):** `test_bounded_mode_memory_does_not_grow_with_rows` fails with `SHAPE_KERNEL=python` (24M 544 MB → 48M 913 MB, +68%); Rust passes. Measurements and leads in `docs/plans/demo_status/FINDING-P1-07-python-kernel-rss.md`. Must be fixed before P1-14 closes (suite green in both kernel modes) and G1; the test is not relaxed. | Lead verification of P1-08..P1-10 |
| 2026-09-30 | P1-14 | The talk and the demo kit mention nothing about RefEngine either: `docs/talks/` and `demo/` join P1-14's scope (the talk itself is reworked on the talk branch by a separate session). | Owner decision |
| 2026-09-30 | D-12, D-13, §10, P1-08, P1-11, G1 | **No RefEngine on Shape's user-facing surface.** Nothing a user sees names RefEngine: the package (`src/`, `rust/`, `plugins/`, `integrations/`), CLI flags, help and messages, `README.md`, `pyproject.toml`, `demo/` and `docs/` outside `docs/plans/` (the talk included: owner, 2026-09-30). The RefEngine-compatible outputs are removed: `--refengine-compat` (P1-08, P1-11) and the `shape profile capture` / `shape profile diff` ports of RefEngine's `ExportedProfile` commands (P1-11). D-12: stream fields become `_shape_table`, `_shape_seq`, `_shape_event_time` (idempotency key `(_shape_table, _shape_seq)`), `--envelope refengine` is dropped, CloudEvents stays. D-13: Shape reads only its own formats (no RefEngine schema or profile input) and has no RefEngine command aliases; §10 is retired as a parity contract. §12.2 loses `--refengine-compat`. The internal parity and benchmark harness (`benchmarks/vs_refengine/`, T-20, T-22, the G-gates measured against the pinned RefEngine) is kept, outside the package. New work package P1-14; G1 needs it. `THIRD_PARTY_NOTICES.md` keeps its RefEngine attribution until the owner confirms they hold RefEngine's copyright (§9). | Owner decision |
| 2026-09-29 | — | Plan v2: adversarial-review fixes (RefEngine stream format, CLI mapping, exact mode for parity and gates, crate pins, Philox implemented in-house, maturin-action, setup, builder guide, work-package splits, verified line references) | Red-team review |
| 2026-09-30 | — | **sqllocks-shape 0.9.0 published to PyPI** from `main` at b2dd663 (Publish run 36749097798, approved by the owner). Verified by the lead: clean-venv `pip install sqllocks-shape==0.9.0` from pypi.org; pure `py3-none-any` wheel; `License-Expression: MIT`; requires numpy>=2.0,<3 and pyarrow>=14.0.1; profile, check and CLI exit codes work. **0.9.0 can never be re-uploaded: the next release must use a higher version.** | Owner release decision |
| 2026-09-30 | — | Main plan reconciled with the shipped demo: P0-05 keeps version 0.9.0 and the DM-00 README; P0-04 names `src/shape/<m>` paths only; P0-06 keeps the demo CI jobs; P0-07 updates demo references to moved harness paths; §6.2(7) keeps `tests/demo` green | Demo track (§12) landed before Phase 0; 0.9.0 is published |
| 2026-09-30 | T-07 | Package pyarrow range `>=25,<26` replaced by `>=14.0.1`; the benchmark harness keeps pyarrow 25.0.1 in both venvs | CI: `fabric-user-data-functions` 1.0.0–1.0.142 requires `pyarrow>=19.0.1,<20`, so `>=25` made Shape uninstallable next to the UDF SDK (`ResolutionImpossible`) |
| 2026-09-30 | D-15 | License: MIT (owner choice), replacing the inherited Apache-2.0 | Owner decision; matches RefEngine |
| 2026-09-30 | D-15 | Open source: public repo, PyPI release; DM-00 public-readiness cleanup moved ahead of the demo | Owner decision |
| 2026-09-30 | D-14, T-29 | Plan v3.2: 48-hour Fabric demo track (§12: notebook, UDF, pipelines, 3 parallel lanes) and Phase F (Fabric, Synapse and ADF pipeline integration, prioritized after G2); pure-Python wheel as a first-class deliverable | Owner: RefEngine retired; demo profiling in Fabric; pipelines for profiling and generation |
| 2026-09-29 | D-07 | RefEngine does have experimental DP (`tier3_research.DifferentialPrivacy`). Under full parity (D-01) it is ported correctly in P4-11, and Shape's fake DP is still removed in P0-02 | Third red-team review; v1's rationale was false |
| 2026-09-29 | — | Plan v3.1: tiers moved from P1-13 to P4-11 (they compare real against synthetic data); G6/P8-01 deadlock removed; seeded harness layout; P6-14 reference inputs corrected; scikit-learn and pyyaml in the RefEngine venv | Third red-team review |
| 2026-09-29 | — | Plan v3: second review (test ordering in P0-04, `scripts/env.sh`, harness contracts, CLI name collisions, release semantics, Rust 1.85); fitting moved into Rust; new P0-00, P1-13, P6-13 and P6-14; RefEngine coverage map; baselines committed | Second red-team review, profiling hotspot analysis |

---

## 3. Measured baselines and targets

All baselines were measured on 2026-09-29 on a 4-vCPU Linux x86_64 machine (Intel Xeon
at 2.10 GHz), Python 3.11, RefEngine at git `422e78d`. The raw files are committed under
`benchmarks/baselines/2026-09-29/`. Each number cites its file.

### 3.1 Start-up

Source: `retail_bench.json` → `scales.medium.summary.{refengine,port_shape_venv}.import_s`.

| Tool | Import time |
|---|---|
| RefEngine | 1.01 s |
| Shape-style (pyarrow and numpy) | 0.13 s |

### 3.2 Generation: retail domain, medium scale (1,965,400 rows, 9 tables)

Sources: `retail_bench.json` → `scales.medium.summary`, and `retail_verify_medium.json`.
The 1:1 port meets T-21 with 60/60 columns equivalent.

| | Generate | Write Parquet | Total | Rows/s |
|---|---|---|---|---|
| RefEngine | 4.65 s | 0.65 s | **5.29 s** | 372k |
| 1:1 port, pyarrow 25.0.1 (`port`) | 0.55 s | 0.71 s | 1.26 s | 1.56M |
| 1:1 port, pyarrow 23.0.1 (`port_shape_venv`, for reference) | 0.60 s | 0.49 s | 1.09 s | 1.81M |
| **10x minimum** | | | **≤ 0.529 s** | ≥ 3.72M |
| **30x stretch** | | | **≤ 0.176 s** | ≥ 11.1M |

- Vectorizing alone gives ~8x on generation but only ~4.2x in total, because Parquet
  writing doesn't speed up. So the 10x minimum needs the Rust kernel **and** T-17.
- Run-to-run variation is about 8%: an earlier run measured RefEngine at 4.90 s. That is
  why gates are ratios measured in the same job (T-19).
- **Large scale** (19,625,400 rows; `retail_bench.json` → `scales.large.summary`):
  RefEngine 102.6 s (generate 96.6 s, write 6.0 s) against the port's 14.9 s
  (generate 10.7 s, write 4.3 s), which is 6.9x. The 10x minimum is ≤10.26 s.
- **Equivalence evidence** (`retail_verify_small.txt`, `retail_verify_medium.txt`,
  `retail_verify_large.txt`): 60/60 columns equivalent at every scale, with the T-21
  seed set. Every table's `FidelityComparator` score satisfies T-21 (h); at medium,
  for example, `product_category` scores 85.60 against a floor of 84.25.
- **Thin margins at large scale.**
  - Clause (h) margins go as low as 0.21 points (`return` 94.87 against a floor of
    94.66; `address` 88.56 against 88.30).
  - `order.order_total` has KS 0.381 against a tolerance of 0.555, but RefEngine's own
    seed-to-seed KS for that column reaches 0.369 (`retail_large_seed_study.json`).
  - So an implementation as faithful as the reference port can land just below a
    floor by chance, at the one fixed seed.
  - **If P4-07 fails only on clause (h), or only on a column listed in the seed
    study, by a margin smaller than RefEngine's own seed-to-seed spread:** do not
    change seeds and do not tune for the seed. Escalate (§0.4), attaching the
    verifier output and a seed study for Shape (seeds 1042–1046) like
    `retail_large_seed_study.json`. Any other T-21 failure is a real defect.
- The gates use same-job ratios (T-19), not these absolute numbers.

### 3.3 Profiling (in-process, `exact=True`)

Source: `profile_bench.json`. The port column is the 1:1 numpy + pyarrow port, without
Rust.

| Dataset | RefEngine | Port, multi-threaded | Port, single-threaded | 10x target |
|---|---|---|---|---|
| D1 CSV (200k × 6) | 1.53 s | 0.19 s (8.0x) | 0.22 s | ≤ 0.153 s |
| D1 Parquet | 1.42 s | 0.18 s (8.1x) | 0.23 s | ≤ 0.142 s |
| D2 CSV (1M × 20) | 31.1 s | 1.82 s (17x) | 4.99 s | ≤ 3.11 s |
| D2 Parquet | 26.5 s | 1.82 s (14.6x) | 4.83 s | ≤ 2.65 s |
| D3 CSV (5M × 10) | 78.3 s | 5.57 s (14.1x) | 14.5 s | ≤ 7.83 s |
| D3 Parquet | 47.9 s | 5.35 s (9.0x) | 13.7 s | ≤ 4.79 s |
| D4 CSV (100k × 200) | 32.1 s | 5.42 s (5.9x) | 14.1 s | ≤ 3.21 s |
| D4 Parquet | 28.3 s | 4.23 s (6.7x) | 13.3 s | ≤ 2.83 s |
| MT (3 tables, FK detection) | 2.01 s | 0.38 s (5.3x) | 0.36 s | ≤ 0.201 s |

Without Rust, the port passes 10x on D2 and D3 CSV. It misses on D1 (small data, where
fixed overhead dominates), on MT, on D3 Parquet, and on D4 (wide data, where
distribution fitting dominates). The fused Rust kernel (P1-06) and the Rust fitting
routines (P1-08) target exactly those cases. Profile evidence for the hotspots is in
`benchmarks/vs_refengine/profile_1to1/README.md` ("Where the time goes (cProfile)").

The §3.3 D1 numbers were measured on a copy of an earlier 200k-row scratch file with
the same schema. `datasets.py` now regenerates D1 deterministically, so P0-07
re-records D1 in `results.json`, and that record supersedes the D1 rows above.

### 3.4 Gates

Every gate is a ratio against RefEngine, measured in the same job (T-19).

| Gate | Workload (exact commands in `benchmarks/vs_refengine/run.py`) | Minimum | Stretch (tracked, not blocking) |
|---|---|---|---|
| PROF-IN | D1–D4 (CSV and Parquet), MT; in-process; `exact=True` | ≥10x each | ≥30x |
| PROF-CLI | D2 and D3, CSV. RefEngine has no CLI command that runs `DataProfiler` alone (`profile capture` only records categorical distributions through `ProfileIO`), so the RefEngine side is `"$REFENGINE_PY" benchmarks/vs_refengine/profile_1to1/refengine_cli_profile.py <file> -o <out>`: a thin script that imports `DataProfiler`, runs `from_csv` and dumps JSON, with start-up included. Shape: `shape profile <file> -o <out>`, its profile mapped onto RefEngine's `TableProfile` JSON by the harness adapter (`benchmarks/vs_refengine/profile_1to1/adapter.py`). Outputs diffed under T-22 | ≥10x each | ≥30x |
| LEARN-CLI | D2 CSV: `"$REFENGINE_VENV/bin/refengine" learn <file> -o <out>.refengine.json` against `shape learn <file> --refengine-json -o <out>` (profile + schema build on both sides). Outputs compared per P4-08 | ≥10x | ≥30x |
| GEN-IN | retail medium and large, then (from P6-01) every domain at medium; generate + write Parquet | ≥10x each | ≥30x |
| GEN-CLI | retail medium and large: `"$REFENGINE_VENV/bin/refengine" generate retail --scale S --format parquet -o D` against `shape generate retail --scale S --format parquet -o D` | ≥10x | ≥30x |
| STREAM-EMIT | `"$REFENGINE_VENV/bin/refengine" stream retail --table order --scale medium --no-realtime --sink file -o F` against `shape stream retail` with the same options; same `--max-events` (default: all rows). Shape generates the full table and emits the same first N events ordered by `_refengine_event_time`. The event multiset must pass T-21 (b)–(e) before timing counts | ≥10x | ≥30x |
| STREAM-PROF | stream profiling of a D2 replay in 64k-row micro-batches (bounded mode), against Shape batch profiling of D2 in bounded mode (no RefEngine equivalent exists) | ≥80% of batch throughput | ≥95% |
| START | median of 10 runs of `shape --version`, wall clock | ≤300 ms | ≤150 ms |

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
│ io · profile · spec/artifact · diff · drift · quality · contracts · fidelity             │
│ generation (schema, engine, strategies) · streaming (consume, emit) · privacy · registry │
│ plugin host · cli                                                                        │
└───────────────▲───────────────────────────────────────────────▲──────────────────────────┘
                │ Arrow PyCapsule interface (zero-copy)          │
┌───────────────┴─────────────── shape._kernel (Rust) ──────────┴──────────────────────────┐
│ fused profile pass · hash · HLL/KLL/SpaceSaving · patterns · temporal histograms          │
│ type inference · distribution fitting (MLE, Nelder-Mead, KS) · correlation               │
│ Philox RNG · alias sampling · pool/string assembly · temporal/holiday sampler            │
│ row-sequential strategies (lifecycle, SCD2, state machines, self-reference)              │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                 Arrow C++ (pyarrow): CSV/Parquet/JSONL/IPC read, Parquet/IPC write
```

### 4.2 Runtime split rules

1. **Python never touches individual values on a hot path.** Each per-batch operation
   is one native call that covers every column.
2. **Every Rust function has a Python reference twin** (T-03), kept in agreement by
   differential tests.
3. **Plugin hooks take whole batches:** Arrow `RecordBatch` or arrays in and out, never
   single values or rows. A plugin that needs speed may ship its own Rust extension.
4. **Arrow is the only in-memory format.** Dicts and DataFrames are converted at the
   edge.
5. **There is one profile schema** for batch, stream, merged and partitioned results.

### 4.3 Plugin API v1

- **Entry-point groups,** each with a Protocol in `shape.plugins.api.v1`:

  | Group | Protocol | Purpose |
  |---|---|---|
  | `shape.sources` | `Source` | URI or scheme → `RecordBatch` iterator |
  | `shape.sinks` | `Sink` | `RecordBatch`es for a table → a destination |
  | `shape.detectors` | `SemanticDetector` | Array → (label, confidence) |
  | `shape.fitters` | `DistributionFitter` | Sample array → family, parameters, KS |
  | `shape.strategies` | `Strategy` | Column spec + context → Arrow array for one chunk |
  | `shape.distributions` | `Distribution` | Parameters + RNG stream → array |
  | `shape.calendars` | `Calendar` | Date range → per-date lift factors |
  | `shape.domains` | `Domain` | Schema + reference data + profiles + scale presets |
  | `shape.chaos` | `ChaosMutator` | Batch → mutated batch plus a report |
  | `shape.emitters` | `Emitter` | Event batches → an external stream |
  | `shape.stream_sources` | `StreamSource` | Offsets and batches from an external stream |
  | `shape.transforms` | `Transform` | Tables → tables |
  | `shape.commands` | `Command` | `shape <name>` subcommands |
  | `shape.reports` | `ReportFormat` | Report → bytes |

- **Versioning and loading:** every plugin declares `SHAPE_API = "1.x"`, and the host
  rejects major mismatches with a clear error. A plugin that fails to load never
  crashes core; `shape plugins doctor` reports it. Plugins load lazily.
- **Built-ins** are everything core ships in `src/shape/builtins/`: the CSV, Parquet,
  JSONL and IPC sources and sinks, the detectors, fitters, strategies and
  distributions, the US calendars and the address strategy. They register through the
  same entry points as any plugin. An import-linter contract forbids importing
  `shape.builtins` from anywhere except `shape.plugins.registry`.

---

## 5. Target repository layout

```
pyproject.toml                  maturin mixed project (core distribution)
Makefile · CLAUDE.md · README.md · CHANGELOG.md · LICENSE · THIRD_PARTY_NOTICES.md
rust/shape-kernel/              Cargo crate → shape._kernel
  src/{lib.rs, ffi.rs, hash.rs, profile/*.rs, sketch/{hll,kll,spacesaving}.rs,
       gen/{rng,alias,pool,string,temporal,sequential}.rs}
src/shape/
  __init__.py  api.py  errors.py  types.py
  kernel/         dispatch.py · reference/
  io/             readers.py · writers.py · uri.py
  builtins/       sources/ sinks/ detectors/ fitters/ strategies/ distributions/ calendars/
  profile/        engine.py · infer.py · fit.py · patterns.py · keys.py · temporal.py · merge.py
  spec/           model.py · schema/shape-v2.schema.json · migrate.py · io.py
  artifact/       shape_file.py · io.py · canonical.py · sign.py
  capture/        edge adapters only (rows/dicts/DataFrames → Arrow)
  model/  types/  security/  validation/  location/  geospatial/
  diff/ drift/ quality/ contracts/ query/ registry/ relations/
  generation/     schema.py · refengine_import.py · schema_builder.py · ddl.py · engine.py ·
                  rules.py · compute.py · fidelity.py · report/
  streaming/      consume/ (windows, keyed, checkpoint, runtime) · emit/ (runtime, rate, formats)
  privacy/        classification.py · release.py · safe_profile.py · kanon.py
  plugins/        api/v1.py · host.py · registry.py · kit.py
  bridge/         JSON stdin/stdout bridge (RefEngine mcp_bridge parity)
  cli/            main.py · commands/ · aliases.py
plugins/
  shape-kafka/  shape-eventhubs/  shape-fabric/  shape-sqlserver/
  shape-domains/  shape-simulation/
benchmarks/
  baselines/2026-09-29/         raw baseline JSON (committed, immutable)
  vs_refengine/                   setup_refengine.sh · run.py · results.schema.json · dump_schema.py ·
                                profile_1to1/ · domain_1to1/ · stream_1to1/
ci/emulators/docker-compose.yml
tests/  (mirrors src/shape and plugins/; property/, differential/, e2e/)
docs/   (mkdocs site; specs/; plans/COMPLETION_PLAN.md)
```

`connectors/` and `packs/` are folded into `io/`, `builtins/` and plugins by P2-04 and
P6-01.

---

## 6. Execution rules

### 6.1 Order

```
§12 demo track (before everything, parallel lanes)
P0 ─► P1 ─► P2 ─┬─► PF (priority) ─────────────┐
          │     ├─► P3 ────────────┐           │
          │     └─► P4 ─┬──────────┴─► P5 ─► P6 ┴─► P8
          │             └─► PF-06
          └─► P7-01..03 (after G1)   P7-04 after G5; G7 and GF needed before P8-04
```

- **Phase F has priority but does not block.** It sits before Phases 3 and 4 in the
  tracker, so the builder picks it first. P3 and P4 do not depend on GF, because GF
  includes an owner dry run (O-07).

- Phases 0, 1 and 2 are sequential.
- P3 and P4 start after G2, and may interleave.
- P5 needs G3 and G4. Phase 6 work packages follow their own `Depends` fields: most
  need G4, P6-08 needs only G2, and P6-09 needs only G1.
- P7-01 to P7-03 need only G1. P7-04 needs G5.
- P8 needs G6 and G7.
- Within a phase, follow each work package's `Depends` field. When several work
  packages are available, take them in tracker order.
- **Gates:** after the last work package of a phase, run every check the gate lists,
  then set the gate's row in §11 to `done` with the commit hash. `Depends: Gx` means
  that gate row is `done`.

### 6.2 Definition of done (per work package)

1. Every acceptance criterion passes: locally where it can run, otherwise in CI
   (emulator and live tests, and multi-platform wheels).
2. Within T-27 scope: `ruff check`, `ruff format --check`, `mypy` (strict for every
   module touched), `pytest -m "not emulator and not live"`, and the Rust `cargo fmt`,
   `clippy` and `cargo test` once Rust exists.
3. Every bug ID listed has a regression test that fails before the fix. For code that
   is deleted, the regression test asserts that the import raises
   `ModuleNotFoundError`.
4. No tracked benchmark regresses by more than 10% (T-19), measured against the median
   of the last nightly run. Until the first nightly run exists, the reference is the
   committed `benchmarks/vs_refengine/results.json`. This applies from P0-07 onward.
5. The docs affected by the change are updated in the same commit.
6. The tracker row (§11) is set to `done`, with the commit hash.
7. **The shipped early-access product keeps working.** `pytest tests/demo` stays green
   (the Fabric tests need `tests/demo/fabric/requirements.txt` and unixODBC; the content
   tests need `$REFENGINE_ROOT`), and the §12.2 API and CLI stay as specified.
   `src/shape/profile/reference/` is the pure-Python profiler behind `shape.profile`
   and the pure wheel (T-29), so it is kept. When P1-07's engine takes over
   `shape.profile`, DM-01's parity run (`verify.py --impl shape`) must still exit 0.

### 6.3 Branches, commits and PRs

- Work on the branch assigned to your session.
- Make one commit or more per work package, with each message starting with its ID,
  for example `P1-03: sketches (HLL, KLL, SpaceSaving)`.
- Push after every completed work package. Don't wait for merges: later work packages
  build on the same branch.
- Open a PR to `main` at each phase gate **only if the owner has asked for PRs**.
  Otherwise push the branch and report.

### 6.4 Rules that are never broken

- **Equivalence before timing.** No performance number counts until that workload's
  equivalence verifier passes on the timed output.
- **Never lower a gate or loosen a tolerance** to get green.
- **Never skip, disable, xfail or quarantine a test** to get green.
- **Never fabricate evidence.** Never commit results that the harness in this repo did
  not produce.
- **No claim without enforcement.** No statement about performance, fidelity, privacy
  or security may ship unless a test or benchmark enforces it.
- **Never modify the RefEngine checkout.**

### 6.5 When a gate is missed

1. Profile with T-28 tools, and list the top hotspots.
2. Move per-value or per-row Python work into Rust (§4.2).
3. Re-measure. Repeat steps 1–3 at most twice.
4. If the gate is still missed, escalate (§0.4). The gate itself is not changed.

---

## 7. Work packages and phase gates

Format: **ID — title** · Depends · Deliverables · Acceptance · Fixes. Bug IDs are in
Appendix A.

### Phase 0 — Honest baseline

**P0-00 — Bootstrap**
- Depends: none.
- Deliverables:
  - `scripts/env.sh`, containing exactly the §1 variable block (exports with defaults).
  - Add the pytest markers `sign`, `contract`, `emulator` and `live` to the existing
    marker list in `pyproject.toml` (which already has `conformance`, `security` and
    `zero_network`). The suite runs with `--strict-markers`.
  - Confirm that `CLAUDE.md` exists and points at this plan.
- Acceptance: `source scripts/env.sh && echo "$REFENGINE_PY"` prints a path, and
  `pytest --collect-only -q` succeeds.
- Fixes: none.

**P0-01 — Registry security**
- Depends: none.
- Deliverables: `registry/local.py` validates names against
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, and resolves paths under the root, rejecting
  anything outside it. `checkout(name, ref)` resolves only refs and hashes recorded in
  that name's log.
- Acceptance: tests show that `commit("../x")`, absolute names and symlink escapes
  raise `RegistryError`, and that `checkout("public", <content hash of "secret">)`
  raises `RegistryError`.
- Fixes: SEC4, SEC5.

**P0-02 — Remove differential privacy (D-07)**
- Depends: P0-04.
- Deliverables: delete `privacy/advanced.py`'s `laplace`, and every DP symbol and doc
  claim.
- Acceptance:
  `grep -rniE "differential privacy|laplace|epsilon" src docs README.md --exclude=COMPLETION_PLAN.md`
  returns nothing, and `import shape.privacy.advanced` either has no `laplace` or
  raises `ModuleNotFoundError`.
- Fixes: SEC1.

**P0-03 — Enforce release policy**
- Depends: none.
- Deliverables: `privacy/policy.py::release_for` behaves as follows.
  - **Column-level:** a column labelled above the target has every value-bearing key
    removed, and `allowed` stays `True`. The keys are the existing
    `privacy/policy.py::VALUE_KEYS`, plus `q25`, `q50`, `q75`, `enum_values`,
    `value_counts_ext`, `pattern_examples` and `distribution_params`.
  - **Artifact-level:** `allowed=False` with reason `source_exceeds_target` when
    `source_classification` is above the target.
  - **Cohort:** `allowed=False` with reason `cohort_below_minimum` when
    `rows < minimum_cohort`.
- This is a deliberate spec change. The existing tests that assert a sanitized
  derivative of a higher-classified source is *allowed* must be rewritten to assert
  `allowed=False, reason="source_exceeds_target"`:
  - `tests/security/test_ga_security.py` (~44–52)
  - `tests/security/test_ga_security_extended.py` (~92–95)
  - `tests/torture/test_cross_feature_torture.py` (~81)

  Rewriting them is not skipping them.
- Acceptance: a PII column released to PUBLIC contains none of the listed keys; both
  denial cases return `allowed=False` with the stated reason; the three rewritten
  tests pass.
- Fixes: SEC2.

**P0-04 — Delete the cut modules (§8.1)**
- Depends: none.
- Deliverables:
  - **First:** move `test_privacy` from `tests/test_future_roadmap.py` to
    `tests/privacy/test_advanced.py`, keeping only the `k_anonymous` and
    `reidentification_risk` assertions. Then delete `tests/test_future_roadmap.py`.
  - Delete the modules and the tests in §8.1, in the order given there. Every module
    name means `src/shape/<m>` only: `integrations` is `src/shape/integrations/`, not
    the repo-root `integrations/fabric/` (the Fabric demo, kept); `reference` is
    `src/shape/reference/`, not `src/shape/profile/reference/` (the shipped profiler,
    kept).
  - For each module, run `git rm -r src/shape/<m>`, then `rm -rf src/shape/<m>`, and
    assert `test ! -e src/shape/<m>`. A leftover `__pycache__` directory would keep
    the module importable as a namespace package.
  - Rewrite `tests/torture/test_all_modules.py` to parametrize over
    `pkgutil.walk_packages(shape.__path__, "shape.")`, instead of reading
    `rq/torture_inventory.json`. (DM-00 already did this: verify it.)
- Acceptance:
  - `python -c "import shape, shape.cli.main"` succeeds, and `pytest` is green.
  - For each deleted module, a parametrized test asserts `ModuleNotFoundError`.
- Fixes: SEC6, SEC7, SEC8, RM1–RM8.

**P0-05 — Repo honesty**
- Depends: P0-04.
- If DM-00 is done, its deletions and README/CHANGELOG rewrite are already in place:
  verify them and do the rest.
- Deliverables:
  - Delete everything in §8.3.
  - README and CHANGELOG: keep DM-00's rewrite (early access; the §12.2 quick start,
    whose every line is tested). Only remove claims that are still false. The README
    must keep the words "early access": `publish.yml` checks for them.
  - **Do not change the version.** It is `0.9.0` in `pyproject.toml` and
    `src/shape/__init__.py`, and 0.9.0 is published (TestPyPI, and PyPI after the
    owner's release). A lower version such as `0.9.0.dev0` would sort below it.
    Versions change only when the owner releases (P8-04 or an owner request).
  - Apply T-07 and T-08:
    - dependencies `numpy>=2.0,<3` and `pyarrow>=14.0.1` (T-07);
    - remove `pydantic` and `typing-extensions`;
    - add the `[sign]` extra, and make `shape.security` import `crypto` lazily;
    - extend `[dev]` with xxhash, maturin, import-linter, vulture, bandit, py-spy and
      sqlglot.
  - Mark the tests that need cryptography with `@pytest.mark.sign`:
    `tests/security/test_crypto.py`, `tests/artifact/test_secure.py` and
    `tests/security/test_ga_security_extended.py`, plus any others found by
    `grep -rl "cryptography\|security.crypto\|artifact.secure" tests`.
  - Update `[tool.hatch.build.targets.sdist] include` to drop deleted paths (`/rq`,
    `/*.json`, `/GA_FINAL_TEST.txt`). hatch is replaced in P1-01a.
  - Retitle `docs/specs/SHAPE_1_0_GA.md` line 1 to drop "GA". P1-10 renames the file.
- Acceptance:
  - No path in §8.3 exists.
  - In a fresh venv without `cryptography`, `python -c "import shape"` and
    `pytest -m "not sign"` pass.
  - `grep -rniE "\bGA\b|certified|production[- ]ready|isolat" README.md CHANGELOG.md docs --include=*.md --exclude=COMPLETION_PLAN.md`
    returns nothing.
- Fixes: X3.

**P0-06 — CI truthfulness**
- Depends: P0-05.
- Deliverables:
  - `.github/workflows/ci.yml` runs, on the T-06 matrix and with T-27 scope: ruff
    lint and format check, mypy with the ratchet (T-12), pytest with coverage
    (`--cov-fail-under` set to the current value, rounded down), `compileall` and
    `pip-audit`.
  - Keep the existing `fabric-demo` job (Fabric tests on Python 3.11 with Java,
    unixODBC and the pinned RefEngine fetch for `tests/demo/content`) and the Windows
    legs of the matrix.
  - `.github/workflows/nightly.yml` is a placeholder until P0-07.
  - `release.yml` and `security.yml` are updated to the new layout.
  - `make check` runs the same commands.
  - Run `ruff format` once over T-27 scope.
- Acceptance: CI is green on the session branch, and `make check` exits 0.
- Fixes: X1, X2 (continued by §6.2(3)).

**P0-07 — Benchmark harness**
- Depends: P0-05.
- Deliverables:
  - Move the reference ports:
    - `git mv benchmarks/retail_1to1 benchmarks/vs_refengine/domain_1to1`
    - `git mv benchmarks/profile_1to1 benchmarks/vs_refengine/profile_1to1`

    Update every reference to the old paths in the same commit: `demo/make_data.py`,
    `demo/build_benchmark_sheet.py`, `tests/demo/`, `docs/plans/demo_status/`,
    `integrations/fabric/` and `.github/workflows/`
    (`git grep -n "retail_1to1\|profile_1to1"`). `pytest tests/demo` must stay green.

    Recorded outputs in those directories (`bench_results.json`, `verify_*.json`,
    `verify_*.txt`, `verify_output.txt`) are already preserved in
    `benchmarks/baselines/2026-09-29/`. Delete them from the moved directories, and
    rewrite the READMEs to use `$BENCH_*`/`$REFENGINE_*` variables instead of machine
    paths.
  - **Domain harness contract:**
    - Output layout: `$BENCH_OUT_DIR/<impl>/<domain>/<scale>/seed<N>/<table>.parquet`.
    - `domain_1to1/generate.py --impl refengine|reference_port|shape --domain D --scale S --seed N`
      writes one run, each impl in its own venv (RefEngine's, or Shape's).
    - `domain_1to1/verify.py --domain D --scale S --impl reference_port|shape` runs in
      the RefEngine venv:
      - it first generates any missing directories: `refengine` seeds 42, 43, 44, 45
        and 46, and `<impl>` seed 1042;
      - it reads Parquet only, and takes tables, FKs and business rules from the
        `dump_schema.py` output, with no hard-coded retail logic;
      - it asserts every T-21 clause (a)–(h), and **exits 1 unless all of them hold**;
      - it has no option to change the baseline seed set (T-21), and exits 2 if the
        seed-42 or seed-43–46 outputs are missing or incomplete.

      The current `verify.py` generates in memory; P0-07 refactors it to this
      contract.
    - `domain_1to1/bench.py --impl reference_port|shape` times generate + write. Its
      timed runs write the `seed42` directory (RefEngine) and the `seed1042` directory
      (impl), and those same directories are then verified.
    - `reference_port` supports retail only, and exits 2 for any other domain.
      `run.py --full` runs `reference_port` for retail only.
  - **Profile harness contract:** `profile_1to1/verify.py --impl X` exits 1 on any
    field outside T-22. `datasets.py` accepts `--rows N` for D3.
  - Every path comes from the §1 variables. D1 is always regenerated by `datasets.py`
    with its fixed seed and never read from elsewhere.
  - `setup_refengine.sh` (§1.2).
  - `dump_schema.py`: runs in the RefEngine venv. For each domain and each mode (3nf,
    star), it serializes the `RefEngineSchema` from `Domain._build_schema()` to
    `$BENCH_OUT_DIR/schemas/<domain>_<mode>.json`.
  - `check_coverage.py`: every file under `$REFENGINE_ROOT/sqllocks_refengine/`
    (excluding `__pycache__`) must match at least one glob in
    `docs/plans/refengine_coverage.tsv` (fnmatch, with an equal number of path
    segments), and every work package named there must exist in §11. Exit 1
    otherwise.
  - Fix the ruff findings in the moved files (T-27), then re-run both verifiers to
    prove their verdicts are unchanged.
  - `run.py`, with `--quick` (D1, D2 and retail small and medium; 3 runs) and
    `--full` (every workload in §3.4; 5 runs). It runs each verifier, then each
    benchmark, and writes `results.json` against `results.schema.json`.
  - Each verifier and bench script takes `--impl reference_port|shape`, where `shape`
    means the product code in `src/shape`. `results.json` records both; `shape` stays
    `null` until the product path exists.
  - `nightly.yml` runs `--full`, and the PR job runs `--quick` on a 4-vCPU runner.
  - `ci/emulators/docker-compose.yml` (T-26).
  - Delete `benchmarks/prototypes/`, `benchmarks/benchmark_012.py` and
    `benchmarks/benchmark_local.py`.
- Acceptance:
  - `grep -rnE '/tmp/|/home/' benchmarks/ --exclude-dir=baselines --exclude-dir=__pycache__`
    returns nothing.
  - `check_coverage.py` exits 0.
  - `domain_1to1/verify.py --domain retail --scale small --impl reference_port` exits 0.
  - `profile_1to1/verify.py --impl reference_port` exits 0.
  - On a fresh machine, following §1, then
    `source scripts/env.sh && python benchmarks/vs_refengine/run.py --quick` produces a
    schema-valid `results.json`. It holds verifier status and numbers for `refengine`
    and `reference_port`, and a `shape` key that is present with the value `null`.
  - Commit `benchmarks/vs_refengine/results.json` (the §6.2(4) reference until nightly
    runs exist).
- Fixes: none.

**Gate G0**
- CI is green.
- `run.py --quick` works from scratch.
- SEC1, SEC2, SEC4 and SEC5 have regression tests.
- `benchmarks/baselines/2026-09-29/` is present and unchanged.

### Phase 1 — Profiling engine

**P1-01a — Rust build and FFI**
- Depends: G0.
- Deliverables:
  - maturin mixed layout (T-05, replacing hatch) and the `rust/shape-kernel` skeleton
    (T-02 pins).
  - `shape._kernel.version()`.
  - Import and export of `RecordBatch` through the PyCapsule interface.
  - `kernel/dispatch.py` (T-03).
- Acceptance:
  - A 1M-row, 10-column batch round-trips through Rust with identical buffer
    addresses (zero-copy).
  - `SHAPE_KERNEL=python pytest` and `SHAPE_KERNEL=rust pytest` are both green.
- Fixes: none.

**P1-01b — Wheels in CI**
- Depends: P1-01a.
- Deliverables: a T-04 wheel workflow, and an sdist build job.
- Acceptance: wheels for every T-04 target are built, installed and smoke-tested
  (`import shape; shape._kernel.version()`) on 3.11 and 3.14.
- Fixes: none.

**P1-02 — Hash and canonicalization**
- Depends: P1-01a.
- Deliverables: T-13, in Rust and as a Python reference.
- Acceptance: property tests on 10⁶ values of every Arrow primitive type show that Rust
  equals the reference; `1` and `1.0` hash equal; NaN and null are excluded; hashes are
  identical under `PYTHONHASHSEED` 0, 1 and random.
- Fixes: P7, S1.

**P1-03 — Sketches**
- Depends: P1-02.
- Deliverables: T-14 in Rust with Python references, serialized in `error_models`.
- Acceptance (Hypothesis):
  - Merges are associative and commutative. SpaceSaving results are within its error
    bound, and HLL registers match exactly.
  - Profiling 1 batch equals profiling N batches, within the bounds.
  - SpaceSaving holds at most `capacity` entries after 10⁷ updates.
  - HLL relative error ≤ 3 × 1.04/√2¹⁴ at the 99th percentile over 200 trials.
  - KLL rank error ≤ 1%.
- Fixes: P5, P6, P9.

**P1-04 — Readers**
- Depends: P1-01a.
- Deliverables:
  - `io/readers.py`: CSV (pyarrow.csv, with inference and schema overrides), Parquet
    (streamed by row group), JSONL, IPC, `dict[str, array]`, pandas and polars
    (zero-copy where possible), and row iterables (edge adapter).
  - Glob and directory expansion.
- Acceptance: a golden reader test set gives correct types, and
  `connectors/files.py::CSVSource` is removed.
- Fixes: P1.

**P1-05 — Type inference**
- Depends: P1-04.
- Deliverables:
  - Rules in `profile/infer.py` that reproduce RefEngine's dtype classification (read
    `inference/profiler.py` and `benchmarks/vs_refengine/profile_1to1/port.py`).
  - Demotion keeps all evidence gathered so far.
- Acceptance:
  - The dtype of every column in every T-22 dataset matches RefEngine.
  - `[1, 2, "x", 3]` gives count 4, with all four values in the value counts.
  - `[None, 1, 2]` is integer.
  - `np.int64` and `Decimal` are numeric.
- Fixes: P2, P3, P4.

**P1-06 — Fused profile kernel**
- Depends: P1-03, P1-05.
- Deliverables: one Rust call per batch, covering every column, in both modes (T-15).
  - **Exact mode:** counts, nulls, NaN and ±inf, min/max, moments, exact distinct and
    value counts, sort-based quantiles, string lengths, pattern classes and temporal
    histograms.
  - **Bounded mode:** the T-14 sketches replace the exact structures.
  - rayon runs across columns and chunks.
- Acceptance:
  - Differential tests against the reference, in both modes.
  - `py-spy` shows <5% of samples in Python frames when profiling D2 and D4.
- Fixes: P10, P11, P13.

**P1-07 — Profile engine**
- Depends: P1-06.
- Deliverables: `profile/engine.py`: the batch loop, merge, T-15 modes,
  `SHAPE_THREADS`, and multi-input profiling in one process.
- Acceptance:
  - In bounded mode, peak RSS on D3 at 5M rows and at 50M rows (`datasets.py D3 --rows 50000000`)
    differs by <10%.
  - Exact and bounded output validate against the same JSON Schema.
- Fixes: P12, P17.

**P1-08 — RefEngine profiler parity**
- Depends: P1-07.
- Deliverables: port the semantics of `benchmarks/vs_refengine/profile_1to1/port.py`
  into `profile/` as product code, on the Rust kernel:
  - distribution fitting: RefEngine's families, estimators and sampling rules. That means
    a 2000-row fitting sample, a 1000-row pattern sample, and the full-column refit
    for `fit_score`, exactly as `profile_1to1/port.py` reproduces them (including
    scipy 1.17's lognormal fit: its root-finder, the Nelder-Mead fallback and the exact
    KS p-value). **The fitting routines run in the Rust kernel**, with the port's
    numpy code as the reference twin. On D4, fitting is ~13.7 s of RefEngine's ~32 s
    and ~7.5 s of the single-threaded port, so it can't stay in Python.
    `[scipy]` stays optional, for extra families only;
  - pattern detection;
  - enum detection with weights;
  - PK detection, and FK detection across tables (MT);
  - outliers and quantiles;
  - every other `ColumnProfile` and `TableProfile` field.
  - `--refengine-compat` output: the `TableProfile` JSON produced by
    `profile_1to1/refengine_dump.py` (not the `profile capture` format; see P1-11).
- Deliverables (additional): close deviations 1–3 listed in
  `benchmarks/vs_refengine/profile_1to1/README.md` ("Deviations, stated plainly").
  Deviations 4–7 are accepted as documented: they are proven equivalent, or they
  describe RefEngine's redundant work, which Shape must **not** reproduce. Add EDGE
  datasets to `datasets.py` for each closed deviation:
  - date and time strings that only dateutil accepts (for example "May 5"), and
    time-only strings;
  - thousands separators, integers above int64, and quoted numbers in CSV;
  - pandas' chunked mixed-type inference cases;
  - Parquet decimal, dictionary/categorical, nested and timezone-aware columns.

  Where RefEngine itself errors on an input, Shape must raise an error of the same
  category, and the verifier checks that.
- Acceptance: `profile_1to1/verify.py --impl shape` exits 0 (T-22) on D1–D4, MT and
  every EDGE variant, including the new ones.
- Fixes: P16.

**P1-09 — Model and artifact v2**
- Depends: P1-07.
- Deliverables: Shape model v2 and its JSON Schema; the v1→v2 migrator; `.shape` v2,
  with explicit NaN/inf encoding; reader errors wrapped as `ArtifactError`; tuples
  round-trip.
- Acceptance:
  - Hypothesis round-trip tests.
  - Every v1 fixture in `tests/` migrates.
  - The existing artifact fuzz tests pass on v2.
- Fixes: P8, P18, P20.

**P1-10 — Downstream on v2**
- Depends: P1-09.
- Deliverables: port diff, drift, quality, contracts, query and relations to v2.
  - `unique` uses the exact distinct count in exact mode, and the HLL error bound in
    bounded mode.
  - The null rate is correct when `rows = 0`.
  - `relationship()` matches exactly.
  - Rename `docs/specs/SHAPE_1_0_GA.md` to `docs/specs/SHAPE_2.md` and update it to v2.
  - `shape conformance` runs a test for every MUST and MUST NOT statement in
    `SHAPE_2.md`.
- Acceptance:
  - A test exists for each fix.
  - A script counts the MUST and MUST NOT statements in `SHAPE_2.md` and asserts one
    conformance test per statement.
- Fixes: P14, P15, P21, X4.

**P1-11 — Profiling CLI**
- Depends: P1-10.
- Deliverables:
  - `shape profile` for files, globs and directories.
  - `--refengine-compat`, which emits RefEngine's `TableProfile` JSON as dumped by
    `profile_1to1/refengine_dump.py`.
  - `shape profile capture`, a port of RefEngine's `profile capture`
    (`inference/profile_io.py::ProfileIO.from_dataframe` plus `ExportedProfile`), with
    the same output format.
  - `shape profile diff` and `shape diff`; `inspect` (alias `show`); `validate`;
    `check`, which exits non-zero on failure.
  - Lazy imports.
  - `benchmarks/vs_refengine/profile_1to1/refengine_cli_profile.py` (§3.4 PROF-CLI).
- Acceptance:
  - CLI e2e tests.
  - `shape profile capture` output on D1 and D2 is JSON-equal to `refengine profile capture`.
  - `shape profile diff` agrees with `refengine profile diff` on a pair of captures.
  - START ≤300 ms; PROF-CLI ≥10x.
- Fixes: none.

**P1-12 — Remove legacy paths**
- Depends: P1-11.
- Deliverables: delete `capture/vectorized.py`, `profile/text_vectorized.py`,
  `profile/sketches.py`, `profile/bounded_dependencies.py` and `kernel/batches.py`,
  and the internals of `capture/core.py:capture_rows`, which becomes an edge adapter.
- Acceptance: `vulture src/shape --min-confidence 80` reports nothing, and the suite
  is green.
- Fixes: none.

**P1-14 — RefEngine removed from the user-facing surface**
- Depends: P1-12.
- Owner decision 2026-09-30 (§2.3). It amends P1-08 (`--refengine-compat`) and P1-11
  (`--refengine-compat`, the `profile capture` / `profile diff` ports and their RefEngine
  acceptance checks).
- Deliverables:
  - Remove `--refengine-compat`, `shape profile capture` and `shape profile diff`, with their
    tests and documentation. `shape profile`, `shape diff`, `inspect` (`show`), `validate` and
    `check` stay.
  - No mention of RefEngine (case-insensitive, identifiers such as `infer_refengine_type` and
    `refengine_compat` included) in `src/`, `rust/`, `plugins/`, `integrations/`, `README.md`,
    `pyproject.toml`, `demo/`, or `docs/` outside `docs/plans/`. Rename identifiers
    to neutral names; rewrite docstrings and comments to describe the behaviour on its own
    terms. The one exception is the RefEngine attribution in `THIRD_PARTY_NOTICES.md` (§2.3).
  - The parity harness stays internal. `benchmarks/vs_refengine/` keeps T-22 verification and
    the timings against the pinned RefEngine, through an adapter in `benchmarks/` (never in
    `src/`) that maps Shape's profile onto RefEngine's `TableProfile` JSON. `bench_cli.py` times
    `shape profile <file> -o <out>` against `refengine_cli_profile.py`, equivalence first
    through the adapter. Remove `verify_cli.py`'s capture and diff checks.
  - `scripts/check_user_facing.py`: exits 1 on any match in the paths above and in the files
    of a built wheel. It runs in CI and in `make check`.
- Acceptance:
  - `scripts/check_user_facing.py` exits 0, in CI.
  - `profile_1to1/verify.py --impl shape` exits 0 on all its default datasets.
  - `pytest tests/demo` is green, and the §12.2 API is unchanged apart from the removal of
    `--refengine-compat` (§2.3).
  - The suite is green in both kernel modes.
- Fixes: none.

**P1-15 — Exact-mode profile on the fused kernel**
- Depends: P1-14. Owner decision 2026-10-01 (§2.3, G1 option a).
- Deliverables: `shape.profile` (the product path, exact mode) runs its per-column work on
  the fused Rust kernel (P1-06): sorting and quantiles, first-seen top values and value
  counts, pattern detection, enum and key detection, outliers, string lengths, temporal
  histograms, correlation; Python keeps orchestration only (§4.2). The numpy reference
  profiler stays as the reference twin (`SHAPE_KERNEL=python`). The output is unchanged.
- Acceptance: `profile_1to1/verify.py --impl shape` exits 0 on all default datasets (T-22);
  the suite is green in both kernel modes; py-spy shows no per-row Python on the profile
  path; PROF-IN and PROF-CLI re-measured per T-19 and recorded (the G1 re-run decides).
- Fixes: G1 escalation (§2.3).

**P1-16 — Cheaper likelihood, bitwise-exact**
- Depends: P1-14. Owner decision 2026-10-01 (§2.3, G1 option b).
- Deliverables: the lognormal (and other) likelihood evaluations in `rust/shape-kernel/src/fit.rs`
  stay bitwise-identical to the reference (numpy's `log` and `pairwise_sum` block tree) but
  cost less: parallel `pairwise_sum` over its fixed block tree, a native port of numpy's
  vectorised log with identical results, and fewer full-column passes in the Nelder-Mead
  fallback where the result is provably identical.
- Acceptance: T-22 parity exits 0 on all default datasets; differential tests show bitwise
  equality against the reference on fuzzed inputs; D3 Parquet and D4 fit times recorded.
- Fixes: G1 escalation (§2.3).

**P1-17 — Small-input fixed costs and the CSV reader**
- Depends: P1-15. Owner decision 2026-10-01 (§2.3, G1 option a, continued).
- Deliverables: after P1-15 the remaining G1 misses outside fitting are the multi-table
  workload (MT, 7.0x: per-call and per-column fixed costs, pool start-up on three small
  tables) and D1 CSV (9.2x), plus the CSV reader's per-token pandas emulation (about 1.3 s
  of D4 and 3 s of D2, single-threaded). Remove those fixed costs and move the CSV reader's
  per-value work onto the kernel; output unchanged.
- Acceptance: T-22 parity exits 0 on all default datasets in both kernel modes; the suite is
  green in both kernel modes; PROF-IN for MT, D1 and D4 CSV recorded per T-19.
- Fixes: G1 escalation (§2.3).

**P1-18 — Enum rule (owner, 2026-10-01)**
- Depends: P1-17.
- Deliverables: `is_enum` requires repetition: distinct values <= 0.5 x non-null values and not unique, on top of the
  existing size limits; in the reference profiler (`profile/reference/column.py`, both paths), the kernel twin
  (`kernel/reference/exact.py`) and Rust (`exact.rs`), bounded mode, and the SQL Server plugin; docs.
- Acceptance: tests (a unique column, a tiny table, free text, a real low-cardinality category, the boundary);
  `profile_1to1/verify.py --impl shape` exits 0 in both kernels with `is_enum`/`enum_values` (and only fields derived
  from them) in a narrow named allow-list, failing if the fix is never exercised; G1 timings do not regress.
- Fixes: none (owner decision, §2.3).

**Gate G1**
- P1-14 is done (no RefEngine on the user-facing surface).
- P1-15, P1-16 and P1-17 are done (owner decision 2026-10-01).
- PROF-IN ≥10x on every workload.
- PROF-CLI ≥10x on D2 and D3.
- START ≤300 ms.
- T-22 parity passes on every dataset.
- Every phase-1 P-bug has a regression test (P19 belongs to phase 7).
- The profile modules are mypy strict.

G1 evidence (2026-10-01; machine: 4 cores, Intel Xeon 2.80 GHz, Linux 6.18, Python 3.11.15; the pinned
RefEngine venv of §1.2, pyarrow 25.0.1 in both venvs; Shape kernel built with `maturin develop --release`):
- Status: **not met** (PROF-IN D3 pq, D4 csv, MT; PROF-CLI D2). Escalated in §2.3.
- Met: P1-14 (`scripts/check_user_facing.py` clean; suite 1026 passed, `-m "not emulator and not live and
  not heavy" --ignore=tests/demo/fabric`); START 43 ms (gate 300); PROF-CLI D3 10.9x; PROF-IN on D1, D2,
  D3 csv and D4 pq; the five remaining profile modules are mypy strict (`mypy` clean on 157 files, ratchet
  list no longer names `shape.profile.*`); every phase-1 P-bug has a regression test
  (`tests/regressions/test_phase1_bugs.py`, P19 excluded); T-22 parity passes on every dataset.
- Commands: `source scripts/env.sh && python benchmarks/vs_refengine/profile_1to1/datasets.py`;
  `... verify.py --impl shape --refresh` then `... verify.py --impl shape` (exit 0, 49/49 PASS);
  `... bench.py --impl shape --out <json>` (in-process, equivalence verified first);
  `... bench_cli.py` (adapter-based equivalence per dataset, then START and PROF-CLI).
- Results: `docs/plans/evidence/G1/prof_in_run1.json`, `prof_cli_run1.json`, `verify_shape_run1.txt` (before
  any optimisation, 8 of 9 PROF-IN workloads missed, PROF-CLI D2 8.7x, D3 10.5x); `prof_in_run2.json`,
  `prof_cli_run2.json`, `verify_shape_run2.txt` (final). Run 2 medians (RefEngine s, Shape MT s, ratio):
  d1.csv 1.91/0.19 10.3x; d1.parquet 1.74/0.16 10.9x; d2.csv 34.80/2.84 12.3x; d2.parquet 30.53/2.45 12.5x;
  d3.csv 99.61/8.76 11.4x; d3.parquet 57.98/6.55 8.8x; d4.csv 37.61/3.81 9.9x; d4.parquet 32.92/3.06
  10.8x; mt 2.41/0.45 5.4x. `SHAPE_THREADS=1` ratios are in the same JSON (2x to 6x).
  PROF-CLI: d2.csv 36.19/3.69 9.8x; d3.csv 99.67/9.18 10.9x.
- Hygiene: load average 2.10 before run 2 started, and 1.0 to 1.3 between runs (about 1.0 of that is the
  previous benchmark process, as in `profile_1to1/README.md`); the harness's own load gate (1.5) held for
  every run. RefEngine and Shape runs are interleaved.

### Phase 2 — Plugin system

**P2-01 — API v1 Protocols**
- Depends: G1.
- Deliverables: `plugins/api/v1.py` with every Protocol in §4.3, and `SHAPE_API = "1.0"`.
- Acceptance: mypy strict; the generated docs page exists.
- Fixes: none.

**P2-02 — Plugin host**
- Depends: P2-01.
- Deliverables: discovery, version check, lazy loading, the registry, and isolation of
  load failures.
- Acceptance: a deliberately broken test plugin doesn't affect `shape profile`, and
  `shape plugins doctor` reports it.
- Fixes: none.

**P2-03 — Plugin CLI**
- Depends: P2-02.
- Deliverables: `shape plugins list|info|doctor`, and plugin-contributed subcommands.
- Acceptance: e2e test with the P2-06 example plugin, which may be built in the same
  commit.
- Fixes: none.

**P2-04 — Built-ins through the registry**
- Depends: P2-02.
- Deliverables: move built-in implementations into `src/shape/builtins/` (§4.3),
  registered through the entry points. Fold `connectors/` and `packs/address` in
  here.
- Acceptance: the import-linter contract (§4.3) passes, and `shape plugins list` shows
  every built-in.
- Fixes: G8.

**P2-05 — Delete the sandbox (D-09)**
- Depends: P2-02.
- Deliverables: remove `plugins/core.py` and `plugins/runtime.py`; write
  `docs/plugins/trust-model.md`.
- Acceptance: `grep -rniE "sandbox|isolat|allow_(network|filesystem|subprocess)" src/shape/plugins docs --exclude=COMPLETION_PLAN.md`
  returns only `docs/plugins/trust-model.md`, which states that plugins are trusted.
  The format "capabilities" negotiation in `spec/capabilities.py` is unrelated and
  stays.
- Fixes: PL1, PL2, PL3, PL4.

**P2-06 — Plugin kit and monorepo**
- Depends: P2-03.
- Deliverables:
  - `shape.plugins.kit`: a conformance test kit per Protocol.
  - `examples/plugin/`: a source, a detector and a command.
  - Skeletons under `plugins/` for every T-09 distribution, with packaging and CI.
  - The author guide.
- Acceptance: the example plugin, installed from outside the tree, passes the kit, and
  each skeleton builds a wheel.
- Fixes: none.

**Gate G2**
- An out-of-tree plugin adds a source, a detector and a command without any core
  change.
- `shape plugins list` shows every built-in.
- The G1 gates still pass.

### Phase F — Fabric and Azure pipeline integration

This phase productizes the §12 demo artifacts and extends them to Synapse and ADF. It
runs after G2 and before Phases 3 and 4, because pipeline integration is the owner's
primary use case (D-14).

**PF-01 — Cloud sources**
- Depends: G2.
- Deliverables: `abfss://` sources for OneLake and ADLS Gen2, and Delta table
  sources, as `shape.sources` built-ins behind the `[azure]` extra (`adlfs`,
  `azure-identity`, `deltalake`).
- Auth, in this order:
  1. an explicit token or credential;
  2. inside Fabric, `notebookutils.credentials.getToken("storage")`;
  3. `DefaultAzureCredential`, which covers managed identity, a service principal
     through environment variables, and the CLI.
- Acceptance:
  - Contract tests against recorded interactions.
  - Nightly e2e against Azurite (`ci/emulators`) for blob/DFS paths.
  - A Delta source test on local Delta tables written with `deltalake`.
  - Live tests when O-02 secrets exist.
- Fixes: none.

**PF-02 — Fabric notebooks, productized**
- Depends: PF-01, G1.
- Deliverables:
  - Promote the DM-05 notebooks into `integrations/fabric/notebooks/`, now running
    on the Rust-kernel wheel when available.
  - Add a **PySpark notebook** that profiles large tables in a distributed way:
    per-partition bounded-mode profiles through `mapInArrow`, merged on the driver
    (T-14/T-15 merge), with an `exact=True` driver-only option for tables that fit in
    driver memory.
- Acceptance:
  - A local Spark (pyspark in `[dev]`) test shows that the distributed bounded
    profile of D3 equals the single-process bounded profile within the T-14 bounds.
  - The DM-05 notebook tests still pass.
- Fixes: none.

**PF-03 — UDF package, productized**
- Depends: G1.
- Deliverables:
  - Promote DM-06 into `shape.integrations.fabric.udf` (importable helpers) plus the
    `function_app.py` template.
  - CI job `pure-wheel`: build the T-29 pure wheel, assert `py3-none-any` and a size
    under 28.6 MB, install it on Python 3.11 with only PyPI numpy, pyarrow and
    pandas, and run the UDF tests with `SHAPE_KERNEL=python`.
- Acceptance: the `pure-wheel` job is green on every PR from this point on.
- Fixes: none.

**PF-04 — Pipeline templates**
- Depends: PF-02, PF-03.
- Deliverables: in `integrations/`:
  - **Fabric:** the DM-07 pipelines, plus a **generation-then-profile** pipeline
    added after G4 by PF-06.
  - **Synapse:** a Synapse Spark notebook (the PF-02 PySpark variant, with Synapse
    auth) and a pipeline using the Notebook activity and an If Condition.
  - **ADF:** a pipeline running the Shape CLI in the PF-05 container through an
    Azure Batch Custom activity. The exit code fails the activity, and the summary
    JSON is written to ADLS and read back through a Lookup activity for branching.
- Acceptance: a JSON-schema-level test of each pipeline definition, and a runbook
  section per platform with a live dry-run checklist.
- Fixes: none.

**PF-05 — Container image**
- Depends: PF-01.
- Deliverables: `Dockerfile` (python:3.11-slim, the Shape wheel with `[azure]`,
  non-root user); the image is built and published to GHCR by CI on tags.
- Acceptance: CI builds the image; inside it, `docker run … shape profile` on D1
  mounted from the host exits 0; image size under 500 MB.
- Fixes: none.

**PF-06 — Generation in pipelines**
- Depends: G4, PF-04.
- Deliverables:
  - A Fabric notebook that generates a domain to lakehouse Delta tables.
  - A UDF `generateSample(domain: str, table: str, rows: int = 10000, seed: int = 42) -> pd.DataFrame`
    (rows are capped so the response stays under 30 MB).
  - A pipeline that generates, then profiles, then checks the output against the
    domain's contract.
  - The Synapse and ADF equivalents.
- Acceptance: local tests of each artifact, and generated output that passes T-21
  for retail at small scale.
- Fixes: none.

**Gate GF**
- PF-01 to PF-05 are done.
- The `pure-wheel` CI job is green.
- The owner's live dry run of the Fabric pipelines passes (runbook checklist).
- PF-06 is required only for G8.

### Phase 3 — Stream profiling

**P3-01 — Stream runtime**
- Depends: G2.
- Deliverables: micro-batches go through the engine in bounded mode; tumbling, sliding
  and session windows with `snapshot()` and `restore()`; watermarks and allowed
  lateness, per `docs/specs/STREAMING_SEMANTICS.md`.
- Acceptance: killing and restoring a window mid-stream gives output identical to an
  uninterrupted run; late-data tests pass per the spec.
- Fixes: S3.

**P3-02 — Keyed state**
- Depends: P3-01.
- Deliverables: bounded keyed state (per-key sketches, LRU and TTL, a hard memory
  cap); vectorized dedupe.
- Acceptance: across 10⁸ events and 10⁶ keys, RSS grows ≤10% after the first 10⁷
  events; dedupe matches a reference set implementation.
- Fixes: S2, S5.

**P3-03 — Checkpoints and offsets**
- Depends: P3-01.
- Deliverables: offset-committed checkpoints; reconnect resumes from the committed
  offset.
- Acceptance: across 100 forced reconnects, after dedupe on offset, the output equals
  an uninterrupted run.
- Fixes: S4.

**P3-04 — Stream-source plugins**
- Depends: P3-03, P2-06.
- Deliverables: consumers in `shape-kafka` and `shape-eventhubs`.
- Acceptance: contract tests on every PR, and emulator e2e nightly (T-26).
- Fixes: none.

**P3-05 — `shape stream-profile`**
- Depends: P3-04.
- Deliverables: the CLI command.
- Acceptance: e2e test; STREAM-PROF ≥80%.
- Fixes: none.

**Gate G3**
- STREAM-PROF ≥80%.
- A stream replay of D2 in bounded mode equals batch profiling of D2 in bounded mode,
  within the T-14 bounds.
- Results are identical across processes.
- The S-bugs have regression tests.

### Phase 4 — Generation engine

**P4-01a — Schema and RefEngine importer**
- Depends: G2, P0-07.
- Deliverables:
  - The Shape generation schema and its JSON Schema.
  - `generation/refengine_import.py`: imports the JSON produced by
    `benchmarks/vs_refengine/dump_schema.py` (RefEngine `RefEngineSchema`), covering model,
    tables, columns, generators, relationships, business rules, scale presets and
    modes (3nf and star).
  - Re-export to the same JSON.
- Acceptance: all 14 domain dumps import, and re-export to JSON that is equal (as
  dicts) to the dump.
- Fixes: none.

**P4-01b — `from-ddl`**
- Depends: P4-01a.
- Deliverables: `shape from-ddl FILE` with RefEngine's options `--output`, `--domain`,
  `--scale`/`-s`, `--smart/--no-smart` and `--explain`, porting RefEngine's DDL inference
  (`schema/` and the `from-ddl` path in `cli.py:751-833`).
- Acceptance: for each DDL fixture in RefEngine's tests (or, if there are none, 5
  fixtures written from RefEngine's docs), Shape's schema equals RefEngine's output.
- Fixes: none.

**P4-01c — `from-ddl` bug fixes (owner, 2026-10-01)**
- Depends: P4-01b.
- Deliverables: fix the baseline behaviours P4-01b reproduced (lane_status/P4-01b.md): a column-level
  `REFERENCES parent(col)` is read as a foreign key to that column; `VARBINARY(MAX)` (and other
  `(MAX)` binary types) is binary and left out like other binary columns; name matching uses whole
  words (`discount_pct` is a percentage, not a quantity; a `state` string column is a region, not a
  status enum); `gender CHAR(1)` gets a value set, not a pattern; CR-08 (parent total summed over
  children) is implemented.
- Acceptance: a test per fix asserting the correct behaviour; `ddl_1to1/verify.py` lists each as an
  intentional difference and every other field still equals the baseline; the `e2e_cli__inline`
  schema validates.
- Fixes: none (owner decision, §2.3).

**P4-02 — Engine**
- Depends: P4-01a.
- Deliverables: dependency resolution (RefEngine's Kahn order); row counts (presets,
  fixed, per_parent × ratio, per_year × years); column ordering; the compute phase; the
  business-rules engine (validate and fix); chunked random access (T-16); `--dry-run`.
- Acceptance:
  - **For retail:** row counts equal RefEngine's at small, medium, large and xlarge
    (checked with `dump_schema.py` plus RefEngine's `calculate_row_counts`).
  - Output is identical with chunk sizes of 1k, 64k and 1M.
- Fixes: G2.

**P4-03 — Rust generation kernel**
- Depends: P4-02, P1-01a.
- Deliverables: Philox4x64-10 (T-16), alias sampling, pool and string assembly
  (templates, case, joins), and temporal sampling (month, day-of-week, hour including
  bimodal), each with a reference twin.
- Acceptance:
  - Philox matches numpy's `Philox.random_raw()` known answers.
  - Differential tests pass.
  - Kernel microbenchmarks are recorded in `results.json`.
- Fixes: G1.

**P4-04 — Strategies.** These are RefEngine's 26 registered strategy names
(`engine/generator.py:316-341`). Each group is
its own work package. Every strategy gets a per-strategy statistical test against
RefEngine's strategy on the same config, under T-21 (b)–(e).
- **P4-04a** (Depends: P4-03): `sequence`, `uuid`, `weighted_enum`, `distribution`,
  `empirical`, `pattern`.
- **P4-04b** (Depends: P4-04a): `faker` and `native` (RefEngine's pools), `formula`,
  `computed`, `derived`.
- **P4-04c** (Depends: P4-04a): `conditional`, `correlated`, `lookup`,
  `reference_data`, `temporal`, `record_field`, `record_sample`.
- **P4-04d** (Depends: P4-04a): `foreign_key`, `composite_foreign_key`,
  `composite_fk_field`, `first_per_parent`, `self_referencing`, `self_ref_field`,
  `lifecycle`, `scd2`. The row-sequential ones run in Rust.
- Acceptance for P4-04d as a whole: G7 has its regression test.
- Fixes: G7 (in P4-04d).

**P4-05 — Distributions and calendars (D-05, D-11)**
- Depends: P4-03.
- Deliverables:
  - **Families:** RefEngine's (uniform, normal, log_normal, pareto, zipf, geometric,
    poisson, bernoulli), plus exponential, gamma, beta, weibull, triangular, negative
    binomial, power law with exponential cutoff, truncation of any family, mixtures,
    and empirical histograms.
  - **An 80/20 helper** for FK fan-out.
  - **Calendars:** the D-11 rule engine; US federal and US retail calendars; lift with
    ramp-up and decay; negative lifts; custom events; payday effects (1st/15th and
    biweekly); month-end and quarter-end effects; trend with step and ramp regime
    changes.
  - **Profiler detection** of month, day-of-week and hour profiles, holiday lift and
    tail index, in `profile/temporal.py`.
- Acceptance, using the parameter table in
  `tests/generation/test_distribution_recovery.py`. The table is fixed in the test
  itself:

  | Family | Parameters |
  |---|---|
  | normal | (10, 2) |
  | log_normal | (3, 0.5) |
  | pareto | (α=1.5, xm=1) |
  | zipf | (a=2) |
  | geometric | (p=0.2) |
  | poisson | (λ=4) |
  | bernoulli | (p=0.3) |
  | exponential | (λ=0.5) |
  | gamma | (k=2, θ=3) |
  | beta | (2, 5) |
  | weibull | (k=1.5, λ=2) |
  | triangular | (0, 3, 10) |
  | negative binomial | (r=5, p=0.4) |
  | power law with cutoff | (α=2, λ=0.01) |
  | mixture | 0.6·N(0,1) + 0.4·N(8,1) |

  - Fitting 10⁶ draws recovers every parameter within max(2%, 3 standard errors).
  - Empirical draws have TVD ≤0.01 against the source histogram.
  - The calendar test recovers the configured Black Friday, Cyber Monday and
    Christmas lifts within 5%.
  - The 80/20 helper's top-20% share is within 1% of the configuration.
  - Profile → generate → profile gives month, day-of-week and hour TVD ≤0.01.
- Fixes: none.

**P4-06 — Writers**
- Depends: P4-02.
- Deliverables:
  - **Core:** CSV, TSV, JSONL, SQL INSERT (`--sql-dialect tsql|tsql-fabric-warehouse|postgres|mysql`;
    options `--sql-ddl`, `--sql-drop`, `--sql-go` and
    `--schema-name`) and Parquet (T-17), plus `summary` output.
  - **Plugins:** Excel (`[excel]`, openpyxl) and Delta (`[delta]`, deltalake).
- Acceptance:
  - Golden output per format.
  - Shape's Parquet output reads back in pandas and in RefEngine's readers.
  - SQL output parses with `sqlglot` for each dialect (a dev dependency).
- Fixes: none.

**P4-07 — Retail through the engine**
- Depends: P4-04a, P4-04b, P4-04c, P4-04d, P4-06.
- Deliverables: retail as the first `shape.domains` entry, generated by the product
  engine (not by the reference port).
- Acceptance:
  - `domain_1to1/verify.py --domain retail --impl shape` passes T-21 at small, medium
    and large.
  - GEN-IN ≥10x on retail medium and large. GEN-CLI is checked in P4-10, because it
    needs the CLI.
- Fixes: none.

**P4-08 — Profile → generate, and `learn`**
- Depends: P4-05, P4-10, G1.
- Deliverables:
  - `shape generate --from X.shape`: fits strategies from the profile (marginals, a
    real Gaussian copula with marginal transforms, missingness, seasonality).
  - `shape plan` reports truthfully what will and won't be preserved.
  - A port of RefEngine's `SchemaBuilder`: `shape learn` (alias) writes a generation
    schema, and `--refengine-json` writes a `.refengine.json`.
- Acceptance:
  - Profile → generate → profile is within T-22 tolerances for the modelled fields.
  - `plan` flags every field that isn't modelled.
  - `shape learn --refengine-json` on D2 gives JSON equal to `refengine learn` on D2,
    except for fields listed, with a reason, in the test.
  - LEARN-CLI ≥10x.
- Fixes: G5, G6.

**P4-09 — Fidelity report**
- Depends: P4-07.
- Deliverables: `shape fidelity` (alias `compare`), with scoring equivalent to RefEngine's
  `FidelityComparator` (`inference/comparator.py:365`); JSON, MD and HTML output via
  `shape.reports`; thresholds; non-zero exit on failure.
- Acceptance:
  - For retail, per table, the score is within 0.5 points of RefEngine's comparator on
    the same pair of datasets.
  - A missing column scores 0, and an empty reference fails.
- Fixes: G3, G4.

**P4-10 — Generation CLI**
- Depends: P4-09.
- Deliverables:
  - `generate`, `describe`, `list`, `presets` (the command; preset content parity
    comes in P6-01e) and `from-ddl`, with `--mode 3nf|star`, `--scale`, `--seed`,
    `--format` (every P4-06 format) and `--dry-run`.
  - The Python API `shape.api.generate(domain, scale=..., seed=..., mode=...)`,
    returning Arrow tables. This is the equivalent of `RefEngine().generate(...)`.
  - Structured JSON logging and run metrics (a port of RefEngine's `observability.py`),
    available to every command.
  - `shape validate FILE` dispatches on content: a Shape or RefEngine generation schema
    goes through the schema validator (RefEngine `schema/validator.py`), and a contract
    goes through the existing contract validation. An unknown type exits 2.
- Acceptance:
  - e2e test per command, for retail. `composite` and the full preset checks come in
    P6-01e.
  - GEN-CLI ≥10x on retail medium and large.
- Fixes: none.

**P4-11 — Fidelity tiers and research modules**
- Depends: P4-09.
- Deliverables (the `[advanced]` extra adds scikit-learn, and `[ctgan]` adds sdv):
  - **Tier 1:** port `inference/advanced_profiler.py` (`AdvancedProfiler.profile_pair`
    and `profile_single`: GMM fits, conditional profiles, adversarial AUC, temporal
    profiles, periodicity) as `shape fidelity --tier 1`.
  - **Tier 2:** port `inference/tier2_profiler.py` (`run_tier2`: format preservation,
    string similarity, anomaly-rate checks) as `shape fidelity --tier 2`.
  - **Tier 3:** port `inference/tier3_research.py`:
    - `ChowLiuNetwork` → `shape fidelity --tier 3`;
    - `DriftMonitor` (PSI) → `shape drift --psi`;
    - `BootstrapMode` → the `bootstrap` generation strategy;
    - `DifferentialPrivacy` → `shape.privacy.dp` (D-07), with OS randomness unless a
      seed is passed;
    - `CTGANWrapper` → the optional `[ctgan]` plugin. Like RefEngine, it degrades
      gracefully when sdv is absent.
  - RefEngine's library names stay importable as aliases from `shape.fidelity`.
- Acceptance:
  - On retail medium (RefEngine seed 42 as real, Shape seed 1042 as synthetic), with
    scikit-learn installed in both venvs, every deterministic output field is equal
    under T-22 tolerances, and AUC and GMM fields are within 0.02 of RefEngine's.
  - DP: 1000 calls with no seed give distinct noise; the same explicit seed
    reproduces RefEngine's output for the same inputs.
  - `[ctgan]`: contract test with sdv mocked, and graceful degradation without it.
- Fixes: none.

**Gate G4**
- Retail passes T-21 at small, medium and large. xlarge is out of range: RefEngine large
  already peaks at 3.35 GB RSS.
- P4-11 acceptance passes.
- GEN-IN and GEN-CLI ≥10x on retail medium and large.
- LEARN-CLI ≥10x.
- Every strategy's test passes.
- The G-bugs have regression tests.

### Phase 5 — Streaming during generation

**P5-01 — Emitter runtime**
- Depends: G3, G4.
- Deliverables:
  - Rate modes: realtime, with `--rate` and burst specs `START:DURATION:MULT`; and
    `--no-realtime`, which is the default, as in RefEngine.
  - `--out-of-order` fraction, and `--anomaly-fraction` through `shape.chaos`. Note
    that RefEngine's CLI ignores `--anomaly-fraction` (its `cli.py` passes `None` when
    the value is non-zero); Shape must honor the flag.
  - `--max-events` and `--duration`.
  - D-12 formats, backpressure, at-least-once delivery with the D-12 idempotency key,
    and a checkpoint on shutdown.
- Acceptance:
  - At 10,000 events/s, the realtime rate stays within ±5% over 10 minutes (CI), and
    over 1 hour (nightly).
  - After kill -9 and restart, then dedupe on the key, the output equals an
    uninterrupted run.
- Fixes: none.

**P5-02 — Emitters**
- Depends: P5-01.
- Deliverables: console, file and JSONL (core); Kafka producer (`shape-kafka`); Event
  Hubs producer (`shape-eventhubs`); Fabric Eventstream and Eventhouse (`shape-fabric`).
- Acceptance: contract tests; emulator e2e nightly; live tests when secrets exist.
- Fixes: none.

**P5-03 — Live fidelity**
- Depends: P5-02, P3-05.
- Deliverables: a tee from the emitted stream into the stream profiler, compared
  against the target shape, with drift alerts.
- Acceptance: the live score is within 0.5 points of `shape fidelity` on the same
  events.
- Fixes: none.

**P5-04 — `shape stream`**
- Depends: P5-03.
- Deliverables:
  - Every option of RefEngine's `stream` (§10), plus Shape's sinks and formats.
  - `benchmarks/vs_refengine/stream_1to1/`: runs the STREAM-EMIT workload (§3.4) for
    `refengine` and `shape`, verifies the event multiset under T-21 (b)–(e) plus field
    names and order, and exits 1 on failure.
  - The workload wired into `run.py`.
- Acceptance: e2e test; the stream verifier exits 0; STREAM-EMIT ≥10x.
- Fixes: none.

**Gate G5**
- STREAM-EMIT ≥10x.
- The realtime rate holds within ±5% for 1 hour (nightly).
- Live fidelity matches offline fidelity.

### Phase 6 — RefEngine feature ports (plugins)

**P6-01 — `shape-domains`.** Carry the reference data over with D-10 notices. For each
domain, `domain_1to1/verify.py --domain D --impl shape` must pass T-21 at small and
medium, and GEN-IN must be ≥10x at medium.
- **P6-01a** (Depends: G4): capital_markets, education, financial.
- **P6-01b** (Depends: P6-01a): healthcare, hr, insurance.
- **P6-01c** (Depends: P6-01a): iot, manufacturing, marketing.
- **P6-01d** (Depends: P6-01a): pulse, real_estate, supply_chain, telecom.
- **P6-01e** (Depends: P6-01b, P6-01c, P6-01d):
  - Row counts equal RefEngine's for every domain at every scale.
  - The 6 presets and `composite` work, each with an e2e parity test.
  - The final retail move into the plugin.

**P6-02 — Chaos engine**
- Depends: G4.
- Deliverables: port RefEngine's `chaos/` (`engine.py`, `config.py`, `categories.py`) as
  `shape.chaos` built-ins, covering exactly its six categories: schema, value, file,
  referential, temporal and volume.
- Acceptance: per mutator, parity against RefEngine on mutation rates and types.
- Fixes: none.

**P6-03 — `shape mask`**
- Depends: G1, G4.
- Deliverables: port `inference/masker.py` as a `shape.transforms` built-in.
- Acceptance: parity against `refengine mask` on D2: the same masked columns, format
  preserved, and no original value remaining.
- Fixes: none.

**P6-04 — `shape-simulation`**
- Depends: G4, G5.
- Deliverables: a port of every module in RefEngine's `simulation/`: clickstream,
  file_drop, financial, hybrid, iot, operational_log, pulse, scd2_file_drops,
  state_machine and stream_emit.
- Acceptance: a parity test per simulator under T-21.
- Fixes: none.

**P6-05 — Incremental**
- Depends: G4.
- Deliverables: `shape continue` and `shape time-travel` (RefEngine's `incremental/`).
- Acceptance: parity tests for growth, churn, updates, deletes and seasonality.
- Fixes: none.

**P6-06 — Transforms**
- Depends: G4.
- Deliverables: `shape transform star|cdm` (aliases `to-star` and `to-cdm`).
- Acceptance: output schemas equal RefEngine's, and rows match on the same input.
- Fixes: none.

**P6-07 — `shape-fabric`.** Split into three work packages:
- **P6-07a — Writers and sources** (Depends: G4, G5):
  - Writers: Lakehouse files, Warehouse bulk (COPY INTO via `--staging-path`), SQL
    Database (`--connection-string`, `--write-mode`, `--batch-size`), Eventhouse and
    Eventstream.
  - Source: Lakehouse/Delta profiling.
  - OneLake paths.
  - Acceptance: contract tests against recorded interactions.
- **P6-07b — Auth** (Depends: P6-07a):
  - `--auth cli|msi|spn|sql|device-code|fabric`, the modes in RefEngine's
    `fabric/sql_database_writer.py` (~line 114).
  - Credential references `env://`, `kv://` and `file://` (RefEngine's
    `fabric/credentials.py` `CredentialResolver`).
  - Acceptance: contract tests.
- **P6-07c — Commands** (Depends: P6-07b):
  - `shape fabric publish|notebook|deploy-notebook|setup`, with top-level aliases
    `publish`, `notebook`, `deploy-notebook`, `setup-fabric` and `export-model`.
    `publish` writes a run manifest (`manifests/run_manifest.py`, as RefEngine's
    `cli.py` ~1993–2016 does).
  - `shape fabric export-model DOMAIN`: Power BI `.bim` semantic-model export
    (RefEngine `fabric/semantic_model_writer.py`), with every option of RefEngine's
    `export-model`: `-s`, `-o`, `--source-type lakehouse|warehouse|sql_database`,
    `--source-name`, `--include-measures/--no-measures` and `--schema-name`.
  - Acceptance: e2e tests for every command and alias, with recorded interactions;
    live when secrets exist.

**P6-08 — `shape-sqlserver`**
- Depends: G2.
- Deliverables: database profiling (RefEngine's `database_profiler`: schema walk,
  pyodbc, Entra auth, the same `sample_rows` default of 1000), plus SQL helpers shared
  with `shape-fabric`.
- Acceptance: nightly e2e against the SQL Server container, and T-22 parity with
  RefEngine's database profiler on the same database. Time is reported, not gated,
  because it is dominated by the server.
- Fixes: none.

**P6-08b — `shape-sqlserver` bug fixes (owner, 2026-10-01)**
- Depends: P6-08.
- Deliverables: fix the baseline behaviours P6-08 reproduced (lane_status/P6-08.md): sample-based
  `null_rate`, `cardinality_ratio` and `is_unique` divide by the sample size (so `is_unique` can be
  true on tables larger than the sample, and is reported as sample-based); a relationship the sampled
  data shows is reported when no FKs are declared; name-inferred FKs set the column's
  `is_foreign_key`.
- Acceptance: a test per fix (against the real-server harness and the stand-ins); the parity check
  lists each as an intentional difference and every other field still equals the baseline.
- Fixes: none (owner decision, §2.3).

**P6-09 — Validation gates, quarantine and `verify`**
- Depends: G1.
- Deliverables: RefEngine's `validation/` gates and quarantine, in core quality and
  contracts, plus `shape verify` (RefEngine's `verify/` runner: schema, nulls, PK, FK, KS
  and chi² checks, and its report).
- Acceptance: parity tests against `refengine verify` on retail output.
- Fixes: none.

**P6-10 — Profile registry parity**
- Depends: G1, G4.
- Deliverables:
  - `shape profile export|import|list|validate`.
  - `shape profile registry list|save|delete|tag|diff|reindex|validate`: RefEngine's
    profile registry (`profiles/`, `inference/profile_store.py`).
  - `shape registry` keeps its current meaning, the content-addressed `.shape`
    artifact registry. The two are different stores and are never merged.
- Acceptance: e2e test per subcommand.
- Fixes: none.

**P6-11 — JSON bridge**
- Depends: G4, G5, P6-12, P6-13. The `demo_*` and `scale_*` commands need both.
- Deliverables:
  - `shape bridge`: the JSON stdin/stdout protocol, in parity with RefEngine's
    `mcp_bridge.py`. Its 17 commands are `list`, `describe`, `generate`, `dry_run`,
    `validate`, `preview`, `profile_info`, `demo_list`, `demo_run`, `demo_status`,
    `demo_cleanup`, `scale_generate`, `stream`, `stream_status`, `stream_stop`,
    `scale_status` and `scale_cancel`.
  - The MCP server that exposes these commands as MCP tools is not in this repository: it moved
    to a separate private commercial component (2026-10-02, §2.3), which builds on `shape bridge`.
- Acceptance: bridge parity tests per command.
- Fixes: none.

**P6-12 — `shape demo`**
- Depends: P6-07c.
- Deliverables: `init`, `list`, `run`, `preflight`, `cleanup`, `status`, `notebook`
  and `report`.
- Acceptance: e2e with the local sinks.
- Fixes: none.

**P6-13 — Scale router, sinks and jobs**
- Depends: G4, G5, P6-07a.
- Deliverables: port RefEngine's scale machinery:
  - `engine/scale_router.py`, and the modes `local_single`, `local_mp` and
    `fabric_spark` as dispatched by `mcp_bridge.cmd_scale_generate` (~lines 351–516;
    `ScaleRouter` itself takes no mode parameter), exposed as
    `shape generate --scale-mode local_single|local_mp|fabric_spark`;
  - `spark_router.py`, plus the Spark worker notebook
    `notebooks/refengine_spark_worker.ipynb`;
  - `chunked_generator.py` and `chunk_worker.py`;
  - `sink_registry.py` and `sinks/`: memory, parquet, lakehouse, warehouse,
    sql_database and kql;
  - `async_job_store.py`, `job_tracker.py` and `stream_manager.py`;
  - `output/multi_store_writer.py`.

  Shape's `local_mp` uses the Rust kernel's threads by default. It keeps a
  multiprocess option only where RefEngine's semantics need it, such as per-chunk
  files.
- Acceptance:
  - e2e test of `shape generate --scale-mode` for each mode (`fabric_spark` through
    contract tests).
  - `local_single` and `local_mp` output pass T-21 against RefEngine's for retail at
    medium.
  - Job lifecycle tests: submit, status, cancel and resume.
  - Contract tests for `fabric_spark` and for the lakehouse, warehouse, sql_database
    and kql sinks; live runs when O-02 secrets exist.
- Fixes: none.

**P6-14 — Scenario packs and GSL**
- Depends: G4, P6-02, P4-10.
- Deliverables:
  - Port RefEngine's `packs/` (loader, runner, validator) and `specs/gsl_parser.py`
    (Generation Spec Language YAML).
  - Port the run manifest (`manifests/run_manifest.py`), written by pack runs.
  - Add `shape pack run|validate|list`.
  - RefEngine's built-in pack root (`packs/loader.py` ~224,
    `scenario_packs_extracted/packs`) does not exist at the pinned commit, so Shape
    ships no built-in packs until the owner supplies some.
- Acceptance:
  - **Reference inputs:**
    - the inline custom pack in RefEngine's `docs/tutorials/advanced/14-scenario-packs.md`;
    - the inline custom pack in the last code cell of
      `examples/notebooks/showcase/08_scenario_packs.ipynb`;
    - every GSL block in `docs/tutorials/advanced/15-gsl-specs.md`, with its
      `scenario.pack` rewritten to point at the tutorial's custom pack.

    Copy all of them into `tests/fixtures/packs/`.
  - Each input loads and validates in both tools, and runs at pack scale
    `fabric_demo`, seed 42, into `$BENCH_OUT_DIR`. For packs without chaos, the
    generated tables pass T-21 (a)–(f).
  - Shape's manifest contains every key present in
    `$REFENGINE_ROOT/pack_output*/*_manifest.json`.
  - e2e tests for `shape pack run|validate|list`.
- Fixes: none.

**Gate G6**
- Every row of §10 has a passing e2e test.
- Every domain passes T-21, with GEN-IN ≥10x at medium.
- Every work package named in `docs/plans/refengine_coverage.tsv` is `done`, and
  `check_coverage.py` exits 0.

### Phase 7 — Privacy and trust

**P7-01 — One taxonomy and safe profile**
- Depends: G1.
- Deliverables: merge `privacy/policy.py` and `classification.py`; add safe-profile
  export and validation in parity with RefEngine's `safe_profile*` and `safe_validator`
  (`shape profile validate --safe`).
- Acceptance: parity with RefEngine's safe-profile output on D2.
- Fixes: SEC3.

**P7-02 — k-anonymity and suppression**
- Depends: P7-01.
- Deliverables: enforced minimum cohort; suppression of small cells in value counts,
  enums and histograms.
- Acceptance: a property test shows no released cell below the minimum.
- Fixes: none.

**P7-03 — Signing**
- Depends: P1-09.
- Deliverables: Ed25519 via `[sign]`; `--sign` and `--verify`; key-handling docs.
- Acceptance: a forged artifact with rewritten hashes fails `--verify`.
- Fixes: P19.

**P7-04 — Security review**
- Depends: P7-03, G5.
- Deliverables: an updated threat model; the artifact fuzzer in nightly CI; `bandit -r src`.
- Acceptance: no findings of high severity.
- Fixes: none.

**Gate G7**
- SEC3 and P19 have regression tests.
- Safe-profile parity passes.
- No high-severity findings are open.

### Phase 8 — Migration and release

**P8-01 — Compatibility**
- Depends: G6.
- Deliverables:
  - Every alias in §10 not already delivered by the work package named in its row.
  - Reading RefEngine profile JSON and `.refengine.json`.
  - `docs/migration/from-refengine.md`.
- Acceptance: each command example in RefEngine's README, with `refengine` replaced by
  `shape`, runs and produces equivalent output.
- Fixes: none.

**P8-02 — Nightly parity suite**
- Depends: G6.
- Deliverables: nightly `run.py --full` over every domain and every profiling dataset,
  published to the docs performance page.
- Acceptance: 7 consecutive green nightly runs.
- Fixes: none.

**P8-03 — Docs site**
- Depends: G6.
- Deliverables: the T-24 site.
- Acceptance: `mkdocs build --strict` and a link check pass.
- Fixes: none.

**P8-04 — Release engineering**
- Depends: P8-03, G7, GF, PF-06.
- Deliverables: T-25 workflows; version 1.0.0 for core and every plugin; a release
  checklist.
- Acceptance: wheels, sdists, SBOM and attestations are built in CI for every T-04
  target. If O-01 is done, a TestPyPI install of `sqllocks-shape[all]` passes the
  smoke suite on each platform.
- Fixes: none.

**P8-05 — Final hostile review**
- Depends: P8-04.
- Deliverables: an independent review of `src/`, `rust/` and `plugins/`, with every
  finding fixed or recorded in §2.3 with owner sign-off.
- Acceptance: no open bugs in Appendix A; every gate G0–G7 is green on the release
  commit; mypy strict covers all of `src/shape`.
- Fixes: all.

**Gate G8 (complete)**
- Every item above holds, including the 7-night record from P8-02.
- **Publishing to PyPI is an owner action (O-01)** and is not part of build
  completion.

---

## 8. Keep / cut / delete lists

### 8.1 Modules to delete (P0-04), in this order

1. `integrations`
2. `etl`
3. `ci`, `distributed`
4. `admin`, `ai`, `marketplace`, `federation`, `enterprise`, `governance`, `graph`,
   `compiler`, `execution`, `reproducibility`
5. `explain.py`, `policy` (top level)
6. `history`, `lineage`, `observability`, `packages`, `reference`
7. `scenarios`, `temporal`, `testing`, `transform`
8. `hub`, `webapp`

That is 27 in total, which is the hub, the web app and 25 others (D-08).

**Tests:**
- **Delete these files:**
  - `tests/test_future_roadmap.py` (after P0-04's first step moves its privacy cases)
  - `tests/compiler/test_compiler.py`
  - `tests/etl/test_etl.py`
  - `tests/history/`
  - `tests/transform/test_transform.py`
  - `tests/platform12/test_integrations.py`
  - `tests/test_ci_gate.py`
  - `examples/ci_gate.py`
- **Edit these files** to remove the deleted-module cases:
  - `tests/platform12/test_features_01_04.py`, `test_features_05_08.py`,
    `test_features_09_12.py`
  - `tests/platform12/test_hardening.py`
  - `tests/torture/test_cross_feature_torture.py`
  - `tests/privacy/test_privacy.py`
  - `tests/torture/test_all_modules.py` (see P0-04)

### 8.2 Kept (rebuilt by the phases)

- `artifact`, `capture` (edge adapters), `profile`, `spec`, `model`, `types`, `errors`,
  `api`, `cli`, `diff`, `drift`, `quality`, `contracts`, `query`, `registry`,
  `relations`
- `streaming`, `connectors` (folded into `io`, `builtins` and plugins), `plugins`
  (rewritten)
- `generation`, `packs` (folded into `builtins` and `shape-domains`), `location`,
  `geospatial`
- `privacy`, `security`, `validation`

### 8.3 Files to delete (P0-05)

- **Root:** every `*_MANIFEST.json`, every `*_QUALIFICATION.json`,
  `CORE_REGRESSION_QUALIFICATION.json`, `FINAL_CREDENTIAL_PATTERN_SCAN.json`,
  `GA_1_3_*.json`, `GA_GENERATION_QUALIFICATION.json`, `GA_FINAL_TEST.txt`,
  `NAMING_MIGRATION.json`, `RC1_*.json`, `REPOSITORY_MANIFEST.json`.
- **Directories:** `rq/`, `docs/qualification/`, `docs/audit/`.
- **`docs/plans/`:** delete exactly these legacy files: `REMAINING_WORK.md`,
  `NEXT_WORK_PACKETS.md`, `M1-VALIDATION.txt`, `WP-0001-EVIDENCE.md`,
  `WP-0101-EVIDENCE.md`, `WP-0102-EVIDENCE.md`, `WP-0103-EVIDENCE.md` and
  `WP-0201.yaml`. **Keep** `COMPLETION_PLAN.md`, `refengine_coverage.tsv` (read by P0-07
  and G6) and `demo_status/`.
- **`docs/` root:** delete everything **except** this keep-list: `INSTALL.md`,
  `QUICKSTART.md`, `TUTORIAL.md`, `CONTRIBUTING.md`, `DETERMINISM.md`,
  `LOCATION_AS_CODE.md`, `PRIVACY_MODEL.md`, `PRODUCT_ARCHITECTURE.md`,
  `SECURITY_SPECIFICATION.md`, `THREAT_MODEL.md`, `SHAPE_MANIFESTO.md`, `BRANDING.md`,
  `API_STABILITY.md`, `RELEASE_POLICY.md`, `EXTERNAL_REFERENCE_ASSETS.md`. Each kept
  file must be edited to remove any statement the P0-05 grep flags, or any claim about
  DP, isolation or performance.
- **Workflows:** `.github/workflows/external-connectors.yml`, `ga.yml` and
  `release-ga.yml`. P0-06 and P0-07 replace them.
- **`docs/specs/`** is kept in full.

---

## 9. Owner actions (external)

| ID | Action | Needed by | Fallback until done |
|---|---|---|---|
| O-01 | PyPI trusted publishing for `sqllocks-shape`: on pypi.org (and optionally test.pypi.org), add a **pending publisher** with owner `sqllocks`, repository `shape`, workflow `publish.yml` and environment `pypi` (`testpypi` on TestPyPI); in GitHub, create the environments `pypi` and `testpypi` with the owner as required reviewer. Plugin projects are added the same way later. | DM-03b (early access); publishing after G8 | The demo uses the manual wheel upload (runbook) |
| O-02 | Fabric workspace and service principal as `FABRIC_*` secrets | live tests in P5-02 and P6-07 | Contract tests |
| O-03 | Azure Event Hubs namespace as `EVENTHUBS_*` secrets | live tests in P3-04 and P5-02 | Emulator |
| O-04 | Branch protection on `main` requiring CI | after P0-06 | CI only |
| O-05 | **Obsolete once O-08 is done** (public repos get 4-vCPU standard runners). If `sqllocks/shape` is private: a 4-vCPU larger runner (`ubuntu-latest-4-cores`) for benchmark jobs, since standard private runners have 2 vCPUs | P0-07 | Run gate benchmarks in a 4-core builder session and commit `results.json` with machine metadata |
| O-06 | After 1.0.0: a deprecation notice in RefEngine's README | after publishing | — |
| O-08 | After DM-00 merges: make `sqllocks/shape` public (Settings → General → Danger Zone → Change visibility). Then add yourself as a **required reviewer** on the `pypi` environment, which becomes available once the repo is public. | before DM-03b's real publish | Publishing without a reviewer is still limited to `main` and `v*` tags |
| O-07 | A Fabric workspace in a UDF-enabled region, with capacity, for the §12 live dry run and the talk: upload the wheel and data, and create the lakehouse, notebooks, UDF item and pipelines by following `integrations/fabric/RUNBOOK.md` | §12.7 check 4, GF | none (the live demo requires it) |
| O-09 | Confirm whether you hold RefEngine's copyright (`THIRD_PARTY_NOTICES.md` names "SQLLocks (Jonathan Stewart)"). If you do, the RefEngine attribution can be removed; if not, MIT requires it to stay wherever RefEngine-derived code ships. | P1-14 | The attribution stays |
| O-10 | W2-06: create the repository secret `FABRIC_SEMANTIC_MODEL` (a semantic model in the O-02 workspace; `FABRIC_WORKSPACE_ID` already exists), so the Fabric live job runs `plugins/shape-fabric/tests/test_live_semantic_model.py`. `sempy` signs in as the notebook user, so the run may need a Fabric notebook or an identity `sempy` accepts. | W2-06 live test | Contract tests against the strict fake (`fake_sempy`); the live test is not run |
| O-11 | W2-08: create the six sink secrets `SHAPE_TEST_SNOWFLAKE_URI`, `SNOWFLAKE_PASSWORD`, `SHAPE_TEST_DATABRICKS_URI`, `DATABRICKS_TOKEN`, `SHAPE_TEST_SYNAPSE_URI` and `SHAPE_TEST_SYNAPSE_STAGING` (Synapse signs in with the O-02 `FABRIC_*` service principal). The nightly sinks job runs each sink's live test only where its secrets exist. | W2-08 live tests | Contract tests with fakes; the live job prints "nothing to run" |
| O-12 | W7-01: in addition to the O-02 secrets (`FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID`, `FABRIC_CLIENT_SECRET`, `FABRIC_WORKSPACE_ID`), create `FABRIC_GIT_REMOTE` (the Git repository the O-02 workspace is connected to through Fabric Git integration; the live test needs no connection id, only these variables) and `FABRIC_GIT_TOKEN` (a token that can push to it), so the nightly `fabric-git-sync-live` job runs. | W7-01 item 1 (issue #139) | #139 stays open; the job prints "nothing to run" |
| O-13 | W3-12: confirm whether the ISO 4217 list (SIX Group) and the ISO 639-2 list (Library of Congress) may be redistributed inside the package. If yes, the reference-pack builders ship them unchanged; if not, they stay unshipped. | W3-12 `iso-4217` and `iso-639` packs | `iso-639-1` covers the `iso639_1` validator; no `iso-4217` or `iso-639` pack ships |

---

## 10. RefEngine CLI parity map

**Retired as a parity contract (owner decision 2026-09-30, §2.3).** The Shape commands in the
right-hand column are still delivered by the work packages named, under their Shape names only:
the `[aliases]` in brackets are dropped, and the `profile capture|diff` row is removed. Nothing
here may surface RefEngine names to users.

RefEngine 3.0.1 commands and their Shape equivalents. Every row needs an e2e test by G6.

| RefEngine | Shape [aliases] | WP |
|---|---|---|
| `generate` | `shape generate` (every `--format`: summary, csv, tsv, jsonl, parquet, excel, sql, delta; `sql-database` with `--auth`, `--connection-string`, `--write-mode`, `--batch-size`, `--staging-path` via `shape-fabric`) | P4-10, P4-06, P6-07a |
| `describe`, `list`, `validate` | `shape describe`, `shape list`, `shape validate` (dispatches on file content; see P4-10) | P4-10 |
| `presets`, `composite` | `shape presets`, `shape composite` | P4-10, P6-01e |
| `stream` | `shape stream` (`--table`, `--scale`, `--seed`, `--rate`, `--max-events`, `--duration`, `--out-of-order`, `--sink`, `--output`, `--mode`, `--realtime/--no-realtime`, `--burst`, `--anomaly-fraction`) | P5-04 |
| `to-star`, `to-cdm` | `shape transform star\|cdm` [`to-star`, `to-cdm`] | P6-06 |
| `learn` | `shape learn` (profile + SchemaBuilder) | P4-08 |
| `export-model` | `shape fabric export-model` [`export-model`] (Power BI `.bim`) | P6-07c |
| `from-ddl` | `shape from-ddl` (`--smart`, `--explain`, `-s`, `--domain`, `-o`) | P4-01b |
| `continue`, `time-travel` | `shape continue`, `shape time-travel` | P6-05 |
| `compare` | `shape fidelity` [`compare`] | P4-09 |
| `verify` | `shape verify` (gate runner) | P6-09 |
| `mask` | `shape mask` | P6-03 |
| `profile capture\|diff` | `shape profile capture`, `shape profile diff` (RefEngine `ExportedProfile` format) | P1-11 |
| `profile export\|import\|list\|validate` | `shape profile export\|import\|list\|validate` | P6-10, P7-01 |
| `profile registry list\|save\|delete\|tag\|diff\|reindex\|validate` | `shape profile registry …` (`shape registry` stays the `.shape` artifact registry) | P6-10 |
| `publish`, `notebook`, `deploy-notebook`, `setup-fabric` | `shape fabric publish\|notebook\|deploy-notebook\|setup` [originals] | P6-07c |
| `demo init\|list\|run\|preflight\|cleanup\|status\|notebook\|report` | `shape demo …` | P6-12 |
| `mcp_bridge` (JSON stdio, 17 commands) | `shape bridge` (the MCP server is a separate private commercial component) | P6-11 |
| *(library)* scale router: `local_single`, `local_mp`, `fabric_spark` | `shape generate --scale-mode …`, plus the bridge `scale_*` commands | P6-13 |
| *(library)* scenario packs and GSL | `shape pack run\|validate\|list` | P6-14 |
| *(library)* fidelity tiers 1–3 (`advanced_profiler`, `tier2_profiler`, `tier3_research`) | `shape fidelity --tier 1\|2\|3`, `shape drift --psi`, `shape.privacy.dp`, the `bootstrap` strategy, the `[ctgan]` plugin | P4-11 |
| *(new)* | `shape stream-profile`, `shape plugins`, `shape check`, `shape inspect`, `shape conformance`, `shape plan` | P1-11, P2-03, P3-05, P4-08 |

---

## 11. Status tracker

Work packages are listed in execution order. The next work package is the first `todo` row whose `Depends` are all `done` (§0.1). Update this table in the same commit as the work. Status values: `todo`, `wip`,
`done`, `blocked` (blocked means an escalation has been logged in §2.3).

| # | WP | Status | Commit |
|---|---|---|---|
| 1 | P0-00 | done | 0a63302 |
| 2 | P0-01 | done | d627836 |
| 3 | P0-02 | done | c0c830d |
| 4 | P0-03 | done | 519ab73 |
| 5 | P0-04 | done (lead completed the deletions; builder was blocked by its permission guard) | be1c145 |
| 6 | P0-05 | done (builder c8aba56; lead finished the sign markers) | 692798e |
| 7 | P0-06 | done | 87d0f4c |
| 8 | P0-07 | done | 79b4250 |
| 9 | P1-01a | done | 8661a10 |
| 10 | P1-01b | done (Wheels run 36734127356: all T-04 targets built and smoke-tested on 3.11 and 3.14) | ed7187a, d51ae78 |
| 11 | P1-02 | done | 3a3cc22 |
| 12 | P1-03 | done | 2f05b17 |
| 13 | P1-04 | done | 79cbe66 |
| 14 | P1-05 | done | d5ee068 |
| 15 | P1-06 | done | 842ec00 |
| 16 | P1-07 | done | f934e60 |
| 17 | P1-08 | done | 76cbdbc |
| 18 | P1-09 | done | 5f57982 |
| 19 | P1-10 | done | 9c797ea |
| 20 | P1-11 | done | e72d596 |
| 21 | P1-12 | done | 0087f7b |
| 21a | P1-14 | done | 9c0f75b |
| 21b | P1-15 | done | 04fe94c |
| 21c | P1-16 | done | 7238901 |
| 21d | P1-17 | done | 981bdd7 |
| 21e | P1-18 | done (279 baseline enums off, 430 kept; parity allow-list `is_enum`/`enum_values`) | d212635 |
| 22 | P2-01 | done | c3909e9 |
| 23 | P2-02 | done | 84e8efb |
| 24 | P2-03 | done | 687f568 |
| 25 | P2-04 | done | 0ca6387 |
| 26 | P2-05 | done | 4c2fa6b |
| 27 | P2-06 | done | 6adf820 |
| 28 | PF-01 | done (nightly Azurite e2e pending) | c8d6acb |
| 29 | PF-02 | done (early start before G1, owner-approved) | 9d0b2f1 |
| 30 | PF-03 | done | a726dd4 |
| 31 | PF-04 | done (fsspec test requirement fixed at integration, c033f48) | b98dbac |
| 32 | PF-05 | done (image 472.5 MB uncompressed; CI Container run 37233892200 on int/INT-18, 500 MB check passed) | 6d2e22a; integrated in INT-18 (2026-10-05, §2.3) |
| 33 | PF-06 | done | fefd854 |
| 34 | P3-01 | done | 37386f3 |
| 35 | P3-02 | done | 500367f |
| 36 | P3-03 | done | 43f5d88 |
| 37 | P3-04 | done | c0e7ace |
| 38 | P3-05 | done | b26e04e |
| 39 | P4-01a | done | 02a0c4d |
| 40 | P4-01b | done | 4b75d46 |
| 40a | P4-01c | done (rounds 1-2: F1-F8) | 14a8dd6 |
| 41 | P4-02 | done | 6917af7 |
| 42 | P4-03 | done | 6ec4c1f |
| 43 | P4-04a | done | e596a57 |
| 44 | P4-04b | done | b314b34 |
| 45 | P4-04c | done (case conditional/is_null_fixed changed after a chance failure; owner to rule) | 42f9732 |
| 46 | P4-04d | done (G7 regression test; scd2 fixture regenerated at integration for the shared calendar fingerprint) | 71b2ebd |
| 47 | P4-05 | done | 9f6fea8 |
| 48 | P4-06 | done (SQL comment/literal injection fixed at integration, 36f32d3) | ba051d2 |
| 49 | P4-07 | done (retail T-21 PASS small/medium/large; lead GEN-IN on 2.10 GHz: medium 11.8x, large 17.9x; evidence P4-07-lead/) | b1e6530 |
| 50 | P4-08 | done (lead LEARN-CLI 15.28x; G5, G6 fixed) | d237c55 |
| 51 | P4-09 | done (retail per table equal to the baseline comparator, max diff 0.00000; G3, G4 fixed) | 9fd7caa |
| 52 | P4-10 | done (two §6.5 rounds; lead GEN-CLI 2.10 GHz: medium 10.57x, large 31.64x; evidence P4-10-lead/) | 5fdf9b2 |
| 53 | P4-11 | done (retail medium tiers 1-3: 0 mismatches over 9 tables; AUC/GMM max diff 0.017 <= 0.02; DP distinct unseeded) | f85b99d |
| 54 | P5-01 | done | 33f57a7 |
| 55 | P5-02 | done | 2d0a1d3 |
| 56 | P5-03 | done | 19e8e6a |
| 57 | P5-04 | done (lead STREAM-EMIT in-process 14.27x on 2.10 GHz; stream verifier and negative control exit 0) | 08d3f7e |
| 58 | P6-01a | wip (merged into build/main-plan in INT-16, 2026-10-04; T-21 seed-1042 misses accepted as chance (owner); GEN-IN round 3 open in lane/P6-01-perf-domains and lane/P6-01-perf-engine) | |
| 59 | P6-01b | wip (merged into build/main-plan in INT-16, 2026-10-04; T-21 seed-1042 misses accepted as chance (owner); GEN-IN round 3 open in lane/P6-01-perf-domains and lane/P6-01-perf-engine) | |
| 60 | P6-01c | wip (merged into build/main-plan in INT-16, 2026-10-04; T-21 seed-1042 misses accepted as chance (owner); GEN-IN round 3 open in lane/P6-01-perf-domains and lane/P6-01-perf-engine) | |
| 61 | P6-01d | wip (merged into build/main-plan in INT-16, 2026-10-04; T-21 seed-1042 misses accepted as chance (owner); GEN-IN round 3 open in lane/P6-01-perf-domains and lane/P6-01-perf-engine) | |
| 62 | P6-01e | wip (merged into build/main-plan in INT-16, 2026-10-04, with the seed study lane/P6-01e-seed: the 18 composite cells' seed-1042 misses are chance, CMP-2 allow-list corrected; GEN-IN round 3 open) | |
| 63 | P6-02 | done | 0f8c36e |
| 64 | P6-03 | done | 50d7be4 |
| 65 | P6-04 | done | Lanes P6-04a and P6-04b, integrated in INT-12 (2026-10-03, §2.3). |
| 66 | P6-05 | done | 3a85d86 |
| 67 | P6-06 | done | e5d338f |
| 68 | P6-07a | done (contract tests on recorded interactions; 5 baseline defects fixed: destructive default mode, unescaped SQL/KQL identifiers, COPY INTO location, bulk-load cleanup and row counts; emulator/live tests nightly) | 98f6ac6 |
| 69 | P6-07b | done | Integrated in INT-13 (2026-10-03, §2.3). |
| 70 | P6-07c | done | Integrated in INT-14 (2026-10-03, §2.3). |
| 71 | P6-08 | done (nightly SQL Server e2e pending) | 29eac3e |
| 71a | P6-08b | done (rounds 1-2: FIX-1..FIX-9; real-server parity 11/11) | 515d26e |
| 72 | P6-09 | done (CI bench-quick verify_1to1 green on c7bd366, run 36850390984) | 3859a3c |
| 73 | P6-10 | done | 1faff65 |
| 74 | P6-11 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #56. |
| 75 | P6-12 | done | Integrated in INT-15 (2026-10-03, §2.3). |
| 76 | P6-13 | done (lead scale_1to1: local_single and local_mp pass T-21 at retail medium, negative controls flagged; baseline local_mp row-count defect fixed) | 693c897 |
| 77 | P6-14 | done | 62160c9 |
| 78 | P7-01 | done | b04bf32 |
| 79 | P7-02 | done | d6e3a97 |
| 80 | P7-03 | done | 06300e7 |
| 81 | P7-04 | done (threat model, nightly fuzzer, no high finding open; plugin security tests moved into the plugins' suites at integration) | d08fe3d |
| 82 | P8-01 | rejected | Superseded by D-13 (2026-10-03, §2.3). |
| 83 | P8-02 | todo | |
| 84 | P8-03 | done (site built with `mkdocs build --strict`; publishing it (Pages, deploy workflow) is not part of the package) | Integrated in INT-20 (2026-10-05, §2.3). |
| 85 | P8-04 | todo | |
| 86 | P8-05 | todo | |
| 87 | W1-01 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #55. |
| 88 | W1-02 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #57. |
| 89 | W1-03 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #58. |
| 90 | W1-04 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #59. |
| 91 | W1-05 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #60. |
| 92 | W1-06 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #64. |
| 93 | W1-07 | done | 9e87cd8, c6b5d17 |
| 94 | W2-01 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #61. |
| 95 | W2-02 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #52. |
| 96 | W2-03 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #53. |
| 97 | W5-01 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #62. |
| 98 | W5-02 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #63. |
| 99 | W1-08 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #66. |
| 100 | W1-09 | wip (merged into build/main-plan in INT-16, 2026-10-04; path, encoding and winutils fixes in; the Spark tests still fail on Windows in Nightly run 37202803596, issue #763) | |
| 101 | W1-10 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #88. |
| 102 | W1-11 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #89. |
| 103 | W1-12 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #90. |
| 104 | W1-13 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #230. |
| 105 | W1-14 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #91. |
| 106 | W1-15 | todo (held with W8-04, #768: re-applied in INT-20 and reverted before landing, because its cross-CPU proof failed (AVX-512 vs AVX2: 33 of 405 tests in each kernel); held for a lane that makes the distribution generators bit-identical across CPUs) | Issue #92. |
| 107 | W1-16 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #68. |
| 108 | W1-17 | wip (merged into build/main-plan in INT-18 through lane/W7-05, 2026-10-05; the lane's later commits are not integrated) | |
| 109 | W1-18 | done | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #94. |
| 110 | W2-04 | done | Integrated in INT-14 (2026-10-03, §2.3); issue #69. |
| 111 | W2-05 | todo | |
| 112 | W2-06 | wip (merged in INT-17; gap: a plain `shape profile semantic-model://...` of one table does not record `hidden: true` in the profile, `shape profile-model` does; W2-06.md) | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #95. |
| 113 | W2-07 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #231 (shard P1 failure #771 fixed in c1999097). |
| 114 | W2-08 | done (live Snowflake, Databricks and Synapse tests wait on the owner's secrets, §9) | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #96. |
| 115 | W2-09 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #97. |
| 116 | W2-10 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #98 (Windows DuckDB URI test: #769, BUGS-win). |
| 117 | W3-01 | done | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #99. |
| 118 | W3-02 | done | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #100. |
| 119 | W3-03 | done (the package is `shape.versions` since INT-18, P0-04's cut list) | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #101. |
| 120 | W3-04 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #70. |
| 121 | W3-05 | done | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #102. |
| 122 | W3-06 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #71. |
| 123 | W3-07 | done (univariate depth opt-in, `--univariate`, for T-19) | Integrated in INT-18 (2026-10-05, §2.3); issue #103. |
| 124 | W3-08 | done (multivariate entries opt-in, `--multivariate`, for T-19) | Integrated in INT-18 (2026-10-05, §2.3); issue #232. |
| 125 | W3-09 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #72. |
| 126 | W3-10 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #73. |
| 127 | W3-11 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #104. |
| 128 | W3-12 | done (iso-4217 and iso-639 packs not shipped until the owner confirms their licences) | Integrated in INT-18 (2026-10-05, §2.3); issue #105. |
| 129 | W3-13 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #106. |
| 130 | W4-01 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #79. |
| 131 | W4-02 | rejected | 2026-10-03, §2.3. |
| 132 | W4-03 | done | Integrated in INT-16 (2026-10-04, §2.3); issue #74. |
| 133 | W5-03 | done | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #233. |
| 134 | W5-04 | done | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #80. |
| 135 | W5-05 | done (real-server seeding confirmed by the databases-e2e job of Nightly run 37251176423) | Integrated in INT-18 (2026-10-05, §2.3); issue #81. |
| 136 | W5-06 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #82. |
| 137 | W5-07 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #83. |
| 138 | W5-08 | wip (integrated in INT-19, landed with INT-20; the Anonymeter integration's `numpy<1.27` pin conflicts with T-07's numpy>=2.0 floor, open for the lead) | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #84. |
| 139 | W5-09 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #85. |
| 140 | W5-10 | done | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #86. |
| 141 | W6-01 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #234. |
| 142 | W6-02 | done | Integrated in INT-15 (2026-10-03, §2.3); issue #75. |
| 143 | W6-03 | done | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #235. |
| 144 | W6-04 | done | Integrated in INT-17, landed with INT-18 (2026-10-05, §2.3); issue #87. |
| 145 | W7-02 | wip (integrated in INT-18 (2026-10-05, §2.3); one part of item 5 blocked, W7-02.md) | Issue #141. |
| 146 | W7-04 | done | Integrated in INT-19, landed with INT-20 (2026-10-05, §2.3); issue #142. |
| 147 | W7-05 | wip (integrated in INT-18 (2026-10-05, §2.3); lane/W7-05b (bridge `suite_list` and `suite_run`) merged in INT-20, but no lane status file records it complete) | Issue #227. |
| 148 | W7-06 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #143. |
| 149 | W7-07 | done | Integrated in INT-18 (2026-10-05, §2.3); issue #145. |
| 150 | W7-01 | wip (integrated in INT-20; item 1, the live Git sync test, is built but has not run live: it needs the O-02 and O-12 secrets) | Integrated in INT-20 (2026-10-05, §2.3); issue #139. |
| 151 | W7-03 | done (mixtures and seasonality opt-in with the univariate depth, `--univariate`, for T-19) | Integrated in INT-20 (2026-10-05, §2.3); issue #226. |
| 152 | W8-01 | wip (merged in INT-20; no lane status file records it complete) | Integrated in INT-20 (2026-10-05, §2.3); issue #564. |
| 153 | W8-02 | done | Integrated in INT-20 (2026-10-05, §2.3); issue #565. |
| 154 | W8-03 | done | Integrated in INT-20 (2026-10-05, §2.3); issue #566. |
| 155 | W8-04 | todo (held with W1-15, #768: merged in INT-20 and reverted before landing; its byte-identical corpus failed the cross-CPU proof) | Issue #567. |
| 156 | W8-05 | todo (lane blocked, nothing built: it needs W5-03's vault and W1-11's safe capture in its base) | Issue #568. |
| 157 | W8-06 | done (reserved identifiers stay the default) | Integrated in INT-20 (2026-10-05, §2.3); issue #766. |
| 158 | W8-07 | wip | Lane lane/W8-07; issue #1. |
| 159 | W9-01 | todo | Issue #2 (platform coverage, 2026-10-08). |
| 160 | W9-02 | todo | Issue #3 (platform coverage, 2026-10-08). |
| 161 | W9-03 | todo | Issue #4 (platform coverage, 2026-10-08). |
| 162 | W9-04 | todo | Issue #5 (platform coverage, 2026-10-08). |
| 163 | W9-05 | todo | Issue #6 (platform coverage, 2026-10-08). |
| 164 | W9-06 | todo | Issue #7 (platform coverage, 2026-10-08). |
| 165 | W9-07 | todo | Issue #8 (platform coverage, 2026-10-08). |
| 166 | W9-08 | todo | Issue #9 (platform coverage, 2026-10-08). |
| 167 | W9-09 | todo | Issue #10 (platform coverage, 2026-10-08). |
| 168 | W9-10 | todo | Issue #11 (platform coverage, 2026-10-08). |
| 169 | W9-11 | todo | Issue #12 (platform coverage, 2026-10-08). |
| 170 | W9-12 | todo | Issue #13 (platform coverage, 2026-10-08). |

| Gate | Status |
|---|---|
| G0 | done b965672 |
| G1 | done (lead, 4:34 PM EDT Oct 1, on build/main-plan 2a5f94a + lane/G1-d1, 4 vCPU Xeon 2.10 GHz: 34 checks exit 0, equivalence 49/49 both kernels first; PROF-IN d1 17.6x/15.4x, d2 18.8x/21.2x, d3 20.2x/16.6x, d4 13.5x/15.6x, mt 15.2x; PROF-CLI d2 13.8x, d3 17.0x; START 44 ms; P-bug regressions and mypy strict in the suites; P1-14..P1-18 done; evidence docs/plans/evidence/G1-lead2/ (lanes: G1-mt, G1-d1)) |
| G2 | done (lead, Oct 1: out-of-tree example plugin adds a source, a detector and a command with no core change, tests/plugins/test_plugin_kit_install.py 7 passed; `shape plugins list` shows all 72 built-ins; G1 gates pass, see G1) |
| GF | todo |
| G3 | done (lead, 8:55 AM EDT Oct 1, on 7ef6a6e: stream_prof verify stream == batch bounded rel 1e-9 PASS and identical across 3 processes PASS; STREAM-PROF 115.7% default / 101.7% SHAPE_THREADS=1 (gate 80%), docs/plans/evidence/G3/stream-prof-lead.json; S2-S5 regression tests pass in both kernels; P3-01..P3-05 done) |
| G4 | done (lead, 1:00 AM EDT Oct 2, 4 vCPU Xeon 2.10 GHz, equivalence first: retail T-21 PASS at small, medium and large (60/60 columns); P4-11 acceptance passed (0 mismatches); GEN-IN medium 14.18x, large 31.09x; GEN-CLI medium 10.57x, large 31.64x; LEARN-CLI 15.28x; tests/generation + tests/regressions 1643 passed (every strategy); G-bugs G1-G8 each have a regression test; evidence docs/plans/evidence/G4/, P4-10-lead/) |
| G5 | todo |
| G6 | todo |
| G7 | todo |
| G8 | todo |

---

## 12. Fabric demo track (48 hours)

**Purpose.** A live talk showing Shape profiling data **inside Microsoft Fabric**:
- in a Python notebook;
- in a Fabric User Data Function (UDF);
- as a quality gate in a Fabric pipeline.

RefEngine is retired. The talk shows Shape replacing it.

**Relation to the main plan.**
- This track runs **before** Phase 0, on its own branches.
- It builds the pure-Python profiler that T-03 requires as the reference twin, so
  the work is not thrown away. Phase 1 later adds the Rust kernel underneath the same
  API.
- Nothing here changes a decision in §2. Where the demo takes a shortcut, this
  section says so explicitly.

**Execution.**
- The track has three lanes, each designed for its own session.
- Lanes touch **disjoint paths** and meet only through the fixed API contract in
  §12.2, so they can run in parallel.
- A lane session follows §0 and §6 like any builder session, but picks its work from
  §12.6 instead of §11.

### 12.1 Verified platform constraints (Microsoft Learn, retrieved 2026-09-30)

| Surface | Constraint | Consequence for Shape |
|---|---|---|
| Python notebook | Kernels 3.10, 3.11 and 3.12 (default 3.12); 2 vCores / 16 GB by default, raised with `%%configure {"vCores": N}`; `deltalake` (delta-rs) and `duckdb` preinstalled; custom `.whl` installed with `%pip` from the notebook's built-in resources folder; Environment items not supported | Select kernel 3.11 or 3.12, and read Delta through `deltalake`. The default 2 vCores give less than the 4-core speedups in §3.3, so the demo runbook sets `vCores: 8`. |
| Notebook → pipeline | `notebookutils.notebook.exit(str)` returns the exit value to the pipeline Notebook activity. It must **not** be called inside `try/except`. | The notebook builds its result, then calls `exit` at top level. |
| User Data Functions | Python 3.11 at run time (3.12 when testing); 240 s execution limit (100 s through the public endpoint); 4 MB request; 30 MB response; **private libraries must be platform-independent `.whl` files under 28.6 MB**; public PyPI libraries allowed; parameter names must be camelCase; types are `str`, `int`, `float`, `bool`, `datetime`, `list`, `dict` and pandas `DataFrame`/`Series` (SDK ≥ 1.0.0); Lakehouse access through `@udf.connection` plus `fn.FabricLakehouseClient` (`connectToFiles`, `connectToSql`); no service principal or managed identity for those connections; `fn.UserThrownError` for handled errors | Shape must ship a **pure-Python wheel** (T-29). The UDF's numpy and pyarrow come from PyPI. UDF work is sized to finish well inside 240 s. |
| Environment item | Attaches libraries to **Spark notebooks and Spark job definitions only** (not Python notebooks). Custom `.whl` upload has no platform-independence rule; Spark runs on Mariner Linux. Publish modes: **Full** takes 3–6 minutes to publish plus 1–3 minutes at session start, for pipelines and jobs; **Quick** takes about 5 seconds and installs at session start, for notebooks only. Outbound access protection blocks PyPI, so dependencies must then be uploaded as custom wheels. | The Spark path installs Shape once per environment. The demo uses Quick mode and the pipelines use Full mode. |
| Spark runtimes | **1.3:** Spark 3.5.5, Python 3.11, end of support announced, still the default for new workspaces. **2.0:** Spark 4.1, Python 3.13, generally available. Spark 4 has `DataFrame.toArrow()`. | Target Runtime 2.0 in the demo Environment. On 1.3, fall back to `mapInArrow` or `toPandas()`. Shape supports both (T-06). |
| Pipelines | The **Functions activity** invokes UDFs with static or dynamic parameters; the Notebook activity runs notebooks | Two gate patterns: notebook-based and UDF-based. |

Sources:
- `learn.microsoft.com/fabric/data-engineering/user-data-functions/user-data-functions-service-limits`
- `…/how-to-manage-libraries`
- `…/python-programming-model`
- `learn.microsoft.com/fabric/data-factory/functions-activity`
- `learn.microsoft.com/fabric/data-engineering/using-python-experience-on-notebook`
- `learn.microsoft.com/fabric/data-engineering/environment-manage-library`
- `learn.microsoft.com/fabric/data-engineering/runtime`

If a lane finds a constraint that differs from this table, it fixes the table in a
`plan-fix:` commit with the evidence (§0.3).

### 12.2 Fixed API contract (all lanes build against this)

```python
import shape

p = shape.profile(source, *, name=None)
# source: str | Path (a .csv, .parquet or .jsonl file; a Delta table directory; a glob;
#         or a directory of files), pyarrow.Table, pandas.DataFrame, or
#         dict[str, <any of those>] for multi-table (FK detection).
p.to_dict()    # the full profile as JSON-ready dicts (T-22 parity is checked by the internal harness)
p.summary()    # small, JSON-safe dict (< 1 MB for 500 columns): name, row_count, and
               # per column: dtype, null_rate, cardinality, is_unique, is_primary_key,
               # is_foreign_key, fk_ref_table, distribution, pattern, min, max, mean, std
p.to_html()    # self-contained HTML report (no external assets)

shape.save(p, path)      # writes a .shape artifact
shape.load(path)         # -> Profile

r = shape.check(p, contract)   # contract: dict or path to JSON (format in §12.3)
r.passed                       # bool
r.violations                   # list[dict]: {column, rule, expected, observed}
r.to_dict()

d = shape.diff(baseline, current, *, thresholds=None)   # both are Profiles
d.drifted                      # bool
d.changes                      # list[dict]: {column, kind, baseline, current, severity}
d.to_dict()
```

**CLI.** All commands exit 0 on success, 1 on a failed check or drift (only when
`--fail-on-drift` is given), and 2 on a usage or input error.

```
shape profile SRC -o OUT.shape [--html REPORT.html] [--json SUMMARY.json]
shape check PROFILE.shape CONTRACT.json [--json RESULT.json]
shape diff BASE.shape CURRENT.shape [--json RESULT.json] [--fail-on-drift]
```

**Rules for the other main-plan work packages:**
- This contract is final for 1.0.
- P1-10 must keep the §12.3 contract format working.
- P1-11 must keep these CLI forms.

### 12.3 Contract format (v1)

```json
{
  "row_count": {"min": 1000, "max": 5000000},
  "columns": {
    "customer_id": {"dtype": "integer", "nullable": false, "unique": true},
    "email":       {"max_null_rate": 0.05, "pattern": "email"},
    "status":      {"allowed_values": ["placed", "shipped", "returned"]},
    "amount":      {"min": 0, "max": 100000, "distribution": "log_normal"}
  },
  "required_columns": ["customer_id", "order_date"],
  "allow_extra_columns": true
}
```

- Every rule is optional.
- `dtype`, `pattern` and `distribution` use RefEngine's vocabulary, as produced by the
  profiler.
- `unique` uses the exact distinct count (T-15).
- `allowed_values` fails if the profile shows any value outside the set: the profile's
  value counts must be a subset.

**Diff defaults** (all overridable through `thresholds`):

| Kind | Default threshold | Severity |
|---|---|---|
| Added or removed column | any | high |
| dtype change | any | high |
| Null-rate change | > 0.05 absolute | medium |
| Cardinality change | ratio > 1.5 or < 0.67 | medium |
| Mean shift | > 0.5 × baseline std | medium |
| Distribution family change | any | low |
| New categorical values | any | low |

### 12.4 Decisions for this track

| ID | Decision |
|---|---|
| DM-1 | The demo runs on the **pure-Python profiler**, no Rust. It is the T-03 reference implementation, ported from `benchmarks/profile_1to1/port.py`, which is bitwise-verified against RefEngine on 30 datasets. |
| DM-2 | **Parity first, speed claims only from committed evidence.** The talk may quote only numbers in `benchmarks/baselines/2026-09-29/` or `results.json`, and must say which machine and core count they were measured on. Live Fabric timings are shown as measured and are not extrapolated. |
| DM-3 | **Where the demo data comes from:** generated locally with the committed retail reference port (`benchmarks/retail_1to1/port.py`, 60/60 equivalent) plus the profiling datasets, written as Parquet, uploaded to the lakehouse by the owner, and turned into Delta tables by a setup notebook. A second "day 2" copy has deliberate, documented drift, so the gate visibly fails. |
| DM-4 | **Owner-operated live steps.** Builders cannot reach the owner's Fabric workspace. Lanes deliver importable artifacts plus local tests; the owner runs the live dry run from `integrations/fabric/RUNBOOK.md`. |
| DM-5 | **The UDF only runs work that fits comfortably in 240 s.** It profiles lakehouse **files** up to a size cap (default 50 MB, a parameter). It rejects larger inputs with `fn.UserThrownError`, recommending the notebook path, and it checks and diffs saved `.shape` files, which is cheap. Heavy profiling belongs in notebooks. |
| DM-6 | **Lakehouse tables in UDFs:** read through `connectToSql` with a `maxRows` cap (default 1,000,000) and the same size guard. The result carries `sampled: true` whenever the cap was hit. |

### 12.5 Paths owned by each lane (never edit another lane's paths)

| Lane | Owns |
|---|---|
| **L1 Core** | `src/shape/profile/reference/` (new), `benchmarks/profile_1to1/verify.py` (the `--impl shape` option only), `src/shape/api.py`, `src/shape/report/` (new), `src/shape/contracts/v1.py` (new), `src/shape/cli/main.py` (the three commands only), `tests/demo/core/`, `scripts/build_pure_wheel.py`, `.github/workflows/publish.yml` (new), `pyproject.toml` (the version and `[project.optional-dependencies]` only) |
| **L2 Fabric** | `integrations/fabric/` (new): notebooks, UDF, pipeline definitions, runbook; `tests/demo/fabric/` |
| **L3 Demo content** | `demo/` (new): data generation scripts, drift injection, contracts, talk notes, benchmark sheet; `tests/demo/content/`. For DM-00 only: `README.md`, `CHANGELOG.md`, `SECURITY.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, the `license`/`license-files` lines of `pyproject.toml`, `tests/torture/test_all_modules.py`, and the files §8.3 deletes |

**Merge order into the integration branch:** L3's DM-00 first, then L1, then the rest of L3, then L2.
- L2 and L3 may build against a local stub of the §12.2 API until L1 lands. A stub
  may never be committed outside `tests/`.
- After merging, the lead session (or the owner) runs `pytest tests/demo` and the
  §12.7 exit checks.

### 12.6 Demo work packages

**DM-00 — Public-readiness cleanup (L3; do this first, because the repo goes public, D-15)**
- Depends: none.
- Deliverables:
  - Perform the **deletions** listed in §8.3: the root evidence files, the `docs/`
    milestone files outside the keep-list, `docs/qualification/`, `docs/audit/`,
    `rq/`, and the workflows `external-connectors.yml`, `ga.yml` and
    `release-ga.yml`.
  - Rewrite `README.md` and `CHANGELOG.md` to the true state: "Early access:
    profiling is available now; data generation and pipeline integration are in
    progress; see `docs/plans/COMPLETION_PLAN.md`." No GA, certified,
    production-ready or isolation claims.
  - Public-facing text (the README, the changelog, docs outside `docs/plans/`) must
    **not** describe Shape as a rebuild or successor of RefEngine, and must not mention
    RefEngine's retirement. Mentioning RefEngine is allowed only in the migration guide
    (P8-01) and in benchmark comparisons.
  - Add `SECURITY.md` contact details if missing.
  - **License (D-15):**
    - Replace `LICENSE` with the standard MIT text, with the copyright line
      `Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart)` (the same holder line as
      RefEngine's `LICENSE`).
    - In `pyproject.toml`, change only the license field, to
      `license = "MIT"` (SPDX) with `license-files = ["LICENSE", "THIRD_PARTY_NOTICES.md"]`.
    - Update any README license badge or text.
    - Keep `THIRD_PARTY_NOTICES.md` and add the GeoNames CC-BY-4.0 attribution to it
      now (D-10).
    - Acceptance addition: `grep -rn "Apache" --include=*.md --include=*.toml --include=LICENSE . --exclude-dir=docs/plans --exclude-dir=.git --exclude=THIRD_PARTY_NOTICES.md`
      returns nothing.
  - Do **not** touch `src/`, `tests/` or `pyproject.toml`: P0-04 and P0-05 still own
    the module cuts and dependency changes.
  - Tests that read deleted files break when those files go. Only
    `tests/torture/test_all_modules.py` reads `rq/torture_inventory.json`; rewrite it
    as P0-04 specifies (`pkgutil.walk_packages`), because that is the only test file
    DM-00 may edit.
- Acceptance:
  - No path in §8.3 exists.
  - The P0-05 claims grep returns nothing.
  - `pytest` is green.
  - The owner can flip the repo to public (O-08).
- Fixes: X3.

**DM-01 — Pure-Python profiler (L1)**
- Depends: none.
- Deliverables:
  - Port `benchmarks/profile_1to1/port.py` into `src/shape/profile/reference/` as
    product code: typed, documented, no RefEngine imports, no machine paths.
  - Implement `shape.profile` and `Profile.to_dict()`/`summary()` per §12.2, for
    every listed source type. Delta tables use `deltalake` when it's installed;
    without it, the function raises a clear `ImportError` naming the package.
  - Multi-table input runs FK detection.
- Acceptance:
  - Add `--impl shape` to `benchmarks/profile_1to1/verify.py` (P0-07's flag, done
    early). It runs in the RefEngine venv with Shape installed by
    `"$REFENGINE_PY" -m pip install --no-deps -e .`; DM-03 widens the pyarrow pin so
    the two coexist.
  - `verify.py --impl shape` exits 0 on D1–D4, MT and every EDGE variant (T-22),
    comparing `shape.profile(...).to_dict()` with RefEngine.
  - Unit tests cover each source type, including a Delta table written with
    `deltalake`.
  - `summary()` is under 1 MB on D4.
- Fixes: P1, P2, P3, P4 (the new path; the old `capture_rows` path is left alone
  until P1-12).

**DM-02 — Artifact, contract, diff and report (L1)**
- Depends: DM-01.
- Deliverables:
  - `shape.save`/`shape.load` for the new Profile, with NaN and ±inf encoded
    explicitly (P8).
  - `shape.check` with the §12.3 contract format.
  - `shape.diff` with the §12.3 defaults.
  - `Profile.to_html()`: self-contained, showing the per-column table, distributions,
    patterns and keys, with no network assets.
  - The three CLI commands with the §12.2 exit codes.
- Acceptance:
  - Tests for every contract rule and diff kind, with pass and fail cases.
  - Round-trip `save`/`load` equality, including NaN.
  - CLI e2e tests assert the exit codes 0, 1 and 2.
  - The HTML file opens with no network access (the test asserts no `http` URLs in
    `src`/`href` attributes).
- Fixes: P8.

**DM-03 — Pure-Python wheel (L1)**
- Depends: DM-02.
- Deliverables:
  - `scripts/build_pure_wheel.py` builds `sqllocks_shape-<version>-py3-none-any.whl`
    containing no compiled code.
  - `import shape` must not require `cryptography`: make `shape.security` import
    `crypto` lazily (this is P0-05's lazy-import item, done early).
  - The wheel declares `numpy>=2.0,<3` and `pyarrow>=14.0.1`, the same range as T-07,
    so that Fabric's preinstalled pyarrow and the UDF SDK's `pyarrow<20` pin are
    accepted.
  - Version `0.9.0.dev1`.
- Acceptance:
  - The wheel is under 28.6 MB and its tag is `py3-none-any` (checked by the
    script).
  - In a fresh Python 3.11 venv with only numpy, pyarrow, pandas and deltalake, the
    following succeed: `pip install <wheel>`, `python -c "import shape"`, and the
    `tests/demo/core` suite run against the installed wheel.
- Fixes: none.

**DM-03b — PyPI early-access release (L1)**
- Depends: DM-03, and §12.7 checks 1–3 passing on the merged branch.
- Deliverables: `.github/workflows/publish.yml`. **The filename and environment name
  must match exactly** what the owner registered as a PyPI trusted publisher (O-01).
  It must:
  - trigger on `workflow_dispatch` with an input `repository: testpypi|pypi`, and on
    tags `v*`;
  - run in the GitHub environment `pypi` (or `testpypi` for TestPyPI), with
    `permissions: {id-token: write, contents: read}`;
  - build the DM-03 pure wheel and an sdist;
  - run `twine check`, and the `tests/demo/core` suite against the built wheel on
    Python 3.11;
  - publish with `pypa/gh-action-pypi-publish@release/v1`, with no API tokens and no
    passwords.
- Version `0.9.0`, **not a pre-release tag** (Fabric library pickers may hide
  pre-releases). The README states: early access, profiling only.
- Acceptance:
  - A dry run against TestPyPI succeeds when O-01 is done for TestPyPI.
  - `pip install sqllocks-shape==0.9.0` works in a clean Python 3.11 venv after the
    real publish, which the owner triggers.
  - If O-01 isn't done, the workflow is committed, and the runbook's manual
    wheel-upload path remains the demo's route.
- Fixes: none.

**DM-04 — Demo data and drift (L3)**
- Depends: none. It uses the committed reference ports only.
- Deliverables: `demo/make_data.py`, which needs the §1.2 RefEngine checkout because
  the retail port reads RefEngine's schema at run time:
  - retail at medium scale via `benchmarks/retail_1to1/port.py`, written as Parquet
    per table, plus D2 via `profile_1to1/datasets.py`;
  - a **day-2** copy with documented drift in `demo/DRIFT.md`: the `customer.email`
    null rate rises from about 5% to 20%; `order.status` gains a new value
    `lost`; `order.order_total` mean shifts by +40%; `product.sku` loses
    uniqueness through 50 duplicate rows;
  - `demo/contracts/*.json` in the §12.3 format, which day 1 passes and day 2 fails
    on exactly the documented rules.
- Acceptance:
  - `make_data.py` runs from §1 setup and prints file sizes.
  - With the core API, or the stub until L1 lands: day-1 contracts pass; day-2
    contracts fail on exactly the rules in `DRIFT.md`; `diff(day1, day2)` reports
    each documented change.
- Fixes: none.

**DM-05 — Fabric Python notebooks (L2)**
- Depends: none; builds against the §12.2 stub.
- Deliverables: in `integrations/fabric/notebooks/`:
  - **`shape_setup.ipynb`:** loads the DM-04 Parquet files from `Files/demo/` into
    Delta tables with `deltalake.write_deltalake`.
  - **`shape_profile.ipynb`** (Python notebook, kernel 3.11 or 3.12):
    - a `%%configure` cell setting `vCores: 8`;
    - an install cell (`%pip install` of the wheel from `builtin/`);
    - a **parameters cell** with `tableName`, `contractPath`, `baselinePath`,
      `outputDir` and `failOnDrift`;
    - reads `/lakehouse/default/Tables/<tableName>` with `deltalake`, then profiles,
      checks and diffs;
    - writes the `.shape`, the HTML report and the JSON summary to
      `/lakehouse/default/Files/shape/<table>/<timestamp>/`;
    - displays the report inline;
    - ends with a top-level `notebookutils.notebook.exit(json.dumps(result))`, where
      `result` = `{table, rows, passed, violations, drifted, changes, artifactPath}`
      (compact, < 1 MB).
  - Notebooks are stored in `.ipynb` form with the Fabric metadata needed for import.
- Acceptance:
  - `tests/demo/fabric/test_notebooks.py` runs each notebook's code cells locally
    against a temp directory standing in for `/lakehouse/default`.
    `notebookutils` is replaced by a stub that captures the exit value; magics are
    skipped.
  - It asserts the exit JSON schema, and a failing result on the day-2 data.
- Fixes: none.

**DM-05b — Fabric Environment and PySpark notebook (L2)**
- Depends: none; builds against the §12.2 stub.
- Deliverables: in `integrations/fabric/`:
  - **`environment/`:**
    - `environment.yml`, which pins numpy, pyarrow and pandas only where Runtime 2.0's
      built-ins are too old;
    - `README.md` with upload steps: the Shape wheel as a custom library in Quick mode
      for the demo, and Full mode for pipelines; runtime 2.0.
  - **`notebooks/shape_profile_spark.ipynb`** (a PySpark notebook attached to the
    Environment):
    - the same parameters cell as `shape_profile.ipynb`;
    - reads with `spark.read.table(tableName)`, then `.toArrow()` on Runtime 2.0, or
      `toPandas()` on 1.3 (the notebook detects the Spark version);
    - profiles with `exact=True` on the driver; the distributed bounded mode arrives
      in PF-02;
    - writes the same artifacts to `Files/shape/...`;
    - the same top-level `notebookutils.notebook.exit` result JSON.
  - A documented row limit for driver-side profiling (the default is based on driver
    memory). Above it, the notebook uses `spark.read.table(...).sample(...)`, and the
    result carries `sampled: true`.
- Acceptance: `tests/demo/fabric/test_notebooks.py` runs the Spark notebook's code
  cells with a local `pyspark` session (a `[dev]` dependency) against a local Delta
  table, and asserts the same exit JSON as the Python notebook on the same data.
- Fixes: none.

**DM-06 — Fabric User Data Functions (L2)**
- Depends: none; builds against the §12.2 stub.
- Deliverables: `integrations/fabric/udf/function_app.py`, using the
  `fabric-user-data-functions` SDK, with camelCase parameters and return type
  `dict`:
  - `profileLakehouseFile(lakehouse: fn.FabricLakehouseClient, filePath: str, outputPath: str = "", maxMegabytes: int = 50) -> dict`
  - `profileLakehouseTable(lakehouse: fn.FabricLakehouseClient, tableName: str, maxRows: int = 1000000, outputPath: str = "") -> dict`
    (through `connectToSql`; see DM-6)
  - `checkProfile(lakehouse: fn.FabricLakehouseClient, profilePath: str, contract: dict, failOnViolation: bool = False) -> dict`
  - `diffProfiles(lakehouse: fn.FabricLakehouseClient, baselinePath: str, currentPath: str, failOnDrift: bool = False) -> dict`
  - `profileDataFrame(data: pd.DataFrame) -> dict` (inline data, ≤ 4 MB request)

  Behaviour:
  - Failures with `fail*=True` raise `fn.UserThrownError` carrying the violations,
    so the pipeline activity fails.
  - Size guards raise `fn.UserThrownError` with a message recommending the notebook.
  - Every result is under 1 MB.

  Also add `integrations/fabric/udf/requirements.md`: the library list to add in
  Library Management (numpy, pyarrow, pandas from PyPI; the Shape wheel as a private
  library).
- Acceptance: `tests/demo/fabric/test_udf.py` installs the public
  `fabric-user-data-functions` package from PyPI and calls each function directly.
  Fake `FabricLakehouseClient` objects serve files from a temp directory and SQL rows
  from an in-memory table. The tests cover pass, fail, size-guard and
  UserThrownError paths, and check that the timings on DM-04 sample files are
  logged.
- Fixes: none.

**DM-07 — Fabric pipeline definitions (L2)**
- Depends: DM-05, DM-05b, DM-06.
- Deliverables: in `integrations/fabric/pipelines/`:
  - **(a) `shape_gate_notebook`:** a Notebook activity (`shape_profile`, with
    parameters) → If Condition on
    `@json(activity('ProfileTable').output.result.exitValue).passed` → on false, a
    Fail activity whose message lists the violations.
  - **(a2) `shape_gate_spark`:** the same shape as (a), using
    `shape_profile_spark` with the Environment attached.
  - **(b) `shape_gate_udf`:** a Functions activity (UserDataFunctions connection)
    calling `profileLakehouseFile`, then `checkProfile` with `failOnViolation: true`.

  Each pipeline ships as its Fabric item definition JSON, plus a step-by-step build
  guide in the runbook in case import isn't available.
- Acceptance:
  - A JSON-schema-level test asserts the activity graph and the expressions.
  - The exact exit-value expression is flagged in the runbook as **verify in the
    workspace on first run** (DM-4).
- Fixes: none.

**DM-08 — Runbook and talk kit (L2 writes the runbook; L3 writes the talk kit)**
- Depends: DM-03, DM-04, DM-07.
- Deliverables:
  - **`integrations/fabric/RUNBOOK.md` (L2):** workspace prerequisites (a UDF-enabled
    region; capacity); uploading the wheel and data; creating the lakehouse, the
    Environment (Runtime 2.0, Quick mode for the demo), the notebooks, the UDF item
    (including Library Management steps) and the pipelines;
    parameters; the expected result of each step, pass and fail; troubleshooting,
    including kernel choice, the `exit` placement rule and UDF size limits.
  - **`demo/TALK.md` (L3):**
    - the storyline: RefEngine retired → Shape profile in a Python notebook → HTML
      report → install once through an Environment and run the same thing in a
      PySpark notebook → contract gate passes on day 1 → day-2 data fails the
      pipeline, with the reason visible → the same check as a UDF → side-by-side
      benchmark;
    - a benchmark sheet quoting only DM-2-compliant numbers;
    - fallback steps if something is slow live.
- Acceptance: a dry run of every local step in the runbook, and a checklist in the
  runbook for the owner's live dry run.
- Fixes: none.

### 12.7 Demo exit checks (all must pass before the talk)

1. `pytest tests/demo` is green on the merged branch.
2. DM-01's parity run exits 0.
3. The DM-03 wheel installs and tests pass in a clean Python 3.11 venv.
4. **Owner dry run in Fabric (DM-4):**
   - the notebook profiles the day-1 table and exits `passed: true`;
   - the Environment publishes, and the PySpark notebook gives the same result;
   - the day-2 table makes pipeline (a) fail, with the violations shown;
   - the UDF check makes pipeline (b) fail on day 2;
   - the timings are recorded in `demo/LIVE_TIMINGS.md`.
5. Every number in `demo/TALK.md` cites its source file.

### 12.8 Demo tracker

- **Lanes never edit this table.** When a lane completes a work package, it creates
  `docs/plans/demo_status/<WP>.md` containing the commit hash, the acceptance
  commands it ran, and their results. Each such file is owned by the lane that owns
  the work package.
- The lead session, or the owner, copies the status into this table after merging.

| WP | Lane | Status | Commit |
|---|---|---|---|
| DM-00 | L3 + lead | done | 8c95156 |
| DM-01 | L1 | done (lead re-verified parity 30/30) | 831e804 |
| DM-02 | L1 | done | 5d4f296 |
| DM-03 | L1 | done (lead fixed license metadata e848348) | 92b4a5d |
| DM-03b | L1 | done; TestPyPI dry run passed (run 36665613888); **published to PyPI 0.9.0** (run 36749097798, 2026-09-30) | f17fd14 |
| DM-04 | L3 | done (lead re-ran make_data and tests/demo/content; CI fetches pinned RefEngine) | bd01f68 |
| DM-05 | L2 | done | 6217d6c |
| DM-05b | L2 | done | 578e4f4 |
| DM-06 | L2 | done | c113422 |
| DM-07 | L2 | done | 4c8725c |
| DM-08 | L2 + L3 | done: runbook (4c8725c), talk kit (b211756); owner live dry run pending (DM-4) | b211756 |

---

## 13. Roadmap work packages (2026-10-03)

The owner delegated decisions to the lead on 2026-10-03 (§2.3). These work packages build the open roadmap items. A package whose issue says "filed at start" gets its issue, which is then its specification, when its lane starts. Each one's deliverables are the numbered "Wanted" list of its issue, which is its specification; anything not on that list is out of scope (§0 default). Common acceptance for every package below: a test per deliverable item, including negative and boundary cases; the full suite in both kernel modes, `make check` and `python scripts/check_user_facing.py` pass; documentation for every new command, file format or API; a compatibility test for any persisted format (follows W1-01 once it lands); no gate, tolerance or D-xx/T-xx decision changed. Persisted formats declare `format` and an integer `version`.

| WP | Issue | Title | Depends |
|---|---|---|---|
| W1-01 | #55 | State and compatibility policy, unified version keys, newer-version error, time-capsule corpus | none |
| W1-02 | #57 | Proposals and decision files; relationship inference with evidence and confidence | none |
| W1-03 | #58 | Memorization gate, utility gate, run reproducibility tuple, dataset id, replay | none |
| W1-04 | #59 | `shape.yml` project file and `shape init` | none |
| W1-05 | #60 | Plugin API v1 stability promise and per-group conformance kit | none |
| W1-06 | #64 | Generation spec JSON Schema, stability promise and edit API | none |
| W1-07 | #65 | `docs/BRANDING.md`: which product names refer to this repository | none |
| W2-01 | #61 | Mergeable profiles | none |
| W2-02 | #52 | `fabric-mirror` sink (open mirroring landing zone) | none |
| W2-03 | #53 | Delta read fallback for deletion vectors and column mapping | none |
| W5-01 | #62 | Stable public masking API | none |
| W5-02 | #63 | Deterministic schema design engine | none |
| W1-08 | #66 | Planted-drift regression sweep, `row_count_change`, quieter `distribution_change` | none |
| W1-09 | #67 | Windows Fabric helper path, Kafka emulator flake, Spark Delta-catalog on Windows | none |
| W1-10 | filed at start | v1.0 definition of done, CLI stability promise, what we are not building | W1-05 |
| W1-11 | filed at start | Safe-by-default capture (sensitive columns keep statistics and formats only; count floor) | W1-01 |
| W1-12 | filed at start | Planned-change registry read by `diff` and gates | W1-04 |
| W1-13 | filed at start | Semantic versioning of diffs (breaking, additive, cosmetic) used by gates | W1-12 |
| W1-14 | filed at start | CI outputs: JUnit, SARIF, documented exit codes, `--json` and `--dry-run` everywhere | W1-04 |
| W1-15 | filed at start | Generator version pinning and a stability promise for pinned fixtures | W1-03, W1-06 |
| W1-16 | #68 | `shape doctor`: Fabric access and broker reachability checks | none |
| W1-17 | filed at start | Chaos safety and non-local confirmation (`--yes`, `SHAPE_CONFIRM_REMOTE=1`; after 2026-10-07) | ISS2-sinks |
| W1-18 | filed at start | Plugin allow-list and opt-in signature check; statement of plugin reach | W1-05 |
| W2-04 | #69 | Excel named ranges, tables, merged cells, autofilter; Delta reader 1 / writer 2 test | none |
| W2-05 | filed at start | Fabric guidance and measurements: Eventstream ingestion, memory on 2 vCore/16 GB, numpy 2 on Fabric runtimes | O-07 |
| W2-06 | filed at start | `sempy` source plugin for semantic models | W1-05 |
| W2-07 | filed at start | Sampling controls recorded in the profile; type inference confidence and declared-versus-inferred report | ISS2-bugs |
| W2-08 | filed at start | Sinks II: Snowflake, Databricks, Synapse | ISS2-sinks |
| W2-09 | filed at start | Streaming II: schema registry formats, dead-letter routing, arrival processes, curves, drift plans, dry run and progress | ISS2-sinks |
| W2-10 | filed at start | SQL Server write path II (Kerberos keytab, identity columns, constraint toggling, idempotent reruns); DuckDB writer | ISS2-sinks |
| W3-01 | filed at start | Rule mutation testing and rule backtesting | W2-01 |
| W3-02 | filed at start | Rule suggestion from a profile through proposal files | W1-02 |
| W3-03 | filed at start | `shape bisect` (and layer bisect) and `shape timelapse` | W2-01, W1-04 |
| W3-04 | #70 | `shape explain` and notebook display | none |
| W3-05 | filed at start | Local synthetic-data report card | W1-03 |
| W3-06 | #71 | Data quality scorecards by dimension; failing-row samples and flag column | none |
| W3-07 | filed at start | Univariate depth (model selection, zero inflation, heaping, Benford, tail index) | ISS2-joint |
| W3-08 | filed at start | Multivariate outliers, mixed-type copula, multi-column determinants | W3-07 |
| W3-09 | #72 | Duplicate detection and entity resolution | none |
| W3-10 | #73 | Reconciliation and time-series quality checks | none |
| W3-11 | filed at start | Fairness slices and training-serving skew | W3-06 |
| W3-12 | filed at start | Reference packs: ZIP-city, IBAN, ISO codes | ISS2-joint |
| W3-13 | filed at start | Environment parity check; consumer data contracts checked in producer CI | W1-04 |
| W4-01 | filed at start | Behavior models (event sequences, telemetry series, transaction streams, file arrivals, entity lifecycles) as `shape.behaviors` modules | PLUG-INT |
| W4-02 | filed at start | Optional Synthea module runner plugin | PLUG-INT |
| W4-03 | #74 | Basic locale packs | none |
| W5-03 | filed at start | Value vault (envelope encryption, per-column policy, vault hash in signatures, vault generation mode) | W1-01, W1-11 |
| W5-04 | filed at start | Contract emission: DDL, JSON Schema, pandera, Great Expectations | PLUG-INT |
| W5-05 | filed at start | Starter scenario library, named CI suites, pytest plugin and database seeding | W1-05 |
| W5-06 | filed at start | Importers (JSON Schema, OpenAPI, Avro, Protobuf, Pydantic, TMDL) and TMDL star export | W1-06, W5-02 |
| W5-07 | filed at start | Nested and standards sources: JSON arrays and structs, XML, X12 835, HL7 | PLUG-INT |
| W5-08 | filed at start | Integration plugins: OpenLineage, MLflow, Presidio, SDMetrics, Anonymeter, Ibis/DuckDB | W1-05 |
| W5-09 | filed at start | Known-answer datasets for DAX measures; drift report template (`publish-report`) | P6-07c |
| W5-10 | filed at start | File-footer fingerprint, safe-to-share bundle, skew rehearsal | W1-03 |
| W6-01 | filed at start | PR bot action, status badge, plugin template, webhook notifications | W1-14 |
| W6-02 | #75 | Air-gap hardening: zero-network tests, offline lockfile, shipped reference data | none |
| W6-03 | filed at start | Failure mode catalog, data detective packs, public dataset library, canaries and game-day runner | W5-05 |
| W6-04 | filed at start | VS Code extension and data dictionary from a profile | W1-04 |
| W8-01 | #564 | Healthcare standards writers: the `tables` companion-table option documented as a stable plugin option | PLUG-INT, W1-05 |
| W8-02 | #565 | Code-set tables: risk-adjustment model hierarchies and coefficients, with a reference scoring function | PLUG-INT |
| W8-03 | #566 | Registry pruning that keeps every referenced or tagged object and reports what it removed | none |
| W8-04 | #567 | Byte-identical CSV, JSON Lines and SQL output per engine version on every supported platform | W1-15 |
| W8-05 | #568 | Value vault management through the bridge (status, verify, key rewrap, column policy), never values or key material | W5-03, P6-11 |
| W8-06 | #766 | One switch for realistic identifiers for a whole run (`identifiers="realistic"`, `--identifiers`, schema default); the default stays reserved | none |
| W8-07 | #1 | Nested columns in profile (opaque kind), --exclude/--columns, clean Delta exit | none |
| W9-01 | #2 | Live profiling of PostgreSQL and MySQL: `postgresql://` and `mysql://` sources, whole-schema profiles with catalog keys and sampling | none |
| W9-02 | #3 | Live profiling of Snowflake and Databricks: `snowflake://` and `databricks://` sources, whole-schema profiles | none |
| W9-03 | #4 | Type fidelity on write: integer widths, time zones, decimals, float32, time precision, string lengths, on every SQL sink | none |
| W9-04 | #5 | Constraints on write: foreign, unique, check and default constraints from the generation schema and contract, per dialect | none |
| W9-05 | #6 | Nested data II: child-field profiling of struct, list and map columns; diff, check and safe-by-default over child paths | W8-07 |
| W9-06 | #7 | Native nested types in the sinks (PostgreSQL, MySQL, Databricks, DuckDB, Delta maps, Snowflake typed) | none |
| W9-07 | #8 | Delta table features on write, all opt-in: properties, constraints, column mapping, deletion vectors, generated columns, `timestampNtz` | none |
| W9-08 | #9 | Kafka and Event Hubs message metadata: keys, headers, partitions and timestamps on write and read | none |
| W9-09 | #10 | Kafka and Event Hubs formats II: Avro and Protobuf without a registry, on read, nested fields, Azure Schema Registry | none |
| W9-10 | #11 | dbt II: singular tests, semantic models, metrics and exposures | none |
| W9-11 | #12 | Fabric Eventhouse source: `eventhouse://` tables profiled through the KQL query endpoint | none |
| W9-12 | #13 | Power BI semantic models II: measures and row-level security read into the profile and kept by `export-model` | W2-06 |

P6-11 (`shape bridge`) is specified further by issue #56; its `demo_*` commands follow P6-12 and the rest start now (owner standing instruction: whatever can run in parallel, do).

---

## Appendix A — Verified bug register

Locations are given as `file::symbol (~line)`, verified on 2026-09-29. If lines drift, the symbol is authoritative. The last column is the phase that fixes the bug.

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
| P10 | `capture/vectorized.py::_numeric` (~24) | `null_count` hard-coded 0; Arrow nulls become NaN | 1 |
| P11 | `profile/text_vectorized.py::profile_text_semantic` (~10, ~34) | `None` becomes the string `'None'`; `null_count` 0 | 1 |
| P12 | `capture/vectorized.py:52` | Fast and row paths emit different schemas (spurious drift) | 1 |
| P13 | `capture/vectorized.py:79` | Text columns fall back to per-cell Python; vectorized text unused | 1 |
| P14 | `contracts/core.py:66` | `unique` compares HLL estimate to exact rows (unique ids fail at 5k–30k rows) | 1 |
| P15 | `contracts/core.py:35` | Null rate with `rows=0` divides by 1 | 1 |
| P16 | `profile/bounded_dependencies.py:48` | Samples per row, not per group; inflates FD confidence | 1 |
| P17 | `profile/dependencies.py`, `advanced.py`, `dependence.py` | Retain all rows despite "bounded" docstrings | 1 |
| P18 | `artifact/shape_file.py::read_shape` (~48–57), `artifact/io.py` (~109) | Raw `KeyError`/zlib errors escape instead of `ArtifactError` | 1 |
| P19 | `artifact/*` | `.shape` authenticity is checksum-only; `secure.py` unused | 7 |
| P20 | `api.py` | `load(save(x)) != x` (tuples → lists) | 1 |
| P21 | `query/core.py:48` | `relationship("x","x")` matches any relationship containing `x` | 1 |

**Streaming**
| ID | Location | Defect | Phase |
|---|---|---|---|
| S1 | `streaming/platinum.py:198,332` | Python `hash()`: non-deterministic across processes; fast path bins differently | 3 |
| S2 | `streaming/keyed.py` (`_heap`, ~23–40) | "Bounded" state heap unbounded | 3 |
| S3 | `streaming/windows.py:62` | `TumblingWindow` has `snapshot()` but no restore | 3 |
| S4 | `connectors/qualification.py:61-70` | Reconnect replays already-yielded batches | 3 |
| S5 | `streaming/vectorized.py::deduplicate_ids` (~37) | `deduplicate_ids` loops per row | 3 |

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
| G1 | `generation/address_vectorized.py::_u64` (~35–40), `packs/address.py::generate` (~64–68) | Seed added to row index (seed 1 = seed 0 shifted); negative seed overflows | 4 |
| G2 | `generation/strategies.py::FirstPerParent.row_at` (~102–144) | `row_at` depends on call order with `FirstPerParent` | 4 |
| G3 | `generation/certificate.py::_relative`, `certify` (~34–47, ~102) | Missing column scores 1.0; empty reference passes | 4 |
| G4 | `cli/main.py:192` | `certify-shapes` always exits 0 | 4 |
| G5 | `generation/future.py:11-14` | `missingness` never applied; `gaussian_copula` is plain MVN | 4 |
| G6 | `generation/fidelity.py::plan_reconstruction` (~90–102) | `plan_reconstruction` marks everything preserved without checking | 4 |
| G7 | `packs/address.py:71-76` | O(n × reference rows) generation | 4 |
| G8 | `packs/domains.py` (registry `get`, ~49–58) | Versions sorted as strings (1.9.0 > 1.10.0) | 2 |

**Privacy and security**
| ID | Location | Defect | Phase |
|---|---|---|---|
| SEC1 | `privacy/advanced.py:5` | Laplace noise uses fixed `seed=0` | 0 |
| SEC2 | `privacy/policy.py::release_for` (~35), `privacy/release.py::redact_sensitive` (~38) | `release_for` always allows; min/max/quantiles of PII columns leak | 0 |
| SEC3 | `privacy/policy.py:6` vs `privacy/classification.py:9` | Two inconsistent classification taxonomies | 7 |
| SEC4 | `registry/local.py:45` | `checkout(name, hash)` returns objects belonging to other names | 0 |
| SEC5 | `registry/local.py:26,32` | Name path traversal writes outside the registry root | 0 |
| SEC6 | `hub/core.py` (URL building, ~118–137) | Unencoded URL paths, no default auth, no body limit | 0 (cut) |
| SEC7 | `webapp/core.py:59` | Non-constant-time token compare, no default auth, exception text leaked | 0 (cut) |
| SEC8 | `enterprise/core.py:44` | Hard-coded audit key | 0 (cut) |

**Process**
| ID | Defect | Phase |
|---|---|---|
| X1 | `mypy --strict` reports 893 errors; `make check` fails; CI does not run mypy | 0 |
| X2 | Thin tests (301 asserts); no type-inference or boundedness tests | 0–1 |
| X3 | Root qualification JSON files are captured sandbox output; docs overclaim; migration matrix is wrong | 0 |
| X4 | `shape conformance` is three smoke tests (`validation/suite.py:14-36`) | 1 |

**Removed with deleted modules (P0-04).** Each gets a regression test asserting
`ModuleNotFoundError`.

| ID | Location | Defect |
|---|---|---|
| RM1 | `distributed/core.py` | Merged `distinct_estimate` sums partition counts (15 instead of 3); crashes when `min` is missing; keeps the first `kind` when partitions disagree; drops quartiles and top-k |
| RM2 | `etl/__init__.py` | Two different `ETLResult` classes; `isinstance` is False |
| RM3 | `observability/core.py` | Prometheus label values are not escaped; duplicate `# TYPE` lines |
| RM4 | `packages/core.py` | Version ordering is wrong; `TypeError` on mixed versions; no path-traversal check on names |
| RM5 | `reference/core.py` | Object written before the immutability check, leaving orphans; no path-traversal check |
| RM6 | `federation/core.py` | `TypeError` on a `None` mean; means weighted by counts that include nulls; `minimum_cohort` and `epsilon` never enforced |
| RM7 | `execution/core.py` | Results returned in completion order, not input order |
| RM8 | `explain.py` | `TypeError` when `distinct_estimate` is `None` |


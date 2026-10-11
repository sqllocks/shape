# FRZ-01 — Retire the reference-engine checks; Shape 0.9.1 is the baseline

Issue #20 (as redefined by the Owner's comment of 2026-10-11) · decision log §2.3, row
2026-10-11 (supersedes the 2026-10-09 row on capture) · tracker §11 row 176 · depends on REL-091
and on the `v0.9.1` tag · written 2026-10-11 against `release/0.9.1` (`f49aa94`) and
`claude/clever-ramanujan-adpvog` (`1ba9755`).

Terms used below: **the reference engine** is the archived engine the harness compared
against (its name comes only from `REFENGINE_NAME`, which this work package removes from the
repository's CI); **the baseline** is Shape 0.9.1 at the commit the `v0.9.1` tag names, installed
in its own virtual environment; **the candidate** is the Shape under test.

## 1. What changes, in one paragraph

Nothing is captured from the reference engine, and nothing runs it, in CI or locally. The
regression baseline is Shape 0.9.1's own output on the same datasets and seeds, recorded once
from the release commit by Shape's verifiers and committed as a fixture store with SHA-256
manifests. T-21 (a) to (f) and T-22 run against that baseline with every threshold unchanged;
T-21 (g) and (h), which need the engine's own code, are retired, and T-20's pin becomes a
historical fact. Timing ratios against the engine are no longer gated: `run.py` times the
candidate and the baseline in the same job (T-19's rule) and tracks the candidate against its
0.9.1 numbers. The `parity-suite`, `parity-publish`, `sqlserver-e2e` and `bench-quick` jobs stop
installing the engine (`REFENGINE_DEPLOY_KEY`, `setup_refengine.sh`, `REFENGINE_NAME`). No gate,
tolerance, D-xx or T-xx decision changes beyond what the Owner's 2026-10-11 row already decided.

## 2. Files that change

### 2.1 The parity harness, `benchmarks/vs_refengine/`

| File | Change |
|---|---|
| `setup_refengine.sh` | **Deleted.** Replaced by `setup_baseline.sh` (below). |
| `setup_baseline.sh` | **New.** Creates `$BASELINE_VENV` (default `$HOME/.venvs/shape-0.9.1`) and installs the baseline: `pip install "sqllocks-shape==0.9.1" "sqllocks-shape-domains==0.9.1" "pyarrow==25.0.1"` from `$BASELINE_INDEX` when set (a `--find-links` directory or index URL holding the `v0.9.1` release wheels), else from PyPI; when neither has 0.9.1 it builds the baseline from a `git worktree` of the tag `v0.9.1` (`pip install /path/to/worktree plugins/shape-domains`, which compiles the Rust kernel with maturin). It then asserts `python -c "import shape; print(shape.__version__)"` prints `0.9.1` and, unless `BASELINE_PURE=1`, that `shape doctor` reports the `rust` kernel (timings need the same kernel on both sides), and writes `pip freeze` to `$BENCH_OUT_DIR/baseline_freeze.txt`. It refuses any other version (exit 2 naming the version found). No secret, no key, no network beyond the package index or the repository. |
| `_refpkg.py` | **Deleted.** Nothing derives the engine's names any more. `scripts/refengine_name.py` (the hash guard that keeps the name off Shape's surface, D-13) stays. |
| `paths.py` | `REFENGINE_ROOT`, `REFENGINE_VENV`, `REFENGINE_PY` removed; `BASELINE_VENV` (`$BASELINE_VENV`, default `~/.venvs/shape-0.9.1`), `BASELINE_PY = BASELINE_VENV/bin/python`, `BASELINE_SHAPE = BASELINE_VENV/bin/shape` and `RECORDED = HERE / "recorded"` added. |
| `recorded/` | **New fixture store** (§3). `recorded/MANIFEST.json`, `recorded/profile/*.json`, `recorded/generate/*.json`, `recorded/stream/*.json`, `recorded/db/*.json`, `recorded/verify_cli/*.json`, `recorded/timings_0.9.1.json`. |
| `recorded_store.py` | **New.** `load(kind, workload_id)` reads one recording, checks the file's SHA-256 against `MANIFEST.json` and the manifest's `shape_version == "0.9.1"`, and raises `RecordingError` (the verifiers turn it into exit 2 naming the workload) on a missing file, a digest mismatch, a manifest that is missing or malformed, or an input digest that does not match the regenerated input. Never skips. Also `check_all()` for `recorded/check.py`. Standard library only (imported from both venvs). |
| `recorded/check.py` | **New.** `python benchmarks/vs_refengine/recorded/check.py`: every manifest entry exists with the recorded SHA-256 and size, every file under `recorded/` is in the manifest, the total is under 50 MB (issue #20 item 4), and the manifest names `shape_version` 0.9.1. Exit 1 otherwise, listing each problem. |
| `record_baseline.py` | **New.** The one-time recorder (§3.3). Runs only when `$BASELINE_PY` reports 0.9.1; writes every recording and the manifest; refuses to overwrite an existing `recorded/` without `--replace`. |
| `run.py` | The `refengine` implementation becomes `baseline` (run from `$BASELINE_PY`); `reference_port` leaves the plan (§5); `--quick` and `--full` time `shape` and `baseline` on the same workloads; the pre-flight checks test `$BASELINE_PY` instead of the engine venv and checkout; `machine.refengine_venv` becomes `machine.baseline_venv`; `baseline_domains()` reads `fixtures/schemas/*_3nf.json` instead of running `dump_schema.py`; results `schema_version` becomes 2 (§5). |
| `results.schema.json`, `results.json` | Schema v2 (§5): `refengine_commit` and the `refengine` tool block are replaced by a `baseline` block; the committed `results.json` is re-recorded by the acceptance run (§7). |
| `nightly.py` | `IMPLS = ("shape",)`; shards must agree on `baseline.version` and `baseline.commit` (instead of `refengine_commit`); the required-workload list (`required_ids`) drops `reference_port`; the merged `baseline` block is the first shard's; everything else (green rules, streak, subset rule) unchanged. |
| `common.py` | Unchanged (`bench_lock`, `machine_meta`, RSS helpers carry no engine reference). |
| `check_coverage.py` | **Deleted** (it reads the engine checkout). `docs/plans/refengine_coverage.tsv` stays as the record of the port; the two tests that read only the TSV stay (§6). |
| `plan_fixtures.py`, `dump_schema.py` | **Deleted** (both run inside the engine venv). `fixtures/schemas/`, `fixtures/composites/` and `fixtures/plan.json` stay, frozen: a test asserts their SHA-256s against `recorded/MANIFEST.json` (`fixtures/` entries), so they cannot drift without a manifest change. |
| `profile_1to1/verify.py` | `refengine_profile(ds, refresh)` becomes `baseline_profile(ds)`: `recorded_store.load("profile", ds)`, whose `inputs` digests must equal `_input_digest(ds)` of the datasets `datasets.py` regenerates (fail closed). `refengine_dump.py`'s normalisation (`table_to_dict`, `dataset_to_dict`) moves into `baseline_dump.py` and is used by the recorder and the candidate side. The intentional-difference adjustments (`enum_rule_baseline`, `long_text_rule_baseline`, `identifier_rule_baseline`, the infinity allow-list, `DATE_DEPENDENT`) are removed: they rewrote the engine's profile into the corrected behaviour, and 0.9.1 already has that behaviour. `RULES`, `TABLE_RULES`, `compare`, `corr_compare`, `check_table` and every tolerance (`1e-9`, `1e-6`, exact fields) are unchanged. `--refresh` is removed (nothing to refresh); `--impl reference_port` is removed. |
| `profile_1to1/refengine_dump.py`, `refengine_cli_profile.py` | **Deleted** (renamed to `baseline_dump.py`; the CLI timing of the engine is gone). `profile_1to1/bench.py` and `bench_cli.py` time `shape` and `baseline` (the baseline worker is the same `shape` worker started from `$BASELINE_PY`). |
| `domain_1to1/verify.py` | The reference side (`REF_SEED` 42) and the self-baseline (`BASELINE_SEEDS` 43, 44, 45, 46) are generated by `generate.py --impl shape` **in the baseline venv** (`ensure_run("baseline", ...)`, output under `$BENCH_OUT_DIR/baseline/<domain>/<scale>/seed<N>/`), and before use each run's per-table `dataset_id` must equal `recorded/generate/<domain>_<scale>.json` (fail closed, exit 2 naming the workload, seed and table). `load_schema_json` reads `fixtures/schemas/<domain>_3nf.json`. Clauses (a) to (f) keep `baseline_distances`, `merge_baselines`, `compare_column`, `fanout`, `record_coherence`, `semantic_rates`, `Pools` and every tolerance. Clause (g) (`BusinessRulesEngine.validate`) and (h) (`FidelityComparator`) are removed with `rebuild_schema`, `_refpkg` and the report keys `business_rules` and `refengine_fidelity_comparator`; the report gains `"clauses": ["a", "b", "c", "d", "e", "f"]` and `"baseline": {"shape_version": "0.9.1", "commit": ...}`. It runs in the Shape venv (`[dev]` has pandas and scipy). `--impl` keeps `shape` only; `--cli` compares the CLI runs of `bench_cli.py` (baseline CLI seed 42 against the candidate CLI). A new `--negative-control` (§6.3). |
| `domain_1to1/generate.py` | `--impl refengine` and `reference_port` removed; `--impl shape` is run from either venv (the venv decides the version); `out_dir(impl, ...)` takes `baseline` as an impl name. |
| `domain_1to1/rowcounts.py` | The `baseline` side reads the planned counts from `fixtures/plan.json` (frozen) instead of the engine's planner; the generated-count comparison runs the baseline venv. |
| `domain_1to1/bench.py`, `bench_cli.py` | Time `shape` and `baseline`. |
| `stream_1to1/verify.py`, `stream_common.py`, `baseline_worker.py` | The baseline command is `$BASELINE_SHAPE stream` at seeds 42 to 46 (the candidate at 1042); `FIELD_MAP` is the identity and `ALLOWED` (the engine's defects Shape fixes) is empty and removed with its probes; every check of names, order, counts and T-21 (b) to (e) per field is unchanged; the baseline files' digests are checked against `recorded/stream/retail_order_<scale>.json` before use; `--negative-control` kept. Runs in the Shape venv. |
| `stream_1to1/bench.py` | Times both `shape` commands (candidate and baseline), fresh processes, as today. |
| `db_1to1/verify.py`, `db_dump.py`, `intentional.py` | The baseline profile of each scenario comes from `recorded/db/<case>.json` (fake server) and `recorded/db/<case>.mssql.json` (real SQL Server 2022); `db_dump.py` is deleted; `intentional.py` (FIX-1 to FIX-9, the engine's defects) is deleted and the "a listed fix never exercised" rule with it. Field rules are `profile_1to1`'s, unchanged. `--mssql` loads the scenarios into the real server and profiles them with the candidate, as today. |
| `verify_1to1/verify.py` | The baseline CLI is `$BASELINE_SHAPE verify`; the retail runs it reads are the baseline venv's seed-42 output and the candidate's seed-1042 output (both `--impl shape`); the recorded 0.9.1 reports in `recorded/verify_cli/<scenario>.json` are checked against the baseline venv's fresh reports before the comparison (fail closed). Scenarios, the gate-by-gate comparison and exit rules are unchanged. |
| `README.md` | Rewritten: recorded mode, the baseline venv, the record date and commit, how a record would be redone (§3.3), the retired clauses, and the file table without the deleted entries. |
| `tests/test_setup_refengine.py` | **Deleted.** Replaced by `tests/test_setup_baseline.py` (§6). |
| `github_known_hosts` | **Deleted** (only the SSH clone used it). |

Out of scope for this package, listed in §9: the other `*_1to1` harnesses that import
`_refpkg` and that no CI job runs (`bridge_1to1`, `chaos_1to1`, `pack_1to1`, `scale_1to1`,
`fabric_commands_1to1`, `simulation_1to1`, `demo_1to1`, `mask_1to1`, `transform_1to1`,
`learn_1to1`, `incremental_1to1`, `strategy_1to1`, `safe_profile_1to1`, `writers_1to1`,
`fidelity_1to1/baseline_scores.py`, `fidelity_tiers_1to1/baseline_tiers.py`, `ddl_1to1/dump_ddl.py`,
`domain_1to1/seed_study*.py`, `domain_1to1/port.py`, `profile_1to1/port.py`, `profile_1to1/adapter.py`,
`profile_1to1/kernel_spy.py`, `schema_import.py`, `composites.py` where it imports the engine).
Their committed goldens and `fixtures/expected/` files stay. After `_refpkg.py` is deleted they
fail at import with `ModuleNotFoundError`; a one-line module `benchmarks/vs_refengine/_refpkg.py`
is therefore kept with only `raise RuntimeError("the reference engine is archived (FRZ-01, 2026-10-11); this harness is historical")`
so the failure names the reason.

### 2.2 CI and Nightly workflows

| File, job | Change |
|---|---|
| `.github/workflows/ci.yml`, top-level `env` | `REFENGINE_NAME: ${{ secrets.REFENGINE_NAME }}` removed. |
| `ci.yml`, `bench-quick` | The step "Pinned RefEngine baseline and venv" (with `REFENGINE_DEPLOY_KEY`) becomes `source scripts/env.sh && bash benchmarks/vs_refengine/setup_baseline.sh`; the `test_setup_refengine.py` step becomes `pytest -q benchmarks/vs_refengine/tests/test_setup_baseline.py`; the `check_coverage.py` step is removed; the fixture step runs `"$SHAPE_VENV/bin/python" benchmarks/vs_refengine/recorded/check.py` and `domain_1to1/rowcounts.py` (no `plan_fixtures.py --check`); `run.py --quick` unchanged in form; the P6-09 step becomes `"$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/generate.py --impl shape --domain retail --scale small --seed 1042`, the same with `"$BASELINE_PY"` and `--seed 42`, then `verify_1to1/verify.py`; the artifact adds `bench-out/baseline_freeze.txt`. The job comment names the baseline as Shape 0.9.1. |
| `.github/workflows/nightly.yml`, top-level `env` | `REFENGINE_NAME` removed. |
| `nightly.yml`, `parity-suite` | Same substitution of the setup step; `check_coverage.py` step removed; `recorded/check.py` added before the shard; the artifact path `bench-out/refengine_freeze.txt` becomes `bench-out/baseline_freeze.txt`. Shards, timeout and matrix unchanged. |
| `nightly.yml`, `parity-publish` | Unchanged except that `nightly.py merge` now judges `baseline.version`/`baseline.commit` agreement; the comment's "pinned baseline" wording becomes "Shape 0.9.1". |
| `nightly.yml`, `sqlserver-e2e` | The step "Pinned baseline venv (plan section 1.2) with pyodbc" is removed; the T-22 step is unchanged in form (`db_1to1/verify.py --mssql "$SHAPE_TEST_MSSQL;Database=tempdb"`) and reads `recorded/db/`. |
| `.github/workflows/nightly.yml`, new job `record-baseline` (`workflow_dispatch` only, input `confirm: record-0.9.1`) | Checks out the ref it is run on, runs `setup_baseline.sh` and `record_baseline.py --all --mssql` (with the SQL Server emulator up, as `sqlserver-e2e` does) on `ubuntu-latest`, and uploads `benchmarks/vs_refengine/recorded/` and `bench-out/baseline_freeze.txt` as the artifact `recorded-baseline`. It never commits; the lead downloads and commits (§8). |
| Repository secrets `REFENGINE_DEPLOY_KEY`, `REFENGINE_NAME` | No longer read by any workflow (Owner action to delete them, §9 of the plan; not a file change). |

### 2.3 bench-quick's local equivalent, environment and scripts

| File | Change |
|---|---|
| `scripts/env.sh` | `REFENGINE_ROOT`, `REFENGINE_VENV`, `REFENGINE_PY` and the `REFENGINE_NAME` warning removed; `BASELINE_VENV="${BASELINE_VENV:-$HOME/.venvs/shape-0.9.1}"`, `BASELINE_PY`, `BASELINE_SHAPE`, `BASELINE_INDEX` (optional) added; the header says the baseline is Shape 0.9.1. |
| `scripts/gen_performance_page.py` | Reads schema v2: the published ratio is `baseline.median_s / shape.median_s` per workload (candidate against Shape 0.9.1 timed in the same job); the page's first paragraph says so and links `recorded/timings_0.9.1.json` as the release-day numbers on the 4-vCPU runner; the D-13 name guard and every publication rule (verifier exit 0, no dirty tree, nothing for the port) unchanged. |
| `docs/PERFORMANCE.md` | Regenerated by the acceptance run (§7). |
| `Makefile` | Unchanged (`check` lints `benchmarks/vs_refengine`; `bench-quick` is a CI job, not a target). |
| `CLAUDE.md`, `docs/CI.md`, `docs/plans/COMPLETION_PLAN.md` §1.2, §6.4 ("Never modify the RefEngine checkout"), §11 row 176, §2.3 | §1.2 gets a note "historical from FRZ-01: the engine is not installed; see `benchmarks/vs_refengine/README.md`"; §6.4's rule stays (there is no checkout to modify); row 176 to `done` with the commits; a §2.3 row records the landing. The decisions themselves are not edited (§0.4). |
| `CHANGELOG.md` | Under `Unreleased` (0.9.2): "The parity suite and `bench-quick` compare against Shape 0.9.1's recorded output; the reference engine is no longer installed. T-21 (g) and (h) retired." |
| `tests/benchmarks/test_run_harness.py`, `test_nightly_suite.py`, `test_vs_refengine_harness.py`, `test_performance_page.py`, `tests/release/test_packaging.py` (if it lists harness files) | Updated for schema v2, `IMPLS`, `BASELINE_*` paths and the deleted files (§6). The REL-091 fixture that sets `REFENGINE_NAME` for the offline stand-in is removed with `_refpkg`. |

## 3. The 0.9.1 baseline record

### 3.1 Workloads, datasets and seeds (unchanged from `run.py --full` and the CI jobs)

| Kind | Workload ids | Inputs | Seeds |
|---|---|---|---|
| `profile` (T-22) | `profile:d1.csv`, `profile:d1.parquet`, `profile:d2.csv`, `profile:d2.parquet`, `profile:d3.csv`, `profile:d3.parquet`, `profile:d4.csv`, `profile:d4.parquet`, `profile:mt`, `profile:mt.parquet`, every `profile:edge/e{3,15,60,130,3000}[_uuidpk].{csv,parquet}` and every `profile:edge/x_*` of `EDGE_DEVIATIONS` | the files `profile_1to1/datasets.py` writes (byte-identical from their fixed seeds; D3 is 5,000,000 rows) | none (profiling is deterministic) |
| `generate` (T-21) | `generate:retail:small`, `generate:retail:medium`, `generate:retail:large`, and `generate:<domain>:medium` for every other domain of `fixtures/schemas/` (`capital_markets`, `education`, `financial`, `healthcare`, `hr`, `insurance`, `iot`, `manufacturing`, `marketing`, `pulse`, `real_estate`, `supply_chain`, `telecom`), 3nf mode | `fixtures/schemas/<domain>_3nf.json` | baseline **42** (the reference) and **43, 44, 45, 46** (the drift set for T-21's `max()` terms); candidate **1042** |
| `stream` (STREAM-EMIT, T-21 (b) to (e)) | `stream:retail:order:small`, `stream:retail:order:medium` | retail `order`, `--no-realtime --sink file`, whole table and `--max-events N` | baseline 42 to 46; candidate 1042 |
| `db` (T-22 on database profiles) | one per scenario of `shape_sqlserver.testing` (`db:<scenario>`), each on the fake server and on SQL Server 2022 (`db:<scenario>:mssql`) | the deterministic scenarios | none |
| `verify_cli` (P6-09) | one per scenario of `verify_1to1/verify.py` | the retail small runs (baseline 42, candidate 1042) | as `generate` |

The seed set is fixed by T-21 and the Owner's row: the verifiers refuse any other set and have
no option for it; a verdict from another set counts for nothing.

### 3.2 Fixture format

All JSON, UTF-8, keys sorted, two-space indent, one file per workload, nothing platform-specific
in a compared value. Every file carries `format`, `version` (1), `workload`, `shape_version`
(`"0.9.1"`), `shape_commit` (the `v0.9.1` commit), `recorded_utc` and `inputs` (a map from each
input's relative path to its SHA-256). Raw generated data is not stored (issue #20 item 1): the
generation and stream recordings hold **digests and shapes**, and the baseline data is
regenerated at run time from the same seeds by the same 0.9.1 code, then checked against them.

| File | Compared values it holds |
|---|---|
| `recorded/MANIFEST.json` | `{"format": "shape-baseline-record", "version": 1, "shape_version": "0.9.1", "shape_commit", "recorded_utc", "machine" (as `common.machine_meta()`), "baseline_freeze_sha256", "files": {"<path under recorded/>": {"sha256", "bytes"}}, "fixtures": {"fixtures/<path>": {"sha256"}}, "workloads": [ids of §3.1]}` |
| `recorded/profile/<dataset id with / as _>.json` | the baseline's profile exactly as `baseline_dump.table_to_dict` / `dataset_to_dict` normalises it (the same fields T-22 compares: dtype, null counts, cardinality, uniqueness, enum flags and values, `value_counts_ext`, mean, std, quantiles, distribution family and parameters, histograms, string lengths, outlier rate, fit score, correlation matrix, PK/FK, pattern), or `{"__error__": {"category": ...}}` where 0.9.1 raises |
| `recorded/generate/<domain>_<scale>.json` | per seed (42 to 46): per table `rows`, `columns` (names in order with Arrow type strings), `dataset_id` (`shape.repro.dataset_id({table: data})`, content-based, platform-independent) and, informational only, the Parquet file's SHA-256 and bytes; the whole run's `dataset_id` over all tables |
| `recorded/stream/retail_order_<scale>.json` | per seed (42 to 46) and per variant (`whole`, `prefix`): event count, field names in order, the `dataset_id` of the events read back as a table, the JSON-lines file's SHA-256 (informational) |
| `recorded/db/<scenario>.json`, `<scenario>.mssql.json` | the baseline's database profile (fake server; real SQL Server 2022 loaded from the scenario), normalised as `profile_1to1` does, plus the scenario's table digests |
| `recorded/verify_cli/<scenario>.json` | the baseline `shape verify` report per scenario: exit code and, gate by gate, pass/fail, errors, warnings and details |
| `recorded/timings_0.9.1.json` | the release-day `results.json` (schema v2) of `run.py --full` on the 4-vCPU runner: Shape 0.9.1's own medians, with `verifier` records that exited 0 (equivalence before timing, §6.4) |

Budget: under 50 MB in total (`recorded/check.py` enforces it); the expected size is a few
megabytes, dominated by the D3 and D4 profiles' `value_counts_ext` (top 500 per column).

The fixture store from `lane/frz-01` (SHA-256 manifest, fail-closed loading) is reused where it
exists. On 2026-10-11 no branch named `lane/frz-01` is on `origin` (only `lane/REL-091` and
`lane/rel-091`); if the lane lives only on the lead's machine, its `recorded_store.py`, manifest
writer and tests are the starting point, otherwise this section is the specification.

### 3.3 How the record is made (once, by the lead, from the release commit)

```bash
git fetch origin tag v0.9.1
git worktree add /tmp/shape-0.9.1 v0.9.1                  # the baseline's source, for the pure-wheel fallback
source scripts/env.sh                                      # BASELINE_VENV, BENCH_DATA_DIR, BENCH_OUT_DIR
bash benchmarks/vs_refengine/setup_baseline.sh             # Shape 0.9.1 + domains in $BASELINE_VENV, pyarrow 25.0.1, rust kernel
"$SHAPE_VENV/bin/python" benchmarks/vs_refengine/profile_1to1/datasets.py   # D1-D4, MT, EDGE, byte-identical
docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
"$SHAPE_VENV/bin/python" benchmarks/vs_refengine/record_baseline.py --all \
    --mssql "$SHAPE_TEST_MSSQL;Database=tempdb" --out benchmarks/vs_refengine/recorded
"$SHAPE_VENV/bin/python" benchmarks/vs_refengine/recorded/check.py
git add benchmarks/vs_refengine/recorded && git commit -m "FRZ-01: record the Shape 0.9.1 baseline (v0.9.1)"
```

`record_baseline.py`:

- refuses to run unless `$BASELINE_PY` imports `shape.__version__ == "0.9.1"` and
  `git rev-parse v0.9.1^{commit}` resolves (the manifest's `shape_commit`); `--all` is
  `--profile --generate --stream --db --verify-cli --timings`, each selectable;
- generates every `generate` and `stream` workload at seeds 42, 43, 44, 45, 46 in the baseline
  venv (`domain_1to1/generate.py --impl shape`, `$BASELINE_SHAPE stream`) under
  `$BENCH_OUT_DIR/baseline/`, computes the digests and writes the recordings;
- profiles every `profile` dataset and every `db` scenario in the baseline venv
  (`baseline_dump.py` run with `$BASELINE_PY`), the `db:*:mssql` ones only with `--mssql`;
- runs `$BASELINE_SHAPE verify` over the `verify_1to1` scenarios;
- with `--timings`, runs `run.py --full` with the baseline as the only implementation and
  stores the result as `timings_0.9.1.json` (the 4-vCPU runner: use the `record-baseline`
  workflow for this part, or the same machine class);
- writes `MANIFEST.json` last, with the SHA-256 and size of every file it wrote and of every
  `fixtures/` file, and refuses to overwrite an existing store without `--replace`.

The lead's run is the only run that writes `recorded/`; the builder tests the recorder with a
stub baseline venv (§6.1). Redoing the record (a new baseline version) is a new work package
that re-records every workload and bumps `shape_version` in the manifest; the verifiers refuse
a manifest whose `shape_version` is not the one they expect.

## 4. How the checks run against the baseline

### 4.1 T-21 (a) to (f), unchanged thresholds

`domain_1to1/verify.py --impl shape --domain D --scale S`:

1. ensures the five baseline runs (seeds 42 to 46, baseline venv) and the candidate run (seed
   1042, Shape venv) exist under `$BENCH_OUT_DIR`, generating any that are missing;
2. loads `recorded/generate/D_S.json` through `recorded_store` (manifest digest checked) and
   requires every baseline run's per-table `dataset_id` to equal the recording's; a mismatch is
   exit 2: `baseline retail/medium/seed43 table order: dataset_id differs from recorded/generate/retail_medium.json (the baseline venv is not Shape 0.9.1 as recorded)`;
3. computes, exactly as today, the self-baseline `B` from seeds 43 to 46 against seed 42
   (`baseline_distances`, `merge_baselines`) and the candidate's distances, and asserts
   (a) identical table names, column names and order, Arrow types (`large_string` ≡ `string`)
   and row counts; (b) null rate within max(5σ, 1.5 × the baseline's seed-to-seed drift);
   (c) KS ≤ max(critical value at α = 0.001, 1.5 × max seed-to-seed KS + 0.002); (d) TVD ≤
   max(3 × multinomial noise, 1.5 × seed-to-seed TVD + 0.002) and vocabulary overlap ≥ 0.999;
   (e) pooled high-cardinality overlap ≥ 0.999 or component parse; (f) 100% FK integrity and
   fan-out within the seed-to-seed range, plus the record-coherence and strategy-semantics
   floors (min over the baseline seeds − 0.005) as today;
4. writes the report and the text summary; exit 0 only when nothing is flagged.

Clauses (g) and (h) are retired: not computed, not in the report, not in the text. The T-21
row in §2.2 is not edited (§0.4); this package's §2.3 row records that (g) and (h) are retired
by the Owner's 2026-10-11 decision and T-20's pin is historical.

### 4.2 T-22, unchanged thresholds

`profile_1to1/verify.py --impl shape [dataset ...]`: for each dataset, `recorded_store.load("profile", ds)`
(digest checked, `inputs` must equal the regenerated files' SHA-256s), the candidate profile
through `shape.profile(...)` in process, both normalised by `baseline_dump.table_to_dict`, then
`check_table` with `RULES` (exact match for dtype, null counts, cardinality, uniqueness, enum
flags, `enum_values`/`value_counts_ext` top 500, PK/FK, pattern; enum weights within 1e-9;
mean and std within 1e-9 relative; quantiles within 1e-9 relative; distribution family equal
and parameters within 1e-6 relative) and `TABLE_RULES`. A recorded `__error__` requires the
candidate to raise the same category. Exit 1 on any field outside T-22, exit 2 on a missing
dataset or recording. `db_1to1/verify.py` applies the same rules to `recorded/db/`.

### 4.3 Stream and `verify` parity

`stream_1to1/verify.py --scale S` checks the baseline files' digests against
`recorded/stream/`, then names and order, event counts and T-21 (b) to (e) per field with the
baseline's own seed-to-seed spread, as today. `verify_1to1/verify.py` compares the baseline
venv's `shape verify` and the candidate's gate by gate, after checking the baseline reports
against `recorded/verify_cli/`.

### 4.4 What "Shape 0.9.1 in the reference engine's place" does not change

The datasets, the scales, the seeds, every tolerance and critical value, the order
"equivalence before timing", the fresh-process timing protocol, the 4-vCPU runner, the median
of 5 runs (3 on `--quick`), the exclusive benchmark lock, the nightly green rules and the
7-night streak.

## 5. Benchmarks: Shape's own timings against its 0.9.1 numbers

`run.py` (schema v2) records two implementations per workload: `shape` (candidate) and
`baseline` (Shape 0.9.1 in `$BASELINE_VENV`), both timed in the same job, fresh processes,
imports excluded, after the candidate's verifier exited 0 (the baseline's own verifier is the
digest check of §4.1, recorded as `verifier: {"status": "pass", ...}`). `results.json` v2:

```json
{"schema_version": 2, "mode": "quick", "runs": 3, "generated_utc": "...", "machine": {...},
 "baseline": {"kind": "shape", "version": "0.9.1", "commit": "<v0.9.1>", "workloads": {...}},
 "shape_commit": "...", "shape_tree_dirty": false,
 "shape": {"workloads": {"profile:d1.csv": {"kind": "profile", "verifier": {...}, "median_s": 0.42, "baseline_median_s": 0.41, "ratio_vs_baseline": 0.98, ...}}}}
```

`speedup_vs_refengine` and `cli_speedup_vs_refengine` become `ratio_vs_baseline` and
`cli_ratio_vs_baseline`; the `refengine` and `reference_port` blocks are gone; `nightly.py merge`
and `gen_performance_page.py` read the new keys. The T-19 PR regression threshold (10% against
the last nightly median) applies to `shape.median_s` as before. The §3.4 ratio gates against
the engine are historical, as the Owner's row says: `gen_performance_page.py` shows the
candidate-to-0.9.1 ratio and links `docs/plans/evidence/G1*`, `G3`, `G4` as the record of the
past comparison. `--quick` times `shape` on D1, D2 (csv and parquet) and retail small and medium
(3 runs); `--full` as §3.1 (5 runs).

## 6. Tests to write

All under `tests/benchmarks/` unless noted; they run with a **stub baseline** (a tiny fake
`$BASELINE_PY` and fake `shape` script written by the test, plus a stub `recorded/` store in
`tmp_path`), never with the real 0.9.1 install, so they run on every PR without network. Each
Wanted item has positive, negative and boundary cases.

### 6.1 The recorder and the store (`test_recorded_store.py`, `test_record_baseline.py`)

- `record_baseline.py` with the stub baseline writes every expected file and a manifest whose
  SHA-256s and sizes match the files; `recorded/check.py` exits 0 on it.
- It refuses a baseline whose `__version__` is not `0.9.1` (exit 2, the version in the message),
  an existing store without `--replace`, and a missing `v0.9.1` tag.
- `recorded_store.load` returns the recording when the digest matches; raises `RecordingError`
  naming the workload when the file is missing, when one byte is changed (digest mismatch),
  when the manifest is missing or malformed, when `shape_version` is not `0.9.1`, and when an
  `inputs` digest differs from the regenerated input. Boundary: an empty `files` map, a manifest
  that lists a file that does not exist, a file not listed in the manifest (`check.py` exit 1).
- `check.py` fails when the total exceeds 50 MB (a sparse 51 MB file in `tmp_path`).
- `fixtures/` entries: changing one byte of a schema fixture makes `check.py` fail.

### 6.2 The verifiers in recorded mode (`test_profile_verify_recorded.py`, `test_domain_verify_recorded.py`, `test_stream_verify_recorded.py`, `test_db_verify_recorded.py`)

- Positive: a candidate equal to the recording (the stub produces the same output on both
  sides) passes with exit 0 and a report naming `clauses a-f` and `baseline.shape_version 0.9.1`.
- Negative controls that prove a real regression still fails, each asserting exit 1 and the
  flagged clause or field in the report:
  - T-22: a candidate `mean` off by 1e-6 relative (tolerance 1e-9) on one column; a `null_count`
    off by one; an `enum_values` weight off by 2e-9; a missing `value_counts_ext` entry; a
    different distribution family; a changed `pattern`; a column dropped; a table's
    correlation matrix entry off by 1e-6.
  - T-21 via `domain_1to1/verify.py --negative-control` (new flag, as `stream_1to1` has): on the
    candidate run in memory, scale a numeric column by 1.05 (c), rename one category (d, vocab
    overlap), raise a column's null rate by 10 points (b), drop 2% of a child table's rows (a),
    point 1% of a foreign key at missing parents (f), reorder two columns (a), change a type
    `int64` to `float64` (a); the verifier must flag each in turn and exits 1 if any goes
    undetected (a passing negative control is itself a failure).
  - Stream: the existing `--negative-control` perturbations, unchanged.
  - A recording missing or tampered: exit 2 naming the workload, and `run.py` records the
    workload with `verifier.status == "unavailable"` and `median_s: null` (never a pass).
- Boundary: a baseline run whose `dataset_id` matches but whose Parquet SHA-256 differs
  (another platform) still passes (the file digest is informational); a seed outside 42 to 46
  or 1042 is refused.

### 6.3 Retirement of (g) and (h), and of the engine

- `test_clauses_g_and_h_are_retired`: `domain_1to1/verify.py` has no `BusinessRulesEngine`,
  `FidelityComparator`, `rebuild_schema`; its report has no `business_rules` or
  `refengine_fidelity_comparator` keys; `clauses == ["a", "b", "c", "d", "e", "f"]`.
- `test_no_engine_in_ci`: `.github/workflows/ci.yml` and `nightly.yml` contain neither
  `REFENGINE_DEPLOY_KEY`, `REFENGINE_NAME` nor `setup_refengine.sh`; `scripts/env.sh` sets no
  `REFENGINE_*`; no file under `benchmarks/vs_refengine/` that a CI job runs imports `_refpkg`
  (the historical list of §2.1 excepted).
- `test_the_workflow_matrix_lists_every_shard` and the `test_nightly_suite.py` cases keep
  passing with `IMPLS == ("shape",)` and schema v2 (`test_a_dirty_tree_or_mixed_commits_make_the_night_red`
  now mixes `baseline.commit`).
- `test_setup_baseline.py` (in `benchmarks/vs_refengine/tests/`, run by `bench-quick` as the SSH
  contract test was): with stand-in `python` and `pip` on `PATH`, the script installs from
  `$BASELINE_INDEX` when set, from the index otherwise, falls back to the worktree build, writes
  `baseline_freeze.txt`, exits 2 on any version but 0.9.1 and on a pure kernel without
  `BASELINE_PURE=1`; the script contains no `ssh`, no key material and no `REFENGINE`.
- `test_the_page_never_names_the_baseline` keeps passing (D-13); `test_results_schema_*` move to
  v2 and reject a v1 document and a `refengine` key.
- `scripts/check_user_facing.py` and `ruff`/`mypy` over the changed files (T-27 scope).

## 7. Acceptance commands and expected results

Run from the repository root on a 4-vCPU Linux runner after the record is committed (§8 step 4);
every command in this list must be run in the accepting session (§0.5).

| # | Command | Expected |
|---|---|---|
| 1 | `source scripts/env.sh && bash benchmarks/vs_refengine/setup_baseline.sh` | exit 0; `$BENCH_OUT_DIR/baseline_freeze.txt` lists `sqllocks-shape==0.9.1`, `sqllocks-shape-domains==0.9.1`, `pyarrow==25.0.1`; `"$BASELINE_SHAPE" --version` prints `shape 0.9.1` |
| 2 | `python benchmarks/vs_refengine/recorded/check.py` | exit 0; prints the file count, total bytes (< 50 MB) and `shape_version 0.9.1` |
| 3 | `source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/profile_1to1/datasets.py && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/profile_1to1/verify.py --impl shape` | exit 0; the per-field matrix shows every field passing on every dataset, `0 mismatches` |
| 4 | `source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/verify.py --impl shape --domain retail --scale small` (and `medium`, `large`) | exit 0; `60/60 columns EQUIVALENT`, `clauses a-f`, no `(g)` or `(h)` line; the text names the baseline as Shape 0.9.1 seeds 42 to 46 |
| 5 | `... domain_1to1/verify.py --impl shape --domain retail --scale small --negative-control` | exit 0 **and** the output lists every perturbation as caught (the command exits 1 if one is missed) |
| 6 | `source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/stream_1to1/verify.py --scale medium` and `--negative-control` | exit 0 both; every perturbation caught |
| 7 | `source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/db_1to1/verify.py` (fake server) and, with the emulator up, `--mssql "$SHAPE_TEST_MSSQL;Database=tempdb"` | exit 0; every case matches; no "intentional difference" section |
| 8 | `source scripts/env.sh && "$BASELINE_PY" benchmarks/vs_refengine/domain_1to1/generate.py --impl shape --domain retail --scale small --seed 42 && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/generate.py --impl shape --domain retail --scale small --seed 1042 && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/verify_1to1/verify.py` | exit 0; every scenario agrees |
| 9 | `source scripts/env.sh && python benchmarks/vs_refengine/run.py --quick --out "$BENCH_OUT_DIR/results.json"` | exit 0; `results.json` validates against schema v2; every `shape` workload has `verifier.status == "pass"` and a `median_s`; `baseline.version == "0.9.1"`; `ratio_vs_baseline` present per workload |
| 10 | `source scripts/env.sh && python benchmarks/vs_refengine/run.py --quick --dry-run` | lists profile D1, D2 (csv, parquet), generate retail small+medium, stream small+medium, implementations `shape` and `baseline` |
| 11 | tamper: `python - <<'EOF'` that flips one byte of `recorded/profile/d1.csv.json`, then command 3 | exit 2 with `recorded/profile/d1.csv.json: digest mismatch` naming workload `profile:d1.csv`; `git checkout -- benchmarks/vs_refengine/recorded` afterwards |
| 12 | `pytest -q tests/benchmarks benchmarks/vs_refengine/tests` with `SHAPE_KERNEL=rust` and again with `SHAPE_KERNEL=python` | all pass, including the new files of §6 |
| 13 | `make check && python scripts/check_user_facing.py` | exit 0 (ruff, format, mypy strict on the changed modules, the suite) |
| 14 | `grep -rn 'REFENGINE_DEPLOY_KEY\|setup_refengine\|REFENGINE_NAME' .github/workflows scripts/env.sh` | no output |
| 15 | Dispatch Nightly on the branch | `parity-suite` five shards green, `parity-publish` green ("Fail on a red night" passes), `sqlserver-e2e` green; the `parity-suite` artifact holds `bench-out/baseline_freeze.txt` |
| 16 | The branch's CI run | `bench-quick` green with the `benchmark-results-quick` artifact; `test`, `zero-network`, `docs-test` green (no `REFENGINE_NAME` needed) |
| 17 | `python scripts/gen_performance_page.py --check` after regenerating from command 9's results | exit 0; the page names no engine (D-13) and shows the candidate-to-0.9.1 ratios |

## 8. Order relative to the `v0.9.1` tag

1. REL-091 lands and `v0.9.1` is tagged from `release/0.9.1`; the release workflow publishes the
   wheels (PyPI or the GitHub release). FRZ-01 **starts after the tag** (the Owner's comment):
   the baseline is recorded from that commit and the version is `0.9.1`.
2. The builder branches `lane/FRZ-01` from `main` after the tag (version `0.9.2.dev`), builds
   §2 and §6 with the stub baseline, and makes commands 10 to 14 of §7 pass. `recorded/` is
   absent on the lane at this point; `recorded/check.py` and the verifiers exit 2 with
   "no recording" (never a pass), which is the expected state of a lane without the record.
3. The lead runs §3.3 (or the `record-baseline` workflow) with the lane's code and the real
   0.9.1 baseline, reviews the sizes per workload, and commits `recorded/` to the lane.
4. The lead runs §7 commands 1 to 9 and 15 to 17; on green, merges, sets tracker row 176 to
   `done` with the commits, adds the §2.3 row, and asks the Owner to delete the two secrets.
5. Nothing in this order touches `release/0.9.1` or the tag; a 0.9.1 patch release would not
   change the baseline (the record is of the tag's commit).

## 9. Questions for the Owner or lead, with recommendations

1. **The engine-only harnesses outside the four CI jobs** (list in §2.1). Recommendation: out of
   FRZ-01's scope; file FRZ-02 to delete them (T-23: delete rather than archive; git keeps
   them) and keep every committed golden. Until then `_refpkg.py` raises the one-line
   "archived" error.
2. **`reference_port`.** It was the engine's 1:1 port, verified against the engine. With the
   engine gone its verifiers have no meaning. Recommendation: drop it from `run.py`'s plan and
   `nightly.py`'s `IMPLS` (as specified above), keep `profile_1to1/port.py` and
   `domain_1to1/port.py` on disk for FRZ-02.
3. **`check_coverage.py` and `refengine_coverage.tsv`.** The script cannot run without the
   checkout. Recommendation: delete the script, keep the TSV and its two tests as the record
   of D-01's porting map.
4. **Timing the baseline in the same job** (§5) versus only comparing with the release-day
   numbers. Recommendation: time both in the same job (T-19's "same job" rule, same pyarrow,
   same kernel) and keep `timings_0.9.1.json` as the release-day reference; the cost is one
   extra Shape run per workload, minutes per shard.
5. **`fixtures/schemas`, `composites`, `plan.json`** can no longer be regenerated
   (`plan_fixtures.py` ran in the engine venv). Recommendation: freeze them under the manifest
   (§2.1) so a change is deliberate and visible.
6. **`lane/frz-01` is not on `origin`.** Recommendation: the lead pushes it if it exists; if not,
   §3.2 is the specification and nothing is lost.
7. **The two repository secrets** (`REFENGINE_DEPLOY_KEY`, `REFENGINE_NAME`) stay unused until
   the Owner deletes them; no workflow reads them after this package. `scripts/refengine_name.py`
   keeps guarding the name by hash, so the name still never appears in a file.

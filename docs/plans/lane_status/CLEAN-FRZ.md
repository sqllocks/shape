# CLEAN-FRZ: retire the archived reference engine's leftovers

Branch `cleanup/frz-legacy`, cut from `origin/main` c10923be9278bc63f2713ac0ed32a548b59a7afb.
0.9.3 work; it must not reach 0.9.2. No decision, gate, tolerance or recorded number changed.

## Finding up front

FRZ-01 had already removed most of what the brief lists. On main there was no
`setup_refengine.sh`, no `REFENGINE_DEPLOY_KEY` use in any workflow, no deploy-key step in
`scripts/env.sh` and no "Pinned RefEngine baseline" step name. This package therefore removed
only what was still left (below) and did the rename.

## Inventory

| Reference | Where | Class | Action |
|---|---|---|---|
| `setup_refengine.sh` (the script) | none; deleted by FRZ-01 | dead | nothing left to remove |
| `REFENGINE_DEPLOY_KEY`, deploy-key steps | no workflow, no `scripts/` file | dead | nothing left to remove; asserted absent by `tests/benchmarks/test_retired_checks.py` |
| `REFENGINE_*` exports in `scripts/env.sh` | none (FRZ-01) | dead | nothing; the same test asserts it |
| "Pinned RefEngine baseline" CI step names | none; bench-quick/nightly step is "Shape 0.9.1 baseline venv" | renamed already | nothing |
| `run setup_refengine.sh` in error messages of 4 historical verifiers (`demo_1to1`, `scale_1to1`, `simulation_1to1/harness.py`, `writers_1to1`) | benchmarks | stale | reworded to "archived (FRZ-01); historical" |
| `setup_refengine.sh` mention in `domain_1to1/README.md` | benchmarks | stale | reworded |
| 15 `domain_1to1` seed-study and check scripts plus `port.py` | benchmarks | dead | removed (see below) |
| `benchmarks/vs_refengine/` path | workflows, Makefile, scripts, tests, docs, README, CLAUDE.md | renamed | updated to `benchmarks/regression/` |
| `REFENGINE_NAME` | `tests/release/test_refengine_name.py`, `tests/release/test_check_user_facing.py`, `tests/docs_site/test_docs_site.py`, `demo_1to1/differences.py`, `test_retired_checks.py` | leak guard | kept, with its secret |
| the guard string `"setup_refengine.sh"` / `"REFENGINE_DEPLOY_KEY"` in `test_retired_checks.py` | tests | guard | kept: it asserts their absence, so `git grep` finds it by design |
| CHANGELOG, `docs/plans/**` (lane_status, evidence, history text), `benchmarks/baselines/2026-09-29/**` | | historical | left alone |
| `benchmarks/regression/recorded/timings_0.9.1.json` (old path in `command` strings) | recorded evidence | historical | left byte-for-byte: `MANIFEST.json` hashes it |

## Removed and why it was dead

`benchmarks/regression/domain_1to1/`: `bridge_check.py`, `column_miss_check.py`,
`column_moments_check.py`, `fk_marginal_check.py`, `full_seed_study.py`, `pipeline_run.py`,
`pooled_check.py`, `port.py`, `rate_check.py`, `seed_study.py`, `seed_study_analyze.py`,
`seed_study_findings.py`, `seed_study_floor_misses.py`, `seed_study_wide.py`,
`seed_study_wide_stream.py` (2,891 lines). Proof, for each:

- every one reads `paths.REFENGINE_ROOT` / `REFENGINE_PY` or calls `$REFENGINE_PY`, names
  that `paths.py` no longer defines, so none can run;
- `git grep -w <name>` outside `docs/plans`, `CHANGELOG` and `benchmarks/baselines` found no caller:
  no workflow, Makefile, script, test or other harness file. The only mentions were each
  other and the directory README (table rows removed);
- bench-quick, nightly and the recorded-store tools use `generate.py`, `verify.py`,
  `rowcounts.py`, `bench.py`, `bench_cli.py` from that directory, none of which imports them.

Not removed: the other roughly 50 archived-engine verifiers (`*_1to1/verify.py`, baseline
workers, `_refpkg.py`). The harness README and tests (`tests/benchmarks/test_archival_guard.py`,
`historical_imports.py`, and many tests that load these files by path) keep them as historical
goldens, and some are loaded dynamically (`importlib`, e.g. the simulation cases). Deleting
them means deleting or rewriting tests, which is a larger decision than this package.

## Rename map

| From | To |
|---|---|
| `benchmarks/vs_refengine/` (`git mv`) | `benchmarks/regression/` |
| `tests/benchmarks/test_vs_refengine_harness.py` | `tests/benchmarks/test_regression_harness.py` |
| module-name strings `vs_refengine_*` in tests | `regression_*` |

Updated: `.github/workflows/{ci,nightly,container,record-baseline-published}.yml`, `Makefile`,
`scripts/{baseline_diff_report,check_docs_language,gen_performance_page,mkdocs_hooks}.py`,
`tests/**`, `docs/evidence/measure_allocator.py`, `benchmarks/{live_fidelity,measure_product}`,
`CLAUDE.md` (CONTRIBUTING.md, AGENTS.md and mkdocs nav did not mention it; pyproject has no
benchmarks path). The harness README is rewritten to open "compares Shape against its own
recorded previous release". `docs/plans/COMPLETION_PLAN.md`: §1 environment block now matches
`scripts/env.sh`, the pointer in §1.2 follows the rename, and one row is added to the §2.3
decision log. That file has no "Useful commands" section; other `vs_refengine` text in it is
history and is untouched.

## Checks (python 3.11 venv; baseline venv built with `setup_baseline.sh`)

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/regression` | exit 0 |
| `ruff format --check src tests plugins benchmarks/regression` | exit 0 (2064 files) |
| `mypy` | exit 0 (698 files) |
| `python benchmarks/regression/recorded/check.py` | exit 0 (103 files, shape_version 0.9.1) |
| leak-guard tests with `REFENGINE_NAME` set (`test_refengine_name`, `test_check_user_facing`, `test_retired_checks`, `test_archival_guard`) | exit 0, 29 passed, 0 skipped |
| `pytest benchmarks/regression/tests/test_setup_baseline.py` | exit 0, 9 passed |
| `domain_1to1/rowcounts.py` | exit 0 |
| `run.py --quick --out ...` against the recorded store | exit 0 |
| `generate.py` (baseline and Shape) then `verify_1to1/verify.py` | exit 0 |
| `make check` locally | **exit 2, not clean.** Lint, mypy and the script checks passed. pytest: 18,387 passed, 17 failed, all in `tests/docs` (`test_reference_examples` x15, `test_brand`, `test_launch_tutorials`) from `ModuleNotFoundError: shape_healthcare_codes` and `playwright`, which this sandbox does not have. The cargo and heavy-test steps after pytest did not run. CI is the authority for those. |
| `git grep -n -i 'setup_refengine\|REFENGINE_DEPLOY_KEY'` | only the guard in `test_retired_checks.py` and history (CHANGELOG, `docs/plans/**`) |

## CI

`ci.yml` dispatched on `cleanup/frz-legacy` at f11ba7b: run **38433319060**
(https://github.com/sqllocks/shape/actions/runs/38433319060). Result: see the end of this file.

**Result: success, all 26 jobs passed, bench-quick included.** The head after this note adds
only this file, so the run covers the same code.

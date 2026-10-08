# DEMO-CANDIDATE: demo candidate for the 2026-10-07 talk (2026-10-05)

Candidate: `build/main-plan` at **abd33c68** ("INT-21a: E1/E2 decisions (#283 #396 #281)") for the
checks and the rehearsal below. A first annotated tag `demo-2026-10-06` on abd33c68 (tag object
2279ef18) was never published: its push failed with a network disconnect (`send-pack: unexpected
disconnect while reading sideband packet`). **Superseded** by the resolution below: findings F1,
F2 and F3 are fixed on `build/main-plan`, the unpublished local tag was deleted, and
`demo-2026-10-06` is to be created on the commit that lands this update (see "Resolution": its
push is refused with HTTP 403 from this session).

Machine: Linux, 4 cores, Python 3.11.15. `$REFENGINE_ROOT` (pinned 422e78d) was only read.

## §12.7 exit checks

| # | Check | Result |
|---|---|---|
| 1 | `pytest tests/demo` | **green in both kernels**: `SHAPE_KERNEL=rust` 377 passed (7 min 45 s), `SHAPE_KERNEL=python` 377 passed (9 min 5 s); no failure, no skip; header `fabric lane: shape API under test = real`. Dev venv (editable on the candidate's tree, Rust kernel built from the same `rust/` sources), numpy 2.4.6, pyarrow 19.0.1 and pandas 3.0.6 from `tests/demo/fabric/requirements.txt`, deltalake 1.6.6, pyspark 4.2.0, fabric-user-data-functions 1.0.142, unixODBC installed, `REFENGINE_ROOT` set for `tests/demo/content` |
| 2 | DM-01's parity run, `benchmarks/vs_refengine/profile_1to1/verify.py --impl shape` | **exit 0**, every dataset `PASS` (49: D1, D2, D4 csv and parquet, MT, MT parquet, the 20 EDGE and 19 EDGE2 variants, then D3 csv and parquet in a second invocation, because the disk did not hold all datasets at once). Datasets from `datasets.py`, pinned baseline venv for the baseline side, cache cleared before the run |
| 3 | DM-03 wheels in a clean Python 3.11 venv | **pass**, with one finding in the CI smoke script (below). Wheels built as `docs/INSTALL.md` says; venv from `python3.11 -m venv`, wheels only (no editable install), `shape`, `shape_domains`, `shape_fabric` load from the venv's `site-packages` and no `sys.path` entry is in the checkout. `shape doctor`: `Result: OK`, kernel `python` (pure wheel), numpy 2.4.6, pyarrow 25.0.1. `shape conformance`: every check passed, no warning or traceback in the output. Wheel smoke tests (`scripts/ci_pure_wheel.sh`, the CI `pure-wheel` job): see "Wheel smoke tests" |
| 4 | Every number in `demo/TALK.md` cites its source file | **pass after the resolution** (was: not all; see "TALK.md numbers" and "Resolution") |
| 5 | Owner dry run in Fabric (DM-4) | **pending: owner action O-07** (notebook day 1, Environment and PySpark notebook, pipeline (a) failing on day 2, the UDF pipeline (b), `demo/LIVE_TIMINGS.md`) |

### Versions (clean venv)

| Package | Version | From |
|---|---|---|
| `sqllocks-shape` | 0.9.0, `py3-none-any`, 2,787,092 bytes (sha256 `fdb46a23cd7b9acf…`, identical in both builds) | `scripts/build_pure_wheel.py` (checks passed: tag, < 28,600,000 bytes, no compiled code, RECORD valid) |
| `sqllocks-shape-domains` | 0.9.0, 1,303,453 bytes | `pip wheel --no-deps plugins/shape-domains` |
| `sqllocks-shape-fabric` | 0.9.0, 161,064 bytes | `pip wheel --no-deps plugins/shape-fabric` |
| `sqllocks-shape-eventhubs`, `sqllocks-shape-sqlserver` | 0.9.0 (17,503 and 31,480 bytes) | `pip wheel --no-deps`, installed by the fabric extras step |
| numpy, pyarrow, Faker | 2.4.6, 25.0.1, 40.40.0 | PyPI (Faker comes with the domains plugin) |
| pyodbc, azure-eventhub | 5.3.0, 5.15.1 | PyPI, through `sqllocks-shape-fabric[sqlserver,eventhubs]` |

### Wheel smoke tests

| Run | Result |
|---|---|
| `scripts/ci_pure_wheel.sh python3.11` as committed | **exit 1**: step 1 (`tests/integrations` on the installed pure wheel, `SHAPE_KERNEL=python`) 139 passed, 1 skipped, **3 failed**; step 2 did not run (`set -e`). The three are `tests/integrations/test_adf_gate_errors.py` (`test_a_cli_that_writes_nothing_is_an_error_gate`, `test_a_missing_shape_command_is_an_error_gate`, `test_an_unexpected_generation_error_is_an_error_gate`): `ModuleNotFoundError: No module named 'fsspec'` from `integrations/adf/batch/run_gate.py:94`. **Classification: environment gap in the smoke script, not the product or the wheel**: the ADF batch gate (AUD-pluginfw's #366 regression tests) imports `fsspec`, which the script's venv does not install (it is in the `dev` extra) |
| the same script with `fsspec` added to its `pip install` line (a scratch copy, nothing committed) | **exit 0**: step 1 142 passed, 1 skipped; step 2 (`tests/demo/fabric/test_udf.py`, `test_generate_udf.py` against `fabric-user-data-functions`) 46 passed. The skip is the test's own (`delta-spark is a tests/demo/fabric requirement`) |

### TALK.md numbers

Cited and checked against their sources: the day-2 table (`DRIFT.md`: 0.08, 4.97%, 20%, five
values, 6000, 5135.63, 7189.882, x 1.40, 5,000, 5,050), the threshold paragraph's 0.5, +40%,
0.39 and 0.25 (`DRIFT.md`), and the benchmark sheet and the "say exactly this" quotes
(`benchmarks/baselines/2026-09-30-product/product_bench.json`: 0.385 s, 1.963 s, 4.892 s,
4.404 s, 519,131, 509,495, 1,021,983, 22,704, 224, 638, 2,107, 508 MB, 239 ms, 294 ms, 4 cores,
Python 3.11.15; the quotes' 1.9 s and 4.8 s are the truncated 1.96 s and 4.89 s).

Not citing a source file:

| Line | Number | Where it can be found |
|---|---|---|
| 4 | "About 20 minutes live" | the talk length, not a measurement (no source needed, listed for completeness) |
| 17, 82, 93 | `vCores: 8`, "default to 2 vCores" | `integrations/fabric/RUNBOOK.md` section 4 (and plan §12.1); TALK.md does not cite it |
| 30 | UDF size cap "default 50 MB" | `integrations/fabric/RUNBOOK.md` section 9, `src/shape/integrations/fabric/udf.py` (`max_megabytes=50`); not cited |
| 48 | "(0.3876)" | **in no committed source**: `DRIFT.md` says 0.39; nothing else holds 0.3876 |
| 51 | "about 1.1 MB of JSON" | the docstring of `tests/demo/content/test_demo_data.py` and `docs/plans/lane_status/DEMO-REHEARSAL.md`; not cited. Measured here: 1,155,856 bytes for profiles captured `--capture full`, but **542,917 bytes** for the default (safe) capture that TALK's own commands make |
| 94 | Environment Quick mode "about 5 s" | `integrations/fabric/RUNBOOK.md` section 5; not cited |
| 97 | "240 s limit" | `integrations/fabric/RUNBOOK.md` section 6; not cited |

## From-scratch rehearsal (clean venv, wheels only)

Runbook: `demo/rehearse.sh`'s steps, run by a scratch copy whose install follows `docs/INSTALL.md`
and `docs/DEMO.md` as they read now (`pip install --find-links wheels` of the core, domains and
fabric wheels, with no separate `faker` install, to check #309), plus checks of #306, #308,
#310, #311 and #721. Run from a scratch folder outside the checkout, `SHAPE_HOME` in it; only
`demo/make_data.py` and `demo/contracts/` are read from the checkout, by design. Run twice from
scratch (wheels, venv, data): the same exit code at every step, and every output identical apart
from session ids, timestamps, step timings and scratch paths. Generated files are byte-identical
(data, every `.shape`, the HTML reports, the notebook, the charts page, the `.bim`) except the
HTML session report (its session id). The plugin wheels differ in bytes between the two builds
(file timestamps in the zip); the core wheel is byte-identical. Times are run 1, wall-clock seconds.

| # | Command | exit | s | Outcome (and what the docs say) |
|---|---|---|---:|---|
| 1 | `python3.11 scripts/build_pure_wheel.py --out wheels` | 0 | 0.49 | 2,787,092-byte `py3-none-any` wheel, checks passed |
| 2 | `pip wheel -q --no-deps -w wheels plugins/shape-{domains,fabric,eventhubs,sqlserver}` | 0 | 8.05 | four plugin wheels |
| 3-4 | `python3.11 -m venv venv`, `pip install -U pip` | 0 | 5.03 | |
| 5 | `pip install --find-links wheels wheels/sqllocks_shape-*.whl wheels/sqllocks_shape_domains-*.whl wheels/sqllocks_shape_fabric-*.whl` (INSTALL.md) | 0 | 11.91 | installs; dependencies from PyPI |
| 6 | where `shape`, `shape_domains`, `shape_fabric` load from | 0 | 0.02 | the venv's `site-packages`; no `sys.path` entry in the checkout |
| 7-8 | `pip freeze`; `import faker` | 0 | 0.33 | Faker 40.40.0 present without a separate install (#309 fixed) |
| 9 | `shape doctor` | 0 | 0.30 | `Result: OK`, kernel python (pure wheel), optional packages listed as missing |
| 10 | `shape conformance` | 0 | 0.52 | every check passed; **no warning tracebacks** (the #311 symptom is gone; #311 is still open) |
| 11 | the fabric plugin's `optional_plugin` for `shape_sqlserver`, `shape_eventhubs` before the extras | 0 | 0.20 | `MissingPluginError` naming `pip install 'sqllocks-shape-fabric[sqlserver]'` / `[eventhubs]` and `--find-links` (INSTALL.md, #310 fixed) |
| 12 | `shape demo list` | 0 | 0.19 | 4 scenarios, as DEMO.md's table (default rows 100,000 / 50,000 / 50,000 / 200,000) |
| 13 | `shape demo run retail --rows 1000` | 0 | 4.29 | `small scale preset: 21,800 rows`, fidelity 96.7% (DEMO.md: inference `small` is 21,800) |
| 14 | `shape demo init --name here --local-path ./landing` | 0 | 0.18 | profile saved |
| 15 | `shape demo run retail --mode seeding --connection here --rows 1000` | 0 | 0.60 | `small scale preset: 21,750 rows (local)`, 9 tables, `Session: ID` (DEMO.md: 21,750; #304 fixed) |
| 16 | `shape demo status ID` | 0 | 0.16 | Success, 9 artifacts |
| 17 | `shape demo report ID --format html --output report.html` | 0 | 0.16 | page written |
| 18 | `shape demo report ID` | 0 | 0.18 | Markdown report, total rows 21,750 |
| 19 | `shape demo cleanup ID --dry-run` | 0 | 0.18 | 9 `Would remove: file/<table>` |
| 20 | `shape demo cleanup ID` | 0 | 0.20 | 9 `Removed` |
| 21 | `shape demo notebook retail --mode seeding --output retail.ipynb` | 0 | 0.17 | written (`--output`, #305 fixed) |
| 22 | the notebook's install cell | 0 | 0.02 | `%pip install --find-links builtin "sqllocks-shape==0.9.0" "sqllocks-shape-domains==0.9.0" "sqllocks-shape-fabric[eventhubs,sqlserver]==0.9.0"` (#306 fixed) |
| 23 | `shape demo preflight` | 0 | 0.17 | `[OK] Local folder: folder landing writable` |
| 24 | `shape demo run retail --dry-run` | 0 | 2.32 | `large scale preset: 2,180,000 rows`, `~0.4 min` (DEMO.md: inference `large` 2,180,000) |
| 25 | `shape demo run retail --estimate` | 0 | 2.19 | the same estimate |
| 26 | `shape demo run retail --mode seeding --connection here --dry-run` | 0 | 0.36 | `19,625,400 rows (large scale preset)`, `~3.3 min` (DEMO.md: 19,625,400) |
| 27 | `shape demo run retail --mode streaming --max-events 5` | 0 | 0.52 | 5 `customer` JSON lines in event-time order; garbled names (`Liiel`) and `is_active` as text remain (#312, open) |
| 28 | `shape demo run retail --rows 1000 --dry-run` | 0 | 2.36 | `small scale preset: 21,800 rows` |
| 29 | `shape demo run retail --rows 1000 --output all --output-dir charts` | 0 | 4.29 | `retail_charts.html` (19,586 bytes) and `retail_model.bim`; the charts page is byte-identical across the two runs |
| 30 | `shape demo run adventureworks --rows 1000` | 0 | 4.57 | the retail tables, as its description now says (#307 fixed) |
| 31 | `shape demo run healthcare --rows 1000` | 0 | 5.36 | fidelity 98.8%, works without a separate faker install |
| 32 | `shape demo run healthcare --mode streaming --max-events 3` | 0 | 0.55 | 3 `patient` lines (the rehearsal of 2026-10-03 streamed `facility`; DEMO.md's rule, the first table with an event time, holds); garbled first names (`Ruita`, `Drth`, #312) |
| 33 | `shape demo run enterprise --mode seeding --rows 1000 --connection here` | 0 | 0.89 | `small scale preset: 63,585 rows`: retail 21,750, hr 8,035, financial 33,800 |
| 34 | DEMO.md's Python snippet | 0 | 0.43 | 9 artifacts, cleanup `True` |
| 35 | `python demo/make_data.py --out data` | 0 | 3.57 | 8 files, 134.6 MB, row counts as `DRIFT.md` |
| 36 | `shape profile data/day1/orders.parquet -o o1.shape --html o1.html` | 0 | 1.29 | written; the default capture is now **safe** |
| 37 | `shape check o1.shape demo/contracts/orders.json` | **2** | 0.43 | **differs from TALK.md ("exit 0")**: `passed: false`, `not_evaluable` for `order_total` `min` and `max` ("captured safe ... re-profile with --capture full") |
| 38 | `shape profile data/day2/orders.parquet -o o2.shape --html o2.html` | 0 | 1.20 | |
| 39 | `shape check o2.shape demo/contracts/orders.json` | 1 | 0.46 | **differs from TALK.md**: only the `status` `lost` violation; the `order_total` `max` 7189.882 violation is `not_evaluable` |
| 40 | `shape diff o1.shape o2.shape --mean-shift-std 0.25 --min-severity medium` | 0 | 0.99 | `mean_shift` 105.94 to 148.31 and `category_shift` (as TALK.md), plus a `not_evaluable` list and the cosmetic/patch lines on stderr; 1,448 bytes |
| 41 | `shape diff o1.shape o2.shape \| wc -c` | 0 | 0.99 | 542,917 bytes (TALK.md: about 1.1 MB; 1,155,856 with `--capture full` profiles) |
| 42 | `shape diff o1.shape o2.shape --json d.json \| wc -c` | 0 | 1.09 | 0 bytes on stdout, 893,685 in the file (#308's `--json FILE` part fixed) |
| 43-50 | profile and check `customers`, `products`, day 1 and day 2 | 0 / 1 | 0.38-0.65 each | day 1 passes; day 2 fails `email` `max_null_rate` 0.2 and `sku` `unique` (5,000 in 5,050), as TALK.md and DRIFT.md |
| 51 | the notebook beat in Python (`shape.profile`, `check`, `diff`, `to_html`) | 0 | 2.12 | 500,000 x 8; day 1 passes; day 2 fails `status` and `order_total` `max` 7189.882; the diff has `mean_shift` (the API's in-memory profile is a full capture, so the Fabric notebook beats are unaffected) |
| 52 | Fabric function helpers (`shape.integrations.fabric.udf`: `profile_lakehouse_file`, `check_profile`) on a CSV with `email` and `ssn` in a local stand-in lakehouse | 0 | 0.47 | `email`, `ssn` (and the near-unique `amount`): `min`/`max` `null`, `redacted: true`; `check_profile` violations `observed: null, redacted: true`; no raw value anywhere in the default results; `include_raw_values=True` returns them (#721 fixed) |
| 53 | `pip install --find-links wheels 'sqllocks-shape-fabric[sqlserver,eventhubs]'` (INSTALL.md, DEMO.md) | 0 | 3.46 | installs `sqllocks-shape-sqlserver`, `sqllocks-shape-eventhubs`, pyodbc 5.3.0, azure-eventhub 5.15.1; both plugins then load (#310 fixed) |
| 54 | `pytest tests/demo/fabric` (RUNBOOK section 2) | 0 | | inside exit check 1 (dev venv with RUNBOOK section 2's packages, unixODBC): passes in both kernels, real Shape API |
| 55 | RUNBOOK section 2: `build_notebooks.py && ruff format integrations`, `build_pipelines.py` | 0 | 0.14 | the notebooks and pipelines regenerate byte-identical; `ruff format integrations` reformats one unrelated file, `integrations/adf/batch/run_generate_gate.py` (a line over the limit; `integrations/` is outside `make check`'s format scope). Reverted, nothing committed |

### Findings

| # | Severity | Finding | Suggested action (not done here: `demo/` and the talk are not this session's paths) |
|---|---|---|---|
| F1 | **high** (stage fallback); **resolved** | TALK.md's "Nothing works" row is wrong since W1-11 made the CLI's default capture safe: `shape check o1.shape demo/contracts/orders.json` exits **2** (not 0), and day 2 misses the `order_total` `max` violation. With `--capture full` on both `shape profile` lines, day 1 exits 0 and day 2 exits 1 with both `status` and `order_total` violations, as TALK.md says (checked here). `demo/rehearse.sh` runs the same commands and records the exit 2 without failing. The Fabric notebook, pipeline and UDF paths are not affected (they profile through the API, step 51, and pass `tests/demo/fabric`) | Add `--capture full` to the two `shape profile` commands in TALK.md's fallback row and in `demo/rehearse.sh`, and say why (the demo data has no personal values in `order_total`, but the contract needs its minimum and maximum) |
| F2 | medium; **resolved** | `scripts/ci_pure_wheel.sh` fails on the candidate: the ADF gate tests need `fsspec`, which its venv lacks (the CI `pure-wheel` job runs this script) | add `fsspec` to the script's `pip install` line (the run with it is green) |
| F3 | low; **resolved** | TALK.md numbers without a cited source (table above); "0.3876" is in no committed file (correction: it is in `docs/talks/shape-v1/NUMBERS.md` N-74, as a derivation from the profiles); "about 1.1 MB" holds only for full-capture profiles (542,917 bytes with the default capture) | cite RUNBOOK sections for the Fabric limits; drop or source 0.3876; restate the diff size for the commands TALK gives |
| F4 | low | #312 (garbled first names, gender mismatch, `is_active` as text in streaming) still visible in steps 27 and 32 | avoid showing streaming output on stage, or fix #312 |
| F5 | low | `shape check` and `shape diff` print the unsigned-artifact note on stderr for every demo file (TALK.md already warns) | none |
| F6 | low | the plugin wheels are not byte-reproducible between two builds (zip timestamps); the core wheel is | none for the talk |
| F7 | info | #311 (conformance tracebacks) and #314 (charts page determinism) no longer show in the output, but both issues are open (#314's core part, `3.0` keys, is a profile issue) | the lead may close #311 after checking its lane |

## What the owner must still do

- Push the tag `demo-2026-10-06` on the `build/main-plan` head (refused with HTTP 403 from this session; see "Resolution").
- ~~Decide F1 before the talk~~ fixed (see "Resolution"); the original note read: F1's fix is a two-flag change to
  TALK.md.
- The Fabric dry run (O-07, DM-4): §12.7 check 4 and `demo/LIVE_TIMINGS.md`.
- #303 (the PyPI `sqllocks-shape` 0.9.0 has no `shape demo`, and the branch wheels use the same
  version) is still open; the notebooks install with `--find-links builtin` from uploaded wheels.

## Resolution (2026-10-05, after the candidate run)

Branch `demo/fix-candidate-findings` from `origin/build/main-plan` 6df305e4, merged `--no-ff` into
`build/main-plan`. Dev venv as exit check 1 (editable on this tree; numpy 2.4.6, pyarrow 19.0.1,
pandas 3.0.6).

| Finding | Fix | Check, in this session | Result |
|---|---|---|---|
| F1 | `--capture full` on the two `shape profile` commands of TALK.md's "Nothing works" row (now written out in full from the repository root, with one sentence saying why: the demo data is synthetic, and the contract's `order_total` `min` and `max` need real values that safe capture withholds), of `demo/rehearse.sh` (which now also fails if the fallback checks do not exit 0 and 1), of `docs/talks/shape-v1/DEMO.md` C2-local and of its `verify_snippets.sh`; `demo/DRIFT.md` gains "On the command line" with the commands and their outputs. RUNBOOK has no CLI profile commands (its paths go through the API, unaffected) | new `tests/demo/content/test_demo_data.py::test_talk_local_fallback_runs_as_written`: parses the row's commands from TALK.md and runs them as written (`python demo/make_data.py --out data`, both profiles, both checks, then the stage diff and a plain diff) | day 1 exit 0, `{"passed": true, "violations": []}`; day 2 exit 1, violations exactly `status` `allowed_values` (`lost`) and `order_total` `max` (7189.882); stage diff has `mean_shift`; passes in both kernels |
| F2 | `scripts/ci_pure_wheel.sh` installs fsspec with the requirement of the project's `[dev]` extra (`fsspec>=2024.2`, read from `pyproject.toml`): `run_gate.py` imports `fsspec` (local paths in the tests, so `adlfs` is not needed) | `bash scripts/ci_pure_wheel.sh python3.11`, end to end | **exit 0** (3 min 55 s): wheel 2,787,092 bytes `py3-none-any`; step 1 `tests/integrations` 142 passed, 1 skipped (the test's own `delta-spark` skip); step 2 UDF tests 46 passed |
| F3 / check 4 | every uncited number now cites a committed file (table below); platform limits recorded in the new `integrations/fabric/RUNBOOK.md` section 1.1 with the Microsoft Learn pages they come from (read 2026-10-05); "about 20 minutes" (in no committed file) replaced by a pointer to `docs/talks/shape-v1/OUTLINE.md`; "about 1.1 MB" replaced by the measured 1,155,856 bytes of the plain diff (of the fallback's `--capture full` profiles) and 942 bytes of the stage diff TALK tells the presenter to run; 0.3876 traced (commit c1b7dabb, NUMBERS.md N-74) and its derivation recorded in `DRIFT.md` | `test_talk_local_fallback_runs_as_written` asserts both byte counts against TALK.md and DRIFT.md; new `test_mean_shift_in_baseline_std_is_the_documented_figure` recomputes 0.3876 from the day-1 and day-2 profiles and checks the derivation text | pass, both kernels |

TALK.md numbers that had no cited source, and their sources now:

| Number | Source cited in TALK.md |
|---|---|
| 2 vCores (default), 8 vCores (set) | `integrations/fabric/RUNBOOK.md` section 1.1 (Microsoft Learn, "Use Python experience on Notebook": "2vCores/16GB memory by default"; `%%configure` `vCores`) and section 4, step 4 |
| UDF size cap 50 MB | `max_megabytes=50` in `src/shape/integrations/fabric/udf.py`; RUNBOOK section 1.1 (Shape's own default, not a platform limit) |
| Quick mode about 5 s; Full mode minutes | RUNBOOK section 1.1 (Microsoft Learn, "Manage libraries in Fabric environments": "Publish completes in about 5 seconds"; Full "3 to 6 minutes") |
| UDF 240 s limit | RUNBOOK section 1.1 (Microsoft Learn, user data functions "Service limits": request execution timeout 240 seconds) |
| plain diff 1,155,856 bytes (was "about 1.1 MB"); stage diff 942 bytes | `demo/DRIFT.md`, "On the command line" (stdout of the commands on the fallback's profiles; tested) |
| 0.3876 | `demo/DRIFT.md`: (148.3128 − 105.9377) / 109.3338, the means the stage diff prints and the day-1 `std` in `o1.shape` (tested) |
| "About 20 minutes live" | removed: in no committed file; TALK.md points at `docs/talks/shape-v1/OUTLINE.md` for timing |

Checks on the final tree:

| Command | Result |
|---|---|
| `SHAPE_KERNEL=rust pytest tests/demo` | **379 passed** (8 min 35 s), exit 0; 377 before plus the two new tests |
| `SHAPE_KERNEL=python pytest tests/demo` | **379 passed** (9 min 37 s), exit 0 |
| `ruff check` on `make check`'s scope | all checks passed |
| `ruff format --check` on the same scope | 1990 files already formatted |
| `python scripts/check_user_facing.py` | clean |
| `bash scripts/ci_pure_wheel.sh python3.11` | exit 0 (above) |
| `bash -n` on `demo/rehearse.sh`, `scripts/ci_pure_wheel.sh`, `docs/talks/shape-v1/verify_snippets.sh` | ok; `rehearse.sh`'s new exit-code check tested on a passing and a failing `steps.tsv` (exit 0 and 1). The full `rehearse.sh` was not rerun (disk: it needs a fresh venv and 136 MB of data; the commands it runs are the ones the new test runs) |

Not changed: `docs/talks/shape-v1/DEMO.md` C2-local still says "The CLI `diff` has no threshold
flag"; `shape diff` has `--mean-shift-std` now (left for the talk's owner).

Tag: the unpublished local `demo-2026-10-06` (object 2279ef18 on abd33c68) was deleted after
`git ls-remote --tags origin demo-2026-10-06` returned nothing, and the annotated tag
`demo-2026-10-06` ("Demo candidate for the 2026-10-07 talk") is created locally on the
`build/main-plan` commit that lands this update. **Its push is refused by the remote with HTTP
403** (`error: RPC failed; HTTP 403 curl 22 The requested URL returned error: 403`, then
`send-pack: unexpected disconnect while reading sideband packet`), on 2026-10-05, while branch
pushes from the same session go through: a permission refusal of the tag push, not a network
fault (the first attempt's log kept only the `send-pack` line, so its cause is not recorded). `git ls-remote --tags
origin demo-2026-10-06` returns nothing. The owner or the lead pushes it:
`git tag -a demo-2026-10-06 -m "Demo candidate for the 2026-10-07 talk" <build/main-plan head>
&& git push origin refs/tags/demo-2026-10-06`.

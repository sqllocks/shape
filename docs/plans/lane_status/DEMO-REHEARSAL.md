# DEMO-REHEARSAL: full demo rehearsal, 2026-10-03 (talk 2026-10-07, wheel freeze 2026-10-06 midday EDT)

Branch `lane/DEMO-REHEARSAL`, from `int/INT-16` at `c153e9c`. Rehearsed as a presenter would, on Linux
(4 cores, Python 3.11.15) with no Fabric or Event Hubs credentials:

- `docs/DEMO.md`: `shape demo`, every scenario and mode, the Python API;
- `demo/TALK.md`: the talk kit, meaning the data, the "Nothing works" local fallback, the beats' API
  calls, the drift table and the benchmark sheet;
- the offline half of `integrations/fabric/RUNBOOK.md` (section 2).

**Result: every scripted step runs and gives what the docs say, after the fixes below.** I found 14
discrepancies:

- 6 are fixed on this branch, each with a failing test first;
- 5 are filed only, because they are outside the demo lane's paths;
- 3 are notes.

Two runs from scratch (fresh venv, wheels rebuilt) gave identical output. A third run through the
committed `demo/rehearse.sh` matched them too.

## How it was installed (as docs/INSTALL.md, with this branch's wheels instead of PyPI)

```bash
python3 scripts/build_pure_wheel.py --out RUN/wheels                 # sqllocks_shape-0.9.0-py3-none-any.whl, 1.9 MB
pip wheel -q --no-deps -w RUN/wheels plugins/shape-domains plugins/shape-fabric \
    plugins/shape-eventhubs plugins/shape-sqlserver
python3.11 -m venv RUN/venv && RUN/venv/bin/python -m pip install -q --upgrade pip
RUN/venv/bin/pip install -q RUN/wheels/*.whl
RUN/venv/bin/pip install -q faker                                    # healthcare inference (#309)
```

Every command ran from a scratch working folder outside the checkout, with `SHAPE_HOME` set to a
scratch folder. `demo/make_data.py` and the contracts are read from the checkout by design (talk
preparation). The whole sequence is `demo/rehearse.sh RUN_DIR`.

Also checked:
- **Platform (Rust) wheel.** `maturin build --release` gives `sqllocks_shape-0.9.0-cp311-abi3-manylinux_2_34_x86_64.whl`.
  In a clean venv, `shape doctor` reports `kernel rust`. Day 1 passes the orders contract, day 2
  fails it the same way, and the summary equals the pure wheel's.
- **Fabric lane offline.** `pytest tests/demo/fabric` (RUNBOOK section 2, real Shape API, unixODBC
  installed): 234 passed.

## Runbook: commands, timings, outputs

Timings are wall-clock seconds for the final two runs from scratch. Output is identical between runs
apart from session ids and timestamps. Steps 7 to 30 follow `docs/DEMO.md`; steps 31 to 46 follow
`demo/TALK.md`.

| # | Command | exit | run 1 s | run 2 s | What it printed (and what the docs say) |
|---|---|---|---:|---:|---|
| 1 | `python scripts/build_pure_wheel.py --out wheels` | 0 | 0.34 | 0.36 | checks passed: py3-none-any, < 28.6 MB, no compiled code |
| 2 | `pip wheel --no-deps` of the 4 plugins | 0 | 8.44 | 8.71 | |
| 3–6 | venv, pip, install wheels, faker | 0 | 12.0 | 11.5 | |
| 7 | `shape doctor` | 0 | 0.27 | 0.22 | `Result: OK`, kernel python (pure wheel) |
| 8 | `shape conformance` | 0 | 0.42 | 0.45 | 32/32 passed, preceded by 6 warning tracebacks with temp paths (#311) |
| 9 | `shape demo list` | 0 | 0.12 | 0.14 | 4 scenarios, matches the DEMO.md table |
| 10 | `shape demo run retail --rows 1000` | 0 | 3.72 | 4.13 | `small scale preset: 21,800 rows`, `Fidelity score: 96.7%`, 60-row report (`address.city` and `product_category.category_name` FAIL) |
| 11 | `shape demo init --name here --local-path ./landing` | 0 | 0.13 | 0.11 | profile saved |
| 12 | `shape demo run retail --mode seeding --connection here --rows 1000` | 0 | 0.54 | 0.52 | `small scale preset: 21,750 rows (local)`, `retail: 21,750 rows in 9 tables`, `Session: ID` |
| 13 | `shape demo status ID` | 0 | 0.14 | 0.17 | Success, 9 artifacts |
| 14 | `shape demo report ID --format html --output report.html` | 0 | 0.11 | 0.14 | 1 KB page, total 21,750 rows |
| 15 | `shape demo report ID` | 0 | 0.15 | 0.14 | Markdown report |
| 16 | `shape demo cleanup ID --dry-run` | 0 | 0.17 | 0.13 | 9 `Would remove: file/<table>` |
| 17 | `shape demo cleanup ID` | 0 | 0.13 | 0.14 | 9 `Removed`, session folder gone |
| 18 | `shape demo notebook retail --mode seeding --output retail.ipynb` | 0 | 0.13 | 0.13 | install cell `%pip install --find-links builtin ...` |
| 19 | `shape demo preflight` | 0 | 0.13 | 0.13 | `[OK] Local folder: folder landing writable` |
| 20 | `shape demo run retail --dry-run` | 0 | 2.30 | 2.08 | `large scale preset: 2,180,000 rows`, `~0.4 min` (learns the schema to count, hence 2 s) |
| 21 | `shape demo run retail --estimate` | 0 | 2.39 | 2.33 | as 20, without the plan line |
| 22 | `shape demo run retail --mode seeding --connection here --dry-run` | 0 | 0.34 | 0.31 | `Would write 19,625,400 rows (large scale preset) to: local`, `~3.3 min` |
| 23 | `shape demo run retail --mode streaming --max-events 5` | 0 | 0.49 | 0.40 | 5 JSON lines of `customer` in event-time order (data oddities #312) |
| 24 | `shape demo run retail --rows 1000 --dry-run` | 0 | 2.14 | 2.16 | `small scale preset: 21,800 rows` |
| 25 | `shape demo run retail --rows 1000 --output all --output-dir charts` | 0 | 3.98 | 4.05 | `retail_charts.html` (20 KB, no external assets) and `retail_model.bim` |
| 26 | `shape demo run adventureworks --rows 1000` | 0 | 4.19 | 4.04 | identical to retail (#307) |
| 27 | `shape demo run healthcare --rows 1000` | 0 | 4.83 | 4.91 | 21,262 rows, 98.8%; **exit 1 without faker** (#309) |
| 28 | `shape demo run healthcare --mode streaming --max-events 3` | 0 | 0.44 | 0.42 | 3 `facility` lines |
| 29 | `shape demo run enterprise --mode seeding --rows 1000 --connection here` | 0 | 0.76 | 0.76 | `small scale preset: 63,585 rows`, retail 21,750, hr 8,035, financial 33,800 |
| 30 | DEMO.md Python snippet (`demo_run`, `demo_status`, `demo_cleanup`) | 0 | 0.48 | 0.45 | 9 artifacts, cleanup ok (held in memory: nothing written) |
| 31 | `python demo/make_data.py --out data` | 0 | 3.72 | 3.71 | 8 files, 134.6 MB; row counts as in DRIFT.md |
| 32 | `shape profile data/day1/orders.parquet -o o1.shape --html o1.html` | 0 | 0.92 | 1.00 | content id `6896909a…` both runs |
| 33 | `shape check o1.shape demo/contracts/orders.json` | 0 | 0.44 | 0.36 | `{"passed": true, "violations": []}` + unsigned note on stderr |
| 34 | same for day 2 (`o2.shape`) | 0 | 0.91 | 0.87 | |
| 35 | `shape check o2.shape demo/contracts/orders.json` | **1** | 0.50 | 0.41 | `status allowed_values` (`lost`), `order_total max` 7189.882; matches TALK beat 6 |
| 36 | `shape diff o1.shape o2.shape --mean-shift-std 0.25 --min-severity medium` | 0 | 0.83 | 0.90 | 670 bytes: `mean_shift` 105.94 → 148.31, `category_shift` |
| 37 | `shape diff o1.shape o2.shape \| wc -c` | 0 | 1.07 | 0.76 | **1,155,366 bytes** (#308) |
| 38–45 | profile and check `customers`, `products`, day 1 and day 2 | 0/1 | ≤ 0.53 each | ≤ 0.51 each | day 1 passes; day 2 fails `email max_null_rate` 0.2 and `sku unique` (5,000 in 5,050); matches TALK and DRIFT |
| 46 | notebook beat in Python (`shape.profile/check/diff`, `to_html`) | 0 | 1.63 | 1.47 | 500,000 rows × 8 columns; pass, fail, drifted with the DRIFT.md kinds |

Before the fixes, the DEMO.md quick-start seeding line `shape demo run retail --mode seeding
--connection here` (no `--rows`) printed `100,000 rows (local)`. It then wrote 19,625,400 rows and
368 MB in 76.9 s, with no progress in between. The same command's `--dry-run` said `100,000 rows` and
`~0.2 min`. `shape demo run retail` (inference, default rows) now says and generates 2,180,000 rows in
6.9 s, with a fidelity of 81.7%: lower than the 96.7% at `--rows 1000`. Quote the small run on stage.

## Not runnable here (need Fabric or Event Hubs credentials), and what was checked offline

| Step | Why not | Checked offline |
|---|---|---|
| TALK beats 2–7 (Python notebook, Environment, PySpark notebook, pipelines, UDF) and RUNBOOK sections 3–7 and 11 | needs a Fabric workspace (DM-4, owner dry run) | `pytest tests/demo/fabric`: 234 passed against the real API (notebooks executed with a local lakehouse, UDF functions, pipeline JSON, `_inlineInstallationEnabled`). Wheel `py3-none-any` 1.9 MB is under the UDF's 28.6 MB limit. The platform wheel builds and gives the same summary (content id differs; note 3) |
| `demo/LIVE_TIMINGS.md` | owner dry run only | still the marked placeholder; nothing filled in |
| `shape demo` Lakehouse, Warehouse, SQL DB, Eventhouse targets; `--scale-mode spark`; `preflight` against them | no credentials | `init` with a Lakehouse saves; `preflight` fails fast (0.35 s, exit 1, `needs azure-identity: pip install 'sqllocks-shape-fabric[entra]'`); seeding fails the same way; a connection string with a password is refused (exit 2). The dry run plans `--scale-mode spark` above 500,000 rows. With `--rows 600000` it now plans 379,600,400 rows (`xlarge`), `~189.8 min`, where it used to say 600,000 |
| The `shape demo notebook` notebook in Fabric | no workspace | the notebook is valid nbformat and identical bytes on both runs; the install cell uses `--find-links builtin` (#306) |
| Event Hubs streaming | no namespace | the demo's streaming mode writes JSON lines to stdout only; it touches no Event Hubs |

## Discrepancies

### Fixed on this branch (test first; tests in `tests/demo_cmd/test_rehearsal.py` and `tests/demo/content/test_demo_data.py`)

| # | Issue | Severity | What was wrong | Fix |
|---|---|---|---|---|
| 1 | #304 | high | Progress, `--dry-run` and `--estimate` printed `--rows` as if it were the count. Examples: `100,000 rows` when 19.6M were written; `1,000 rows (approx)` when 21,800 were; the streaming estimate counted 100,000 | They now print the preset's real rows. Seeding counts from the domain schema, streaming from `small`, and inference from the schema it learns. The DEMO.md quick start uses `--rows 1000`. DEMO.md has a table of preset sizes, enforced by a test |
| 2 | #305 | high | `shape demo notebook ... -o retail.ipynb` (DEMO.md) exits 2: there is no `-o` | DEMO.md uses `--output`. A test parses every `shape demo` line in DEMO.md with the real CLI |
| 3 | #306 | medium | The generated notebook `%pip install`s plugins that are not on PyPI | `--find-links builtin` plus a comment naming the wheels to upload |
| 4 | #307 | medium | `adventureworks` is described with DimCustomer/FactSalesOrder tables but generates the retail tables | The description says what it is; a test checks descriptions against the generated tables |
| 5 | #314 | medium | The comparison page (`--output charts`) differed between runs (tie order depended on the hash seed), and showed 0% overlap for integer columns (`3` vs `3.0`) | Stable tie order, integral float keys normalized; the page is identical under two `PYTHONHASHSEED` values (test) |
| 6 | #308 (talk part) | medium | A plain `shape diff` of the two days prints 1.1 MB to the terminal; TALK.md gave no command to run | TALK.md gives `shape diff o1.shape o2.shape --mean-shift-std 0.25 --min-severity medium` (670 bytes, shows `mean_shift`), and its "Nothing works" row names the day-1/day-2 paths and the expected exit codes (content test) |

DEMO.md also says now:
- install from the release's wheels, not from PyPI (#303, #310);
- the fabric plugin needs the Event Hubs and SQL Server plugins and unixODBC;
- healthcare inference needs faker (#309).

### Filed only (outside `src/shape/demo/`, `demo/`, `docs/DEMO.md`)

| Issue | Severity | Area | Summary |
|---|---|---|---|
| #303 | **high** | release / packaging | PyPI's `sqllocks-shape==0.9.0` (158 files, no `shape.demo`) and the branch wheel (512 files) share version 0.9.0. `pip install sqllocks-shape` from INSTALL.md has no `shape demo`. The Fabric `%pip install --find-links builtin "sqllocks-shape==0.9.0"` can resolve to either. **Decide before the freeze**: bump the version, or add `--no-index` to the notebooks |
| #308 | medium | `shape diff` | `new_categorical_values` lists every day-2 `order_total` value (1.1 MB); `--json FILE` still prints everything to stdout |
| #309 | medium | `learn` / extras | learned schemas use faker strategies; faker is in no user extra; healthcare inference fails in a clean install |
| #310 | medium | plugins / PyPI | the plugins are not on PyPI; the fabric plugin hard-requires eventhubs and sqlserver (pyodbc, unixODBC) |
| #311 | low | `shape conformance` | 6 `ArtifactNotVerifiedWarning` tracebacks with random temp paths (the only output that differs between runs) |
| #312 | low | domains plugin | stream data: garbled first names (`Liiel`, `Niee`), gender mismatch, `is_active` as a string, fractional `bed_count`, no event time on `facility` |
| #314 (core part) | low | profile of generated data | integer values keyed `3.0` in `enum_values` |

### Notes (no issue filed)

1. **Timing of the estimate.** `--estimate` for seeding 19.6M rows locally says `~3.3 min`; the
   measured run took 76.9 s. Its warning "use the chunked scale router" names nothing a user can
   act on. The inference `--dry-run` now takes about 2 s, because it profiles the source to count
   the rows.
2. **The unsigned note.** `shape check` and `shape diff` print `note: ... is not signed` on stderr
   for every demo `.shape`. TALK.md now tells the presenter to expect it.
3. **Pure versus Rust content ids.** The two kernels give `.shape` files with different content ids
   for the same table: `order_total.distribution_params` differ in the last bit (`loc`, `s`).
   Summaries, checks and diffs are identical. A day-1 baseline made with one kernel still diffs
   cleanly against the other.
4. **The local platform wheel** is `manylinux_2_34` (this machine's glibc 2.39). The release CI's
   wheel is what goes to Fabric.

## Determinism

Run 1 and run 2 were each built from scratch: wheels, venv, `SHAPE_HOME`, data. The third run used
`demo/rehearse.sh`.

- **Text output:** all 46 step outputs are identical after replacing session ids, timestamps, step
  timings and the scratch paths. `shape conformance`'s temp file names are the only remaining
  difference (#311).
- **Generated files:** byte-identical. That covers the 8 data files, all `.shape` files (orders
  content id `6896909a2d04…`), both HTML profile reports, the notebook, the charts page (after fix 5;
  before it, the charts page differed in the second pair of runs), the `.bim` model, and the HTML
  session report up to its session id.
- **Not deterministic by design:** session ids (random) and the `Started`/`Finished` timestamps.

## Checks run in this session (final code)

- `ruff check src tests plugins benchmarks/vs_spindle`: all checks passed.
- `ruff format --check` (same paths): 1345 files already formatted.
- `mypy`: no issues in 476 files. `mypy --strict src/shape/demo`: no issues.
- `scripts/check_user_facing.py`, `check_secrets.py`, `check_shipped_data.py`: exit 0.
- `pytest tests/demo tests/demo_cmd -m "not emulator and not live"`: 502 passed (fabric lane: real
  API; `tests/demo/content` alone: 39 passed).
- Full suite, rust kernel (`SHAPE_KERNEL=rust pytest -m "not emulator and not live"`, dev venv
  `pip install -e ".[dev]"` with every plugin, plus `tests/demo/fabric/requirements.txt`): 8744 passed,
  17 skipped, **14 failed** in 32 min. None of the 14 is in the code this branch changes:
  - 13 fail the same way on the base commit `c153e9c`, run in the same venv with a worktree.
    - 9 are `tests/generation/test_composite_p601e.py`: the pinned Spindle baseline
      (`sqllocks_spindle`, `$SPINDLE_ROOT`) is not installed in this session.
    - 4 are `tests/kernel/test_hashing.py` (float16, `1 == 1.0`),
      `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`
      and `tests/streaming/emit/test_faults.py::test_emit_to_two_files`. The venv has pyarrow
      19.0.1: the Fabric test requirements (deltalake, delta-spark) pulled it down from the 25.x
      that CI uses.
  - 1, `tests/plugins/test_plugin_kit_install.py::test_every_skeleton_builds_a_pure_wheel`, was
    caused by this session's `pip wheel plugins/...`, which left `plugins/*/build/` behind. With
    those removed it passes: the file gives 10 passed. `demo/rehearse.sh` builds the same way, so
    after running it, delete `plugins/*/build/` before running the suite.

  No test was skipped, deselected or weakened.

Before any fix, the new tests failed: 9 of the first set, then the inference-plan test, then the 3
charts tests and the TALK stage-diff test. All pass after the fixes.

## Paths changed

`src/shape/demo/` (`modes/common.py`, `modes/inference.py`, `modes/seeding.py`, `modes/streaming.py`,
`notebook.py`, `catalog.py`, `charts.py`), `docs/DEMO.md`, `demo/TALK.md`, `demo/rehearse.sh` (new),
`tests/demo_cmd/test_rehearsal.py` (new), `tests/demo/content/test_demo_data.py` (one test). Nothing
under `$SPINDLE_ROOT`, `.github/workflows` or the plan's tables.

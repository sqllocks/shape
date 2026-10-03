# AUD-tests — test suite quality, flakiness, speed

Branch: `lane/AUD-tests` (from `build/main-plan`). Area: the test suite (`tests/`). No gate,
tolerance or decision was changed; no test was skipped, xfailed, deselected or weakened, and
no existing assertion changed. `.github/workflows` was not edited: the workflow changes this
lane needs are the exact diffs below.

## Findings

| # | severity | issue | defect | status |
|---|---|---|---|---|
| 1 | high | #331 | CI `test` and `zero-network` jobs red: (a) `tests/demo_cmd/test_notebook_and_outputs.py` semantic-model tests need `plugins/shape-fabric`, which those jobs do not install; (b) the module-wide autouse fixture of `tests/profile/test_delta_fallback.py` downloads DuckDB's `delta` extension, so under `unshare --net` every test of the module errors | (b) suite part fixed 49630e0 (the fixture is used only by the 20 tests that read through DuckDB; under no network the other 18 pass). (a) and the rest of (b) need the workflow diff below |
| 2 | high | #328 | `test_executors_on_the_python_kernel_give_the_same_profile` got the running session back from `getOrCreate()` (static conf ignored, so executors never ran the Python kernel) and then stopped the module's shared session | fixed b5c1080 (regression test a0723af): the Python-kernel executors run in their own process. Low part (fixtures left `PYSPARK_PYTHON` set) fixed b4fea80 |
| 3 | medium | #329 | generation strategy tests edited schemas that share dicts with the module-level `CASES`, so later tests saw the edits (order-dependent failures) | fixed 5460ea9 (regression test bcc9909): the tests deep-copy the case schema |
| 4 | medium | #330 | the drift-sweep size test got the cached `test_drift_sweep` module and its size check never ran when the doc test ran first | fixed 4ca1708 (regression test 72d6836) |
| 5 | medium | #332 | tests marked `zero_network` download from the internet through native code (JVM Maven resolution for Delta, DuckDB `INSTALL delta`), and one wrote `$BENCH_OUT_DIR/simulation_1to1/results.json` | the `$BENCH_OUT_DIR` write fixed 24a84bc (regression test ca6ea55). The downloads are left open: they need CI pre-fetching (diff below) or a `network` marker in `pyproject.toml` (outside this lane's paths) |
| 6 | medium | #333 | three tests fail on pyarrow 19.0.1, the Fabric UDF pin T-07 admits, although the code under test works there | fixed 6f4efb2 (the existing tests are the regression tests; assertions unchanged) |
| 7 | medium | #334 | the two `shape_databases` tests in `tests/` (`tests/cli/test_generate_to.py:156`, `tests/streaming/emit/test_table_sink.py:228`) are skipped in every CI job | open: needs the workflow diff below (the `database-plugins` job runs those two files) |
| 8 | medium | #335 | Delta Spark tests fail when a plain Spark session started the JVM first (one JVM per process; `spark.jars.packages` only applies at JVM start) | fixed 6b3a164 (regression test 68b4fad) |
| 9 | low | #77 | the cloud-SDK import check of credential-reference resolution read the shared `sys.modules`, so it failed after the Fabric demo tests | fixed 82ce26a (regression test 4ec238c): checked in a fresh interpreter |

Improvements (behaviour-preserving, one commit each):

| commit | change |
|---|---|
| 3c7274a | tests for `shape.quality.evaluate` (`quality/core.py` 50% -> 100%) |
| 84dce78 | tests for `shape.privacy.assess_summary` (`privacy/core.py` 60% -> 100%) |
| 2aea0be | tests for `shape.spec` save/load JSON and YAML round trips (`spec/io.py` 42% -> 87%; the rest is the missing-PyYAML branch) |
| 0d48767 | tests for the GeoNames and Census Gazetteer loaders (`location/reference.py` 52% -> 100%) |
| 4f7374d | tests for `DatetimeProfile` update and merge (`profile/datetime.py` 44% -> 100%) |
| 0d551af | the T-22 dtype parity test of `infer_column_type` is marked `heavy` (37 s alone, 133 s under coverage, the slowest default test after the soak); CI's heavy step `pytest -m heavy tests/kernel tests/profile tests/streaming` still runs it, unchanged |

## Workflow changes needed (exact diff, for the lead; not applied)

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ jobs: test: steps
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
+      # #331: the demo semantic-model tests (tests/demo_cmd) need the shape-fabric plugin.
+      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
@@ jobs: zero-network: steps
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
+      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
+      # #331/#332: DuckDB's delta extension is a download; fetch it (as the user the tests run as)
+      # before the network is removed, so the delta-fallback tests read it from the local cache.
+      - name: Pre-fetch DuckDB's delta extension
+        run: sudo env "PATH=$PATH" python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL delta'); c.execute('LOAD delta')"
       - name: Prove the namespace has no route out
@@ jobs: database-plugins: steps
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev]' -e plugins/shape-databases
-      - run: python -m pytest -q -m "not emulator and not live" plugins/shape-databases/tests
+      - run: pip install -e '.[dev,streaming]' -e plugins/shape-databases
+      # #334: the two tests in tests/ that need shape-databases run here, the only job with it.
+      - run: python -m pytest -q -m "not emulator and not live" plugins/shape-databases/tests tests/cli/test_generate_to.py tests/streaming/emit/test_table_sink.py
       - run: python -m shape.plugins.kit sqllocks-shape-databases
```

`plugins/shape-fabric` depends on `sqllocks-shape-eventhubs` and `sqllocks-shape-sqlserver` at
the same version, which are not on PyPI at that version, so they are installed from the tree.

## Commands and results (this session)

In progress: `make check` and the full suite in both kernel modes are running; results follow in the next commit.

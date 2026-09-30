# DM-05b — Fabric Environment and PySpark notebook (L2)

- Status: done locally (Environment publish and Runtime 2.0 behaviour unverified live)
- Commit: 578e4f4
- Shape API used: test-only stub (see DM-05.md)

## Acceptance commands run

```
cd tests/demo/fabric && python -m pytest test_notebooks.py -q -p no:cacheprovider
18 passed  (includes 3 Spark tests: pyspark 4.2.0 + delta-spark, local[1])
```

- Spark notebook exit JSON equals the Python notebook's (minus artifactPath) on day 1 and day 2.
- Sampling test lowers DRIVER_ROW_LIMIT: `sampled: true`, `rows` stays the true count.

## Deviations / notes for the lead
- §12.2 has `profile(source, *, name=None)` with no `exact` argument. The Spark notebook passes `exact=True` only if the installed `shape.profile` accepts it.
- `pyspark` and `delta-spark` must be added to `[dev]` in pyproject.toml (L1 owns it); meanwhile `tests/demo/fabric/requirements.txt` lists them.
- environment.yml has nothing active by default: Runtime 2.0's bundled numpy/pyarrow/pandas versions could not be checked offline; README step 7 verifies in the workspace.

# BUGS-stream-2 — lane status

Branch `lane/BUGS-stream-2` (from `build/main-plan`).

## #166 — CloudEvents `time` is RFC 3339 with an offset (fixed)

`_wrap` in `src/shape/streaming/emit/formats.py` writes the envelope `time` through `_rfc3339`:
a zone-less timestamp gets `Z` (zone-less event times are UTC), a date becomes `T00:00:00Z`, and a
zoned timestamp keeps its offset (it is already written as UTC `Z`). `data`, `_shape_event_time`
and the flat envelope are byte-unchanged.

Tests (`tests/streaming/emit/test_formats.py`): the zone-less expectation of
`test_cloudevents_envelope` is now `2024-01-02T03:04:05.678901Z` (comment cites #166), plus
`test_cloudevents_time_of_a_date_column` and `test_cloudevents_time_of_a_zoned_column`. Before the
fix `test_cloudevents_envelope` and the date test failed; the zoned test already held and guards
the behaviour. No other assertion was changed.

## Commands and results

| Command | Result |
|---|---|
| `pytest tests/streaming/emit/test_formats.py` | 10 passed |
| `"$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify_files.py` (all cases; CloudEvents `time` is form-checked in `files_case_stream_emit.py`) | ALL PASSED, exit 0 |
| `make check` (every step) | exit 0 |
| `python scripts/check_user_facing.py` | clean |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 7065 passed, 4 failed, 19 errors (see below) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 7084 passed, 4 failed, 0 errors (the same 4 as below; Java option unset) |

No equivalence verifier for kernel, generation or profile applies: the change is in the event
encoder only, so no timing was taken.

## Environment notes

Plugins installed editable (`shape-sqlserver` and `shape-fabric` with `--no-deps`, as the
`==0.9.0` pins do not resolve from the index), `tests/demo/fabric/requirements.txt`,
`scikit-learn`, and unixODBC. The sandbox sets `JAVA_TOOL_OPTIONS`, whose banner breaks the Spark
gateway; the Spark tests are run with it unset.

## Workflow diffs

None: `.github/workflows` was not touched.

## Rust-kernel run: failures not caused by this change

- 19 errors in `tests/demo/fabric/test_generate_synapse.py` (Spark gateway exit from the
  `JAVA_TOOL_OPTIONS` banner); with it unset, that file and `tests/security/test_credential_refs.py`
  pass (69 passed).
- `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`,
  `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]` and
  `::test_one_and_one_point_zero_hash_equal` fail identically on the base commit `5c91ea5`
  (installed pyarrow is 19.0.1). Not touched here; no test was skipped or changed.

## Python-kernel run

The same four tests fail as in the Rust-kernel run (the three that also fail on the base commit,
plus `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`).
The last one passes when run alone on the base commit and on this branch; in the full run it sees
`azure.functions` already imported (installed from `tests/demo/fabric/requirements.txt`). I did not
confirm whether it fails in a full run on the base commit, so it is open, not shown to be
pre-existing. No test was skipped, deselected or changed.

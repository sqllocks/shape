# AUD-fabric: audit of the Fabric, Event Hubs and Kafka plugins (lane/AUD-fabric)

Status: **done; two findings wait for the lead (#425, #429), one half-finding is open (#445 duration).** Area: `plugins/shape-fabric/**`, `plugins/shape-eventhubs/**`,
`plugins/shape-kafka/**`, and the `docs/plugins/**` pages for them. Every finding below was
reproduced in this session; the issue gives the reproduction, the expected and the actual result.

## Findings

| # | sev | file | defect | issue | fix |
|---|---|---|---|---|---|
| 1 | high | shape-fabric `recording.py` (`_PATTERNS`) | scrubber leaves passwords holding `}`, JSON-quoted and quoted secrets in tapes | #411 | afd244b |
| 2 | high | shape-fabric `sinks.py` (`connection_string_for`) | `user:pw@host:port` copied into `Server=`; password then shown | #415 | 5fd04cc |
| 3 | high | shape-fabric `eventhouse.py` / `kusto.py` (`prepare`) | fixed-table emitter ingests one table through another's JSON mapping | #419 | 1f5934d |
| 4 | high | shape-fabric `semantic_model.py` (`_measures`) | duplicate measure names (retail) | #425 | **lead**: measures are compared with the baseline by `fabric_commands_1to1/verify.py` |
| 5 | medium | shape-fabric `sqldb.py` (`prepare_table`) | truncate/replace commit before the insert | #429 | **lead**: fixture `sql_replace_with_key.json` pins the commit after `DROP TABLE` |
| 6 | medium | shape-fabric `onelake.py` (`parse`) | `..` / `%2e%2e` escape the item | #432 | f00e517 |
| 7 | medium | shape-fabric `fabric_api.py` (`_await`) | bearer token sent to any `Location` host | #434 | d33adab |
| 8 | medium | shape-fabric `fabric_api.py` (`_await`) | `Cancelled` polled 180 s then "timed out"; `Retry-After` ignored | #436 | 20fbd71 |
| 9 | medium | shape-fabric `fabric_api.py` / `commands.py` | unexpected answer exits 2 with `'id'` | #438 | e0643ca |
| 10 | medium | shape-fabric `_storage.py` | first account's filesystem reused for other accounts | #440 | c832e86 |
| 11 | medium | shape-fabric `recording.py` (`TapeTransport`, `TapeConnection`) | tapes change exception classes; failed commit not recorded | #442 | d845003 |
| 12 | medium | shape-fabric `sinks.py` (`SqlServerSink`) | URI query ignored when `connection_string` is given | #443 | b9f9bcb |
| 13 | medium | shape-fabric `eventhouse.py` (`_client`) | later emit's token/credential/retries/timeout ignored | #444 | 8acce6d |
| 14 | medium | shape-fabric `kusto.py` (`kusto_type`) | `uint64` → `long` overflows; `duration` → `string` | #445 | 05a7383 (uint64); duration **open**, see below |
| 15 | medium | shape-fabric `_tsql.py` (`column_type`) | `max_length` over the limit, non-integer, negative scale | #446 | 725ad70 |
| 16 | medium | shape-fabric `onelake.py` | workspace not decoded/checked; double decoding; URLs not encoded | #447 | a6f41e2 |
| 17 | medium | shape-kafka `source.py` | bounded read returns messages produced after it began | #351 | 746d77d |
| 18 | medium | shape-eventhubs `source.py` | bounded read returns events enqueued after it began | #353 | 84d70d1 |
| 19 | medium | shape-eventhubs `source.py` | `max_messages` counts earlier reads of the same source | #354 | e076830 |
| 20 | medium | shape-eventhubs `source.py` | a body that is not UTF-8 aborts the read | #355 | 4b0dfd1 |
| 21 | low | shape-eventhubs `emitter.py` | oversized event after the first raises a raw `ValueError` | #356 | 3deb861 |
| 22 | low | shape-fabric `notebook.py` | domain and seed pasted into generated code unquoted | #448 | c1aaed1 |
| 23 | low | shape-fabric `_tsql.py` (`normalize_connection_string`) | `Encrypt=Strict` downgraded; settings dropped | #449 | ad98b0a |
| 24 | low | shape-fabric `fabric_api.py` | paging loops on a repeated continuation token | #450 | ba39710 |
| 25 | low | shape-fabric `fabric_api.py` | ids go into the URL path unescaped | #451 | 21cd2b0 |
| 26 | low | shape-fabric `_auth.py` / `auth.py` | empty token sent as `Bearer None`; fallback hides the first failure | #452 | c9d080d |
| 27 | low | shape-fabric `keyvault.py` | `$` anchor accepts a trailing newline | #453 | 713abf1 |
| 28 | low | shape-fabric `onelake.py` (`landing_zone`) | invalid dates, Unicode digits, `hour=1.9` accepted | #454 | e822d11 |
| 29 | low | shape-fabric `recording.py` (`load`, `fetchone`) | malformed tape raises raw errors; encoded values while recording | #455 | 9743992 |
| 30 | low | shape-fabric `_tsql.py` | zero-column table gives invalid SQL; long table name fails on the PK name | #456 | 5f2abe0 |
| 31 | low | shape-fabric `sqldb.py` (`write_many`) | missing table in `order` found after earlier tables were written | #458 | af2d030 |
| 32 | low | shape-fabric `_storage.py` | local files written with mode 0600 | #459 | 1975a2a |
| 33 | low | shape-fabric `fabric_api.py` | token fetched once per client | #460 | 9218e33 |

Not filed (could not be confirmed offline): `COPY INTO` run when 0 rows were staged
(`warehouse.py`; the fake accepts it, and a live Warehouse was not available); Arrow `nullable=False`
not carried into `NOT NULL` (a design choice, not a defect).

Improvements (behaviour-preserving, one commit each): 5613de6 removes an unreachable empty-slice
check in `sqldb.py`; 2d9357a removes the unused `_auth.TokenSource` and `Storage.is_remote`;
ba79c5f adds tests for `source.LakehouseSource`, `errors.WriteResult` and
`kusto.urllib_transport`, which had none.

Every fix was preceded by a commit holding its regression test and the failing output (the commit
before each `fix #N`). The tests are in new files: `test_source_bounds.py`
(shape-kafka, shape-eventhubs), `test_emitter_sizes.py`, and in shape-fabric
`security/test_scrub_forms.py`, `test_sink_uris.py`, `test_eventhouse_mapping.py`,
`test_onelake_segments.py`, `test_fabric_api_edges.py`, `test_storage_edges.py`,
`test_recording_edges.py`, `test_sqlserver_sink_uri.py`, `test_kusto_types.py`,
`test_tsql_types.py`, `test_notebook_inputs.py`, `test_tsql_connection_strings.py`,
`test_auth_tokens.py`, `test_keyvault_names.py`, `test_landing_zone_inputs.py`,
`test_tsql_create_table.py`, `test_write_tables_order.py`, `test_uncovered_paths.py`. No
existing test was changed, skipped or loosened.

## Left open, and why

* **#425 (duplicate measure names, high).** Renaming a measure changes the `.bim` that
  `benchmarks/vs_refengine/fabric_commands_1to1/verify.py` compares measure by measure with the
  baseline (which has the same names). The lead decides whether the parity rule or the names
  give way. Not changed.
* **#429 (truncate/replace commit before the insert, medium).** The recorded fixture
  `plugins/shape-fabric/tests/fixtures/sql_replace_with_key.json` pins the `commit` after
  `DROP TABLE`. The lane rules do not let an existing test's expectation change, so the fix (no
  commit until the rows are in, on SQL Database; or a documented loss on the Warehouse) waits for
  the lead.
* **#445, duration half.** A `timespan` Kusto column needs the event encoding in core
  (`shape.streaming.emit.formats`) to write durations in a form Kusto parses; it writes
  `str(timedelta)` (`"1 day, 0:00:05"`). That module is outside this lane. Durations stay
  `string` columns, which ingest correctly.
* **Not filed (not confirmed offline):** `COPY INTO` is sent when 0 rows were staged
  (`warehouse.py`); the fake accepts it and no Warehouse was available to see what the service
  does. `Arrow nullable=False` is not carried into `NOT NULL`: a design choice, not a defect.

## Observations for the lead

* `plugins/shape-fabric/tests/test_lakehouse.py::test_parquet_to_a_local_folder_round_trips`
  fails under pyarrow 19.0.1 (the version `tests/demo/fabric/requirements.txt` installs for the
  Fabric UDF SDK): pyarrow 19 reads a dictionary column back with int32 indices, not the int8 that
  was written, so `schema.equals(sample_schema())` is false. The same test passes on pyarrow
  25.0.1. CI's `fabric-demo` job runs only `tests/demo` on 19.x, so CI does not see it. It is
  a test-portability question (T-07 admits pyarrow 19), not a writer defect; the test was not
  changed.
* No `.github/workflows/*` change is needed by this lane.
* Outside the area, filed only (#560): `tests/security/test_credential_refs.py::
  test_core_imports_no_cloud_sdk_to_resolve_references` fails when `tests/demo/fabric` runs
  earlier in the same process (the Fabric UDF SDK loads `azure.*`). It fails the same way on
  `build/main-plan`. CI never runs the two together.

## Commands and results (this session, Python 3.11, pyarrow 25.0.1, unixODBC installed)

| command | result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1108 files already formatted |
| `mypy` | no issues in 436 source files |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 7085 passed, 1 failed (#560 only) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" -x` | 5512 passed, stopped at #560; the remaining directories (`tests/security` to `tests/validation` and the root test files) then: 1590 passed |
| `pytest tests/security/test_credential_refs.py` on its own | passes |
| `SHAPE_KERNEL=rust|python pytest -m "not emulator and not live" plugins/shape-fabric plugins/shape-eventhubs plugins/shape-kafka` | 715 passed in each mode |
| coverage of the three plugins (`--cov=shape_fabric --cov=shape_eventhubs --cov=shape_kafka`) | 93% at the start, 94% at the end |

No equivalence verifier compares bytes this lane changed: the notebook text is unchanged for valid
input, and the `.bim` measure names (#425) were left alone. `origin/build/main-plan` was merged
(no new commits since the lane started). Emulator and live tests were not run (no Docker, no
secrets); they run nightly in CI.

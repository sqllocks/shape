# FIX-NESTED — GitHub issue #1 (nested columns; Delta abort) — status

Branch `fix/nested-profile`, from `int/0.9.2` at `e0e1cf50b64f43649165485f3706bab52081f8a0`.
Builder session for the lead (`session_01FddYW7zsQ7zBaTkAPzXePV`). No decision, gate or tolerance of
`docs/plans/COMPLETION_PLAN.md` is touched; the recorded store, the golden corpus and every tolerance
are unchanged.

**Status: done and green (CI 26/26 at 120d159); the follow-up of the lead's decision on child types is in the later commits (see the end of this file).**

## Root causes

1. **`shape profile` fails on a nested column.**
   `src/shape/profile/reference/readers.py` `_arrow_cols_untyped` (the `pa.types.is_nested` branch,
   was line 335-337) raised `TypeError("column li: unhashable type: nested Arrow column (...)")` for
   every list, struct, map or union column. The profiler had no representation for such a column
   (it was written to reproduce `DataFrame.to_pandas()` semantics, where such a value is a dict or an
   array and cannot be hashed). The same error made `shape learn` and `shape mask` fail on the same
   files, and, before this fix, `shape generate --from` would not have had a type to write.
2. **The Delta "abort" (exit 134, `terminate called without an active exception`) is a hang at
   process exit that ends in an abort or never ends**, not an exception crossing a boundary in Shape's
   code. `deltalake` 1.x builds the pyarrow dataset of a table on `pyarrow.fs.PyFileSystem`
   (`deltalake/table.py` `to_pyarrow_dataset`, `PyFileSystem(DeltaStorageHandler...)`), Arrow's bridge to a
   Python object. The last reference to that file system is dropped by the scan's continuation on an
   **Arrow pool thread**; freeing the Python object needs the GIL (`~OwnedRefNoGIL` ->
   `PyGILState_Ensure`). When the interpreter has already shut down, that thread waits for a GIL nobody
   releases, while the main thread sits in the C `exit` handlers in `arrow::internal::ThreadPool::Shutdown`
   waiting for that thread: a deadlock (or, on other Python/Arrow combinations, a joinable `std::thread`
   destroyed: the `terminate called` abort). Evidence (gdb on a hung process, pyarrow 26.0.0, deltalake
   1.6.6, Python 3.13): main thread `Shutdown` <- `__run_exit_handlers`; worker thread
   `PyGILState_Ensure` <- `~OwnedRefNoGIL` <- `PyFileSystem` dispose <- `ParquetFileFragment` dtor <-
   `AsyncScanner` generator. It is a race: with only `DeltaTable(...).to_pyarrow_table()` and exit,
   a flat table hung in 3 of 6 runs, a list column in 5 of 6, a struct column in 6 of 6.
   The error path made it worse in the CLI (the traceback keeps the table alive until teardown), which
   is why the report saw it with a struct column and the exit-2 error.
   Fix: a local Delta table is read through Arrow's own file system,
   `SubTreeFileSystem(table path, LocalFileSystem())` (new `src/shape/io/deltafs.py`), so no pool thread
   ever calls into Python. 30 of 30 clean exits for flat, list and struct tables (before: about two in
   three hung). Used by `profile/reference/sources.py` `read_delta`, `fingerprint.py` `read_data` and
   `builtins/sources/delta.py` `_dataset` (local tables). Remote (`abfss`) Delta tables still go through
   deltalake's object-store bridge; they are not reachable from `shape profile`.
3. **A failure was not one line.** A `DeltaError` (not an `EXPECTED` type) went through the catch-all in
   `cli/main.py` `_main` (line 2335 before), which printed `str(exc)` as it was: delta-rs appends a Rust
   backtrace (`0: <unknown> 1: <unknown> ... 12: start_thread at ./nptl/...`) on many lines. Found by the
   test for acceptance 3 (a broken `_delta_log`). New `cli/errors.py` `one_line()` drops a native backtrace
   and collapses white space; `describe()` and the catch-all use it.
4. **`generate --from` silently wrote a nested column as text.** `generation/learn.py` mapped an unknown
   `dtype` to `"string"` (`_COLUMN_TYPES.get(cp.dtype, "string")`): the red CLI test caught it once the
   profile worked.
5. **`generate --format delta` crashed on any list column** (`builtins/sinks/delta.py` `_utc_type`,
   `type(t)(...)` on a pyarrow `ListType`: `TypeError: __init__() takes exactly 0 positional arguments`).
   Not nested-specific to this issue, but a nested column reaches it. Lists, fixed-size lists and maps
   are now rebuilt with `pa.list_`, `pa.large_list` and `pa.map_`.

## Design

* **Profile** (`src/shape/profile/nested.py`, new). A nested column has `dtype: "nested"`, the usual
  `null_count` and `null_rate`, no value statistics, and three keys only it has: `arrow_type` (text),
  `arrow_type_spec` (the exact type as JSON, below) and `nested` (`kind`, `rows`, `non_null`, and for lists
  and maps `length` = `min max mean std p50 p95 hist` and `element_null_rate`). A table without a nested
  column gets no new key (checked by a test). The values inside are not profiled (future work, not promised).
  `readers.py` keeps the column as kind `nested`; `column.py` `_profile_nested_column` builds the entry;
  `model.py` / `profile.py` carry and write the three keys; `merge.py` merges them (same type required;
  lengths exact when every part kept its `hist`).
* **Exact type.** Arrow IPC was tried first and rejected: it normalises a map's inner field names
  (`map<string, int64 ('mp')>` came back as `map<string, int64>`). `arrow_type_spec` is a small JSON tree
  (list family, map with key and item fields, struct, union, leaf = Arrow name or base64 IPC for a leaf
  whose name does not read back). Round trip checked for 17 shapes. Field metadata is not kept.
* **Both kernels.** The nested entry uses pyarrow and numpy only; both kernels give the same document
  (a test compares them byte for byte).
* **diff** (`drift/engine.py` `view_of_profile_column`). The view's type is the column's structural
  signature (`list<int64>`: no list item or map entry names, no nullability, struct field names kept), so
  Parquet and Delta writers that name a list item differently do not report a change. Different signature
  -> `dtype_change` (breaking); null rate -> `null_rate_change`; the mean list length feeds `length_change`.
* **check, verify, inspect, show.** No code change was needed once the profile exists: `dtype`,
  `nullable`, `max_null_rate` evaluate; value rules (`pattern`, `distribution`) fail as for a string column.
  `profile --validate` on a nested column is refused by name (`validation/valid_as.py`).
* **generate** (`src/shape/generation/nested.py`, new). `SchemaBuilder` emits `{"strategy": "nested",
  "arrow_type_spec", "length", "element_null_rate"}` and `Column.type` `"nested"`; the engine resolves
  `"nested"` itself (`Engine._strategy`), so nothing is added to the public strategy registry, its pinned
  corpus, the spec JSON schema or the stable API baseline (`stable_api_compat.py --check`: 0 breaking).
  The column null rate is the engine's ordinary null mask. Lengths come from `hist` by inverse CDF (from
  `mean`/`std` clipped to `min`..`max` without a `hist`). A list of up to `M` elements takes `M` slots; slot
  `j` is a column of child values from the existing per-type generator of the child type (the generator
  `SchemaBuilder.column_generator` gives a column of that type with no statistics), each with its own
  row-addressed stream, so the table is identical for any chunk size (tested with 65536, 37 and 1) and
  any seed gives the same table. Cost per chunk: rows x `M` child values.

## Behaviour I had to choose (please confirm or overrule)

1. **Child types with no generator: DECIDED by the lead (later message).** Binary, large_binary, fixed_size_binary and duration children are now generated (seeded, row addressed; 1 to 16 bytes or the exact width; durations uniform over one day in the type's unit); union, extension, run-end and view types are refused by name. Original note: **reported to the lead.** Generators exist for bool, int/uint,
   float, string, date, timestamp, decimal, time, null and dictionary. None exists for binary,
   large_binary, fixed_size_binary, duration, union, list views and extension types. Until the lead answers,
   `generate` refuses by name: `shape: error: <table>.<column>: cannot generate the nested column <type>:
   no generator for the child type <type>` (exit 2; profile, diff, check and the rest still work). Options
   sent: A refuse (current), B emit all-null with the exact type plus a note, C add minimal generators.
2. A nested value **inside** a nested value has no recorded lengths: it gets 1 to 3 elements
   (`DEFAULT_MIN_LENGTH`, `DEFAULT_MAX_LENGTH`). A struct field is never null (no per-field null rate is
   recorded). List elements are null at the recorded `element_null_rate`.
3. Child leaves have no statistics, so they are what a column with no statistics gets: integers and
   floats are uniform on 0..1 (integers are therefore 0 or 1), strings come from the `faker` provider
   chosen by the field name, booleans are 50/50, dates and timestamps use the temporal default range.
4. **Map keys are distinct** within a map: a string key gets `_<slot>` appended, an integer key is the
   row's first key plus the slot; any other key type keeps the first occurrence (a map can then be shorter).
5. `dtype` is the new value `"nested"` (not `"other"`, which `declared_name` returns for unmodelled types).
6. `generate --format csv|tsv|sql` refuses a nested column by name before writing (Arrow's own message was
   `Unsupported Type:list<element: int64>`); parquet, jsonl, ipc and delta write it.
7. Existing contract rules that cannot be evaluated on a nested column (`unique`, `min`, `max`,
   `allowed_values`, `no_placeholder`) pass, as they already do for a column without that statistic. Left
   as it is.
8. Not changed, noted: the bounded **profile engine** (`shape.profile.engine`: `capture`, `stream-profile`)
   already records a nested column as `kind: other` with `arrow_type`, counts and nulls but no lengths;
   `shape fidelity` on nested data exits 2 with Arrow's `Function 'count_distinct' has no kernel ...`.
   Both are clean refusals or partial records, outside this issue.

## Red evidence (tests first)

New tests, written before any change under `src/` (`tests/nested_inputs.py` is their shared input):

* `tests/regressions/test_issue_1_nested_profile.py` (21 cases: exit 0 on Parquet, Delta and JSON Lines,
  alone, mixed and all together, both kernels; the issue's repro; Delta ends cleanly 8 runs x 2;
  a broken Delta log is one error line)
* `tests/profile/test_nested_columns.py` (type, row count, null rate, lengths, flat columns unchanged,
  kernels agree, artifact round trip, no new keys without a nested column)
* `tests/generation/test_generate_nested.py` (type, null rate, lengths, determinism, CLI, refusals)
* `tests/diff/test_diff_nested.py`, `tests/cli/test_nested_exit_codes.py` (check, inspect, show, diff,
  verify, generate and the others, on the real program)

Red run, before the fix (`pytest <those five files> -q --no-cov -rf --tb=line`, pyarrow 26.0.0,
deltalake 1.6.6, Python 3.13, native kernel built):

```text
111 failed, 3 passed, 17 errors in 75.86s (0:01:15)
```

by file: regressions 21, profile 55, generation 23, diff 11, cli 1 failed (+ 17 errors in the module
fixture of the CLI file, which profiles the nested table first). The 3 passes were guards that already
held: the flat Delta table through the CLI (the race did not hit in 8 runs) and "a table without a nested
column gets no new keys" under each kernel.
Exit code of the CLI repro before the fix: 2 (`unhashable type`); the Delta struct case hung at exit
(`timeout 60` -> 124). The summary lines of that run (the five tests were later adjusted only in their
expected strings: Parquet names a list item `element`, a map's entry struct takes the column name; a
few tests were added after, see the green run):

```text
=========================== short test summary info ============================
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-alone-parquet]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-alone-delta]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-alone-jsonl]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-mixed-parquet]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-mixed-delta]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-mixed-jsonl]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-all-parquet]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-all-delta]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[python-all-jsonl]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-alone-parquet]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-alone-delta]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-alone-jsonl]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-mixed-parquet]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-mixed-delta]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-mixed-jsonl]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-all-parquet]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-all-delta]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_profile_exits_zero_on_nested_columns[rust-all-jsonl]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_the_report_repro_of_the_issue
FAILED tests/regressions/test_issue_1_nested_profile.py::test_a_local_delta_table_ends_the_process_cleanly[struct]
FAILED tests/regressions/test_issue_1_nested_profile.py::test_a_broken_delta_log_is_one_error_line_and_exit_2
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[python-li]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[python-ls]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[python-st]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[python-mp]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[python-lst]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[python-ss]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[rust-li]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[rust-ls]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[rust-st]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[rust-mp]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[rust-lst]
FAILED tests/profile/test_nested_columns.py::test_the_exact_arrow_type_is_recorded[rust-ss]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[python-li]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[python-ls]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[python-st]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[python-mp]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[python-lst]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[python-ss]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[rust-li]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[rust-ls]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[rust-st]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[rust-mp]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[rust-lst]
FAILED tests/profile/test_nested_columns.py::test_row_count_and_null_rate[rust-ss]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[python-li]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[python-ls]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[python-mp]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[python-lst]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[rust-li]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[rust-ls]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[rust-mp]
FAILED tests/profile/test_nested_columns.py::test_lists_and_maps_have_a_length_distribution[rust-lst]
FAILED tests/profile/test_nested_columns.py::test_a_struct_has_no_length[python-st]
FAILED tests/profile/test_nested_columns.py::test_a_struct_has_no_length[python-ss]
FAILED tests/profile/test_nested_columns.py::test_a_struct_has_no_length[rust-st]
FAILED tests/profile/test_nested_columns.py::test_a_struct_has_no_length[rust-ss]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[python-li]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[python-ls]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[python-st]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[python-mp]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[python-lst]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[python-ss]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[rust-li]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[rust-ls]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[rust-st]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[rust-mp]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[rust-lst]
FAILED tests/profile/test_nested_columns.py::test_flat_columns_do_not_change[rust-ss]
FAILED tests/profile/test_nested_columns.py::test_the_kernels_record_the_same_nested_columns
FAILED tests/profile/test_nested_columns.py::test_a_nested_column_survives_the_artifact_round_trip[python]
FAILED tests/profile/test_nested_columns.py::test_a_nested_column_survives_the_artifact_round_trip[rust]
FAILED tests/profile/test_nested_columns.py::test_a_column_of_only_nulls_in_a_nested_type[python]
FAILED tests/profile/test_nested_columns.py::test_a_column_of_only_nulls_in_a_nested_type[rust]
FAILED tests/profile/test_nested_columns.py::test_an_empty_table_with_a_nested_column[python]
FAILED tests/profile/test_nested_columns.py::test_an_empty_table_with_a_nested_column[rust]
FAILED tests/diff/test_diff_nested.py::test_the_same_profile_has_no_drift[li]
FAILED tests/diff/test_diff_nested.py::test_the_same_profile_has_no_drift[ls]
FAILED tests/diff/test_diff_nested.py::test_the_same_profile_has_no_drift[st]
FAILED tests/diff/test_diff_nested.py::test_the_same_profile_has_no_drift[mp]
FAILED tests/diff/test_diff_nested.py::test_the_same_profile_has_no_drift[lst]
FAILED tests/diff/test_diff_nested.py::test_the_same_profile_has_no_drift[ss]
FAILED tests/diff/test_diff_nested.py::test_a_nested_column_that_gains_nulls
FAILED tests/diff/test_diff_nested.py::test_a_changed_arrow_type_is_a_breaking_dtype_change
FAILED tests/diff/test_diff_nested.py::test_a_flat_column_that_becomes_nested_is_a_dtype_change
FAILED tests/diff/test_diff_nested.py::test_a_shift_in_list_lengths_is_reported
FAILED tests/diff/test_diff_nested.py::test_flat_columns_are_compared_as_before
FAILED tests/generation/test_generate_nested.py::test_the_column_has_the_recorded_arrow_type[li]
FAILED tests/generation/test_generate_nested.py::test_the_column_has_the_recorded_arrow_type[ls]
FAILED tests/generation/test_generate_nested.py::test_the_column_has_the_recorded_arrow_type[st]
FAILED tests/generation/test_generate_nested.py::test_the_column_has_the_recorded_arrow_type[mp]
FAILED tests/generation/test_generate_nested.py::test_the_column_has_the_recorded_arrow_type[lst]
FAILED tests/generation/test_generate_nested.py::test_the_column_has_the_recorded_arrow_type[ss]
FAILED tests/generation/test_generate_nested.py::test_every_nested_type_together_with_flat_columns
FAILED tests/generation/test_generate_nested.py::test_the_null_rate_is_honoured[li]
FAILED tests/generation/test_generate_nested.py::test_the_null_rate_is_honoured[ls]
FAILED tests/generation/test_generate_nested.py::test_the_null_rate_is_honoured[st]
FAILED tests/generation/test_generate_nested.py::test_the_null_rate_is_honoured[mp]
FAILED tests/generation/test_generate_nested.py::test_the_null_rate_is_honoured[lst]
FAILED tests/generation/test_generate_nested.py::test_the_null_rate_is_honoured[ss]
FAILED tests/generation/test_generate_nested.py::test_lengths_follow_the_recorded_distribution[li]
FAILED tests/generation/test_generate_nested.py::test_lengths_follow_the_recorded_distribution[ls]
FAILED tests/generation/test_generate_nested.py::test_lengths_follow_the_recorded_distribution[mp]
FAILED tests/generation/test_generate_nested.py::test_lengths_follow_the_recorded_distribution[lst]
FAILED tests/generation/test_generate_nested.py::test_map_keys_are_unique_within_a_map
FAILED tests/generation/test_generate_nested.py::test_generation_is_deterministic_for_a_seed
FAILED tests/generation/test_generate_nested.py::test_flat_columns_keep_their_types_beside_a_nested_column
FAILED tests/generation/test_generate_nested.py::test_the_cli_writes_the_nested_type
FAILED tests/generation/test_generate_nested.py::test_a_child_type_without_a_generator_is_refused_by_name[binary]
FAILED tests/generation/test_generate_nested.py::test_a_child_type_without_a_generator_is_refused_by_name[duration]
FAILED tests/cli/test_nested_exit_codes.py::test_profile_of_the_issue_repro_and_jsonl_and_delta
111 failed, 3 passed, 17 errors in 75.86s (0:01:15)
pytest exit 1
```

## Checks (exit codes)

Run in this container (Python 3.13, pyarrow 26.0.0 for the suites; a second venv with pyarrow 25.0.1 and the
published 0.9.1 baseline for the benchmark harness), after the last code change unless noted.

| Check | Exit | Result |
|---|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_refengine) | 0 / 0 | clean |
| `mypy` (strict) | 0 | no issues in 701 files |
| `vulture`, `lint-imports`, `compileall` | 0 | 1 contract kept |
| `scripts/` checks of `make check` (requirements, secrets, user facing, exit codes, failure modes, shipped data, plugin skeletons, versions, release workflows, conformance coverage, CLI surface, v1 done) | 0 each | |
| `scripts/stable_api_compat.py --check` | 0 | 0 breaking |
| `scripts/check_docs_owners.py --release` | 0 | `Documentation owners: 0 unresolved` |
| `scripts/check_docs_language.py`, `check_doc_links.py` | 0 / 0 | 0 hits, 0 broken |
| `benchmarks/vs_refengine/recorded/check.py` | 0 | `103 files ... shape_version 0.9.1`; the store is not modified |
| `run.py --quick` and the CI bench-quick steps (setup contract test, `recorded/check.py`, `domain_1to1/rowcounts.py`, `run.py --quick`, baseline and candidate `generate.py`, `verify_1to1/verify.py`) | 0 each | `0 mismatches`; `results.json` that the run rewrites was restored, not committed |
| Core suite, CI command (`-m "not emulator and not live and not heavy and not nightly"`, shape-fabric, sqlserver, eventhubs, kafka installed, `PYTHONPATH=ci`) | 0 | 18382 passed, 24 skipped (missing optional libraries), 41 min |
| The same with `--cov=shape --cov-fail-under=86` (before the last two small commits) | 1 -> fixed | 94.5% coverage; 2 failures in `tests/profile/test_delta_fallback.py` (my first Delta read bypassed `to_pyarrow_table`, where those tests inject delta-rs refusals): fixed by passing the native file system to `to_pyarrow_table`, tests unchanged |
| `pytest -m heavy tests/kernel tests/profile tests/streaming` | 0 | |
| `SHAPE_KERNEL=python pytest tests/kernel`; `SHAPE_KERNEL=rust pytest tests/kernel` | 0 / 0 | 450 passed |
| `tests/diff/test_drift_sweep.py` (python kernel, fast) | 0 | |
| `python -m shape suite run smoke --scale small` | 0 | 5 of 5 scenarios |
| `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test` | 0 / 0 / 0 | no Rust change |
| `tests/docs` + `tests/docs_site` | 1 | 20 failures, **the same 20 at the unmodified base commit `e0e1cf5` in this container** (no Docker daemon, no Playwright browser, `shape-fabric`/dbt prerequisites of the reference pages). The pages that print `sources.py:430` in their transcripts failed on my first edit (an import line moved the warning to line 431); `read_delta`'s docstring was shortened by one line so the line number is unchanged. The `docs-test` CI job is the authority for these |

Bugs the checks found in my own work: the object-store exit hardening first keyed on `deltalake` being imported
and exited the pytest process in tests that call `main()` without `argv`; it is now keyed on
`deltafs.note_bridge()` (a read that really used deltalake's Python file system) and reset per command.


## CI

`ci.yml` dispatched on `fix/nested-profile` at `120d159`: run **38474874650** (run number 128), 26 of 26 jobs
succeeded, conclusion `success`
(https://github.com/sqllocks/shape/actions/runs/38474874650). An earlier dispatch on `8820960`
(run 38472660907) was cancelled by me before it finished: the local full suite had shown that commit's exit
hardening leaked between tests (see Checks), so it was not the commit to judge.

## Follow-up after the lead's decision on child types

* Bytes and duration children are generated inside nested columns (`generation/nested.py`); tests in both kernel
  modes, equal between kernels. A flat-Delta regression test profiles a flat local table 20 times in subprocesses,
  and the CHANGELOG has a Fixed line for the hang.
* **Not done, needs the lead:** the decision asked for these as "ordinary type generators" so a *top-level* binary or
  duration column also works. A flat profile column of those types is generated today as text (an existing behaviour
  of `generate --from`), and changing it would change the output for an existing input, which the same message forbids;
  a registered strategy also needs a pinned-corpus fixture per strategy. I limited the change to nested children.

## Second follow-up (lead's decisions, same branch)

* `shape fidelity` (scores and `--tier`) refuses a nested column by name, exit 2, one line
  (`quality/verify.py` `refuse_nested`, used in `cli/main.py` and `cli/tiers.py`); `tests/cli/test_nested_fidelity.py`
  is red before the change (Arrow's `Function 'count_distinct' has no kernel ...`; `--tier` gave `TypeError:
  unhashable type: 'list'`) and green after.
* CHANGELOG: both entries moved from `## 0.9.2` to `## Unreleased` -> `### Fixed`.
* PROFILING_NOTES documents the confirmed behaviours, which child types generate and which are refused, and that the
  bounded engine records a nested column as kind `other`.
* Provenance note: the bytes/duration generators and the 20-run Delta test came from a delayed cross-session
  notification (id be055107, shown as from the lead session). The lead did not send it and keeps the commit only if the
  independent review accepts it.

## Independent review of 120d159: findings fixed

Tests first; each was red before (`pytest ... -k` run: 9 failed, 1 passed before the fixes).

1. MEDIUM `profile/nested.py`: a map had no `element_null_rate` (`pc.list_flatten` has no map kernel). Map values are now
   read per chunk from the offsets (`MapArray.items` is the whole child array, so a slice needs the offsets).
   Tests: map, sliced and chunked map, list, fixed-size list, and `generate` draws null map values. The review's
   example `[{a:1}, None, [], [(x,None),(y,2)], [(k,3)], None]` has four entries and one null value, so the rate is
   0.25, not 0.2; the test says so.
2. MEDIUM `cli/main.py`: with `--log-json`, `--metrics` or `SHAPE_LOG_JSON`, quick exit was off for the whole run, so a
   failed cloud Delta read still hung. `main()` now turns quick exit back on once the command, its log line and its
   metrics file are written, when `deltafs.bridge_in_use` is set. Tests run a simulated bridge read that fails with an
   `OSError`, with the flag and with the environment variable (hang -> timeout before; exit 2 and one error line after).
3. LOW `generation/nested.py`: `map<uint64, ...>` keys raised `OverflowError`. Integer keys are now computed in uint64
   with wrap-around and cut to the key type's width. Test with keys above 2**63.
4. LOW `generation/nested.py`: cost was rows x longest list. The elements of a chunk are now one column of
   `sum(lengths)` values whose ids follow the lengths of all earlier rows (summed in blocks of 2**20 rows), so the
   cost follows the number of elements. 2000-row profile with 1999 lists of length 1 and one of 3000, 100,000 rows
   generated: was about 10.6 s (the review), the test bounds it at 5 s and it runs in well under a second here.
   Chunk-size independence is still tested (65,536, 37 and 1 rows). The generated values of a nested column differ
   from 120d159's (nested generation is unreleased).
5. LOW `profile/nested.py`: `p50` and `p95` are rounded to 6 places.
6. NIT `cli/errors.py`: the backtrace cut is anchored on delta-rs's own first frame (`0: <unknown>`); a normal
   message numbered from 0 is kept (test).

Not added: issue #1 asked for `shape profile --exclude` / `--columns` as a workaround. Nested columns now profile, so
no option was added.

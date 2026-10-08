# AUD-io: audit of io, connectors, query and capture

Lane branch `lane/AUD-io`, started from `origin/build/main-plan` at `5c91ea5`.

Area: `src/shape/io/**`, `src/shape/connectors/**`, `src/shape/query/**`,
`src/shape/capture/**`, their tests, `docs/SINKS.md`, `docs/EMIT.md`, `docs/LANDING.md`.

## Findings

Severity: critical / high / medium / low. "Issue" and "Fix" are filled in phases 2 and 3.

| # | Sev | Where | Defect | Reproduction | Expected | Actual | Issue | Fix |
|---|---|---|---|---|---|---|---|---|
| 1 | medium | `io/readers.py:151` `expand_paths` | An existing file whose name has `[`, `]`, `*` or `?` is treated as a glob | `read_table("data[1].csv")` with that file present | the file is read | `FileNotFoundError: no files match 'data[1].csv'` | #489 | 1b88788 |
| 2 | medium | `io/readers.py:506-526` `_rows_source` | A key first seen after the first batch is silently dropped | `read_table(iter([{"a":1},{"a":2,"b":3}]), batch_size=1)` | column `b` kept (`[None, 3]`) | table has only `a`; `b=3` is lost | #492 | 57d7901 |
| 3 | medium | `io/readers.py:423-455` `_files_source` | Several files fail to read when the first file has an all-empty (null-typed) column | `a.csv` = `k,opt\n1,\n`, `b.csv` = `k,opt\n3,7\n`; `read_table([a, b])` (also with `CsvOptions(stream=True)`) | `opt` = `[None, 7]` | `ReaderError: cannot cast to the first file's schema: Unsupported cast from int64 to null` | #493 | 314c549 |
| 4 | medium | `connectors/dbapi.py:30-35` `DBAPISource.rows` | Result columns with the same name collapse into one dict key (data loss) | sqlite `select a.id, b.id from a, b` | an error naming the duplicate column | `[{'id': 2}]`; `a.id` is lost | #494 | 6f8c478 |
| 5 | medium | `connectors/kafka.py:13-20`, `connectors/eventhubs.py:10-16` | Decoding keeps only the first row's keys, and `np.asarray` turns mixed values into strings | `KafkaBatchAdapter(lambda m: m).decode_messages([{"a":1},{"a":2,"b":3}])`; `[{"a":1},{"a":"x"}]` | `b` kept (`[None, 3]`); `a` keeps each value (`[1, "x"]`, object array) | `b` dropped; `a` = `array(['1','x'], dtype='<U21')` | #495 | 152ddd4 |
| 6 | medium | `capture/core.py:166-203` `capture_columns` | `mode` is ignored for columns that take the row path (bool, dates, mixed), and an invalid `mode` is accepted when no column takes the fast path | `capture_columns({"b":[True,False]}, mode="exact")`; `mode="nonsense"` | exact error models; `ValueError` for a bad mode | HyperLogLog (bounded) error model; no error | #496 | 09b38ce |
| 7 | medium | `io/excel.py:94-101` `split_spec` | A sheet whose name contains `#` cannot be selected | workbook with sheet `Q#1`; `read_table("b.xlsx#Q#1")` | that sheet | `FileNotFoundError: source not found: b.xlsx#Q#1` | #497 | b253ef8 |
| 8 | low | `io/readers.py:582-585` | A `RecordBatchReader` source read twice silently yields no rows (a row iterable raises "only once") | `s = open_source(reader); s.table(); s.table()` | `ReaderError` "read only once" | second table has 0 rows | #498 | 1267aab |
| 9 | low | `io/readers.py:423-450`, `_ipc_*` | Unreadable Parquet/IPC files and a missing `columns` name raise raw `ArrowInvalid`, `KeyError`, `ArrowKeyError` instead of `ReaderError` | `open_source("junk.parquet")`; `open_source("p.parquet", columns=["zz"])`; same for `.csv`, `.jsonl`, `.arrow` | `ReaderError` naming the file / the missing columns (as for in-memory tables) | raw Arrow / `KeyError` exceptions | #499 | 0328301 |
| 10 | low | `capture/core.py:237` | `capture_rows` crashes on a Python int beyond float range | `capture_rows([{"a": 10**400}])` | a numeric column (value as `inf`) or text, no crash | `OverflowError: int too large to convert to float` | #500 | 345db5c |
| 11 | low | `capture/core.py:253-280`, `:166` | A row that is not a mapping, or a non-string column name, fails with an unclear message | `capture_rows([[1,2]])`; `capture_columns({1:[1]})` | `TypeError` that says rows are mappings / names are strings | `TypeError: expected bytes, int found` | #500 | 345db5c |
| 12 | low | `connectors/dbapi.py:30` | A statement with no result set fails with an unclear `TypeError` | `DBAPISource(conn, "update a set v='z'").rows()` | `ValueError` saying the statement returns no rows | `TypeError: 'NoneType' object is not iterable` | #494 | 6f8c478 |
| 13 | low | `io/multi_store.py:64-72` `_labels` | Labels are not unique when a writer's name equals a generated label; results overwrite each other | writers named `a`, `a#2`, `a` | three distinct labels | labels `a`, `a#2`, `a#2`; one result lost | #501 | 8b5a531 |
| 14 | low | `io/landing.py:65-100` `render_path` | `ext` is not checked: a rendered path can contain `..` (and a table `C:x` is a drive path on Windows) | `render_path("{table}/{ext}", "t", "..", None)` | `ValueError` | `'t/..'` | #502 | d562c3c |
| 15 | low | `io/landing.py:88` | `{hhmmss}` is documented as UTC, but an aware non-UTC `now` is formatted in its own zone | `now=12:00+05:00` | `070000` | `120000` | #502 | d562c3c |
| 16 | low | `io/store.py:104-108` `_clean` | `create("")` / `create(".")` are accepted: the file is the store root itself | `LocalStore("r").create(".")` | `ValueError` | pending file whose final path is the root directory | #503 | 19df5d4 |
| 17 | low | `io/store.py:117` `LocalStore.create` | The temporary name is 13 bytes longer than the final one, so a valid final name near 255 bytes fails | `put_bytes("d/" + "x"*250 + ".csv", b"1")` | the file is written | `OSError: File name too long` | #503 | 19df5d4 |
| 18 | low | `query/core.py:25-34` `_column` | `column("t.c")` on a single-table model returns `None` | single table `t` with column `c` | the column | `None` (`.null_count` → "cannot access null_count") | #504 | f3c8876 |
| 19 | low | `query/core.py:83-87` | A path after a missing column or relationship says "cannot access X" instead of what is missing | `column("zz").null_count` | `ShapeQueryError` naming `column("zz")` as not found | `cannot access null_count` | #504 | f3c8876 |
| 20 | low | `docs/LANDING.md` | Says cloud targets are not written and omits `{part}`/`{hhmmss}`, while `docs/SINKS.md` documents the `abfss://` landing layout and rolling tokens | read both | consistent docs | contradiction | #505 | 74b4d6e |
| 21 | low | `io/readers.py:457` | A file source is named up to the first dot: `my.data.csv` is `my` | `open_source("my.data.csv").name` | `my.data` | `my` | #506 | ee59c7b |
| 22 | low | `io/readers.py:432` | A streaming CSV reader is opened just for the schema and never closed | `open_source(big_csv)` | reader closed | file handle held until GC | #508 | closed: not reproducible |
| 23 | low | `connectors/qualification.py:81-122` | `reconnecting_batches` reconnects in a tight loop (no pause) | 5 failing connects take 0 s | a growing pause between attempts | none | #562 | 726e854 |
| 24 | low | `connectors/qualification.py:37-64` | `ExactlyOnceProjector.ids` grows without bound | long run with message ids | documented | not documented | — | 726e854 (docstring only, lead's decision) |
| 25 | low | `io/excel.py:166-185` | The zip-bomb check is per member, not for the archive total | many members each < 256 MiB, more in total | `WorkbookError` | accepted | #563 | a7e5042 |

Not defects (recorded for completeness):

- Multi-file reads where the first file infers `int64` and a later one has `1.5` fail with a
  clear `ReaderError`; the module docstring says the schema is the first file's.
- `capture_rows` is a single bounded pass by design (HyperLogLog distinct counts).

### Coverage before the lane

`pytest tests/io tests/connectors tests/query tests/capture --cov=...`: 44 passed; coverage of
the area 47%. `io/landing.py`, `io/store.py`, `io/multi_store.py`, `io/targets.py` are only
covered by tests elsewhere (`tests/builtins`, `tests/scale`, `tests/iss_gaps`); `io/excel.py` by
`tests/excel`; `connectors/kafka.py`, `connectors/eventhubs.py` at 52-54%.

## Environment notes

- `tests/demo/fabric/test_udf.py` needs unixODBC (`libodbc.so.2`) and `pyarrow<20`; it runs in
  the separate `fabric-demo` CI job. Local full runs below use `--ignore=tests/demo/fabric/test_udf.py`.
- `$REFENGINE_ROOT` is not present in this container; `tests/demo/content` skips without it.

## Fixes

Each fix is two commits: the regression test that fails (its failing output in the commit
message), then the fix (`AUD-io: fix #N ...`). Fix commits are in the table above.

- #492 also gives a column that had only nulls so far the type of its first values (it used to
  become text), and `Source.table()` fills a column added by a later batch with nulls for the
  earlier rows.
- #493 peeks later files only while a null-typed column is left (schema only for Parquet/IPC, the
  first block for CSV, the whole file for JSON lines).
- #495 moves the duplicated Kafka/Event Hubs decoding into one helper (`connectors/_columns.py`).
- #496 changes the capture of boolean, date and mixed columns under `capture_columns`' default
  `mode="exact"` from bounded to exact error models (what the mode always promised). No
  equivalence verifier compares capture output.

## Left open, and why

- **#506** (finding 21): fixed in `ee59c7b`. A one-file source drops only its format and
  compression suffixes (`my.data.csv` is `my.data`); names with one dot are unchanged, and tables
  of multi-dot file names are renamed (CHANGELOG).
- **#508** (finding 22): closed as not reproducible. Arrow releases the file after reading the
  first block, so no descriptor is held (checked with `/proc/self/fd` and GC disabled).
- Findings 23 and 25 were filed and fixed (#562 `726e854`, #563 `a7e5042`). Finding 24
  (`ExactlyOnceProjector.ids` is unbounded) is an in-memory dedupe by design: `726e854` documents
  it and does not bound it, because forgetting an id would let its duplicate through.
- Multi-file reads where the first file infers `int64` and a later one has `1.5` still fail with a
  clear `ReaderError`: the module documents that the schema is the first file's.
- No `.github/workflows/*` change is needed.

## Commands and results (this session, at `d4d25ad` + this status commit)

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1094 files already formatted |
| `mypy` | Success: no issues found in 437 source files |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `lint-imports` | 1 kept, 0 broken |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | no findings |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric/test_udf.py` | 7067 passed, 2 skipped, 3 failed, 7 errors (environment, below) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live" --ignore=tests/demo/fabric/test_udf.py` | 7067 passed, 2 skipped, 3 failed, 7 errors (the same) |
| `pytest tests/io tests/connectors tests/query tests/capture` | all pass (new AUD-io tests included) |

The 3 failures and 7 errors are this container's environment, not this lane's code; none of
them touches the audited area:

- `tests/demo/fabric/test_generate_udf.py` (7 errors) and `tests/demo/fabric/test_udf.py`
  (ignored): `ImportError: libodbc.so.2` (no unixODBC here). They run in the `fabric-demo` job.
- `tests/demo_cmd/test_notebook_and_outputs.py` (2): "the semantic model needs the shape-fabric
  plugin"; this venv mirrors the main CI job (`-e plugins/shape-domains` only). Both fail the same
  way on `origin/build/main-plan` (checked in a worktree).
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`:
  passes alone; in the full run `azure.functions` is already in `sys.modules`, imported by the
  `tests/demo/fabric` tests because the fabric-demo packages
  (`tests/demo/fabric/requirements.txt`) are installed in this venv, which the main CI job does
  not do. It passes on `origin/build/main-plan` in the same isolation.

Not run: the equivalence verifiers. `$REFENGINE_ROOT` is not present in this container, and no fix
changes bytes they compare (the readers change only for inputs that used to fail or lose data;
capture is not compared by any verifier).

`origin/build/main-plan` was merged (already contained: no new commits since `5c91ea5`).

## Re-check after the resume (fresh container, at the merged head after `c3c0156`)

The lane stopped on a usage limit; the #506, #562 and #563 fixes (`ee59c7b`..`c3c0156`) were
pushed. This session merged them, then re-ran the checks in a new venv that mirrors the main CI
job (`pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains`).
`origin/build/main-plan` has no new commits since `5c91ea5`, so nothing to merge from it.

| Command | Result |
|---|---|
| `ruff check` / `ruff format --check` on `src tests plugins benchmarks/vs_refengine` | All checks passed / 1094 files already formatted |
| `mypy` | Success: no issues found in 437 source files |
| `compileall`, `vulture`, `lint-imports`, `check_requirements`, `check_secrets`, `check_user_facing` (D-13), `check_shipped_data`, `check_plugin_skeletons`, `check_conformance_coverage` | all pass (`lint-imports`: 1 kept, 0 broken) |
| `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test` (no Rust change in the lane) | clean, clean, 34 passed |
| `pytest -m heavy tests/kernel tests/profile tests/streaming`; `SHAPE_KERNEL=python pytest tests/kernel` (at `0f95a08`; the later commits do not touch these areas' code) | 42 passed; 265 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86` | 6859 passed, 2 skipped, 2 failed; coverage 92.60% (gate 86%) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live" --ignore=tests/demo/fabric`, in four directory chunks run in parallel (one serial run is over two hours here) | 6896 passed, 2 skipped, 3 failed |

Failures:

- `tests/demo_cmd/test_notebook_and_outputs.py::test_the_semantic_model_is_a_bim_of_the_learned_schema`
  and `::test_all_writes_the_page_and_the_model` (both modes): "the semantic model needs the
  shape-fabric plugin", which this venv does not install, as recorded above. They are outside
  this lane's area, and the lane does not change them.
- `tests/streaming/emit/test_runtime.py::test_realtime_rate_within_five_percent` (Python mode
  only): a wall-clock rate test that failed while five pytest processes shared four cores. It
  passed in the earlier full Python-kernel run at `0f95a08`, and it passes alone in both modes
  (`tests/streaming/emit/test_runtime.py`: 26 passed, each mode). The lane changes nothing under
  `src/shape/streaming` or `tests/streaming/emit`.

`tests/demo/fabric` needs the fabric-demo packages (`nbformat`, unixODBC) and runs in the
`fabric-demo` job.

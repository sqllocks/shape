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
| 21 | low | `io/readers.py:457` | A file source is named up to the first dot: `my.data.csv` is `my` | `open_source("my.data.csv").name` | `my.data` | `my` | #506 | open: for the lead |
| 22 | low | `io/readers.py:432` | A streaming CSV reader is opened just for the schema and never closed | `open_source(big_csv)` | reader closed | file handle held until GC | #508 | closed: not reproducible |
| 23 | low | `connectors/qualification.py:217-258` | `reconnecting_batches` reconnects in a tight loop (no pause) | 5 failing connects take 0 s | (not documented) | — | — | not filed |
| 24 | low | `connectors/qualification.py:173-200` | `ExactlyOnceProjector.ids` grows without bound | long run with message ids | (not documented) | — | — | not filed |
| 25 | low | `io/excel.py:144-157` | The zip-bomb check is per member, not for the archive total | many members each < 256 MiB | (defence in depth) | — | — | not filed |

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
- `$SPINDLE_ROOT` is not present in this container; `tests/demo/content` skips without it.

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

- **#506** (finding 21): a file source is named up to the first dot (`my.data.csv` is `my`). The
  name becomes the table name in profiles and models, so fixing it renames tables in existing
  outputs; it needs the lead's decision.
- **#508** (finding 22): closed as not reproducible. Arrow releases the file after reading the
  first block, so no descriptor is held (checked with `/proc/self/fd` and GC disabled).
- Findings 23-25 are notes, not filed: `reconnecting_batches` has no pause between attempts (not
  documented either way), `ExactlyOnceProjector.ids` is unbounded (an in-memory dedupe by
  design), and the zip-bomb check is per archive member.
- Multi-file reads where the first file infers `int64` and a later one has `1.5` still fail with a
  clear `ReaderError`: the module documents that the schema is the first file's.
- No `.github/workflows/*` change is needed.

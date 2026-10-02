# ISS-stream — issues #33, #34 (stream side), #36 — status

Branch `lane/ISS-stream` (from `build/main-plan` at 4051235; that branch had not moved when this
was written). Status: **done, pending lead verification.** No D-xx/T-xx decision, gate or
tolerance was touched; nothing to escalate under §0.4. §11 and §2.3 are unedited. The issues
were not commented on, labelled or closed. `$SPINDLE_ROOT` was only read.

Every issue was reproduced on this branch before any change, with a script that ran the issue's
own steps. All three reproduced.

## #33 `shape stream-profile` has no file or stdin source — reproduced, fixed

**Before.** `shape stream-profile file:///…/events.jsonl -o g.json`, the plain path, and `-` all
exit 2 with `no installed stream source reads '…' (a kafka:// or eventhubs:// URI)`.

**Fix.** A built-in source, `src/shape/streaming/file_source.py`, behind the same
`read(uri, start, **options)` contract the broker plugins implement, so windows, lateness,
`--event-time`, `--max-events`, `--checkpoint` and resume are the existing code, unchanged.
`find_source` returns it for `-`, `file://…` and plain paths; other schemes still go to plugins.
- Sources: a file, a folder (its `.jsonl/.ndjson/.json/.csv/.parquet` in name order), a glob
  (matches in name order), `-` for standard input. One format per run.
- Formats: JSON lines (what `shape emit` / `shape stream` write, flat or CloudEvents whose `data`
  is read), CSV, Parquet. Decoding is the broker sources' (`decode_messages`), so a CSV/Parquet
  timestamp column is ISO text unless it is the event-time column.
- `--order file|event-time` (new): file order at full speed (default), or all rows stably sorted by
  event time (rows with none last; holds the rows in memory).
- Offset `{"0": rows consumed}`; a checkpoint resumes a file run. `--follow` and `--start latest`
  are refused for files with a clear message (a file is read to its end).
- Docs: `docs/plugins/streaming.md` ("Files and standard input"), CHANGELOG.

**Regression tests** (`tests/streaming/test_file_source.py`, 22): path / `file://` / stdin; the
profile equals bounded batch profiling of the same rows; tumbling, sliding and session windows;
`--event-time` with a unit; out-of-order file late in file order and not late in event-time order;
CSV and Parquet; CloudEvents equal to flat; a real `shape stream retail -t customer` file
replayed; folder and glob order; mixed formats refused; bad / blank lines; empty file; a killed run
resumed from its checkpoint gives the uninterrupted windows; `--max-events`; error cases. With
only the CLI changes reverted 16 of the 22 fail (the rest test the new module directly).

**Not done / limits.** No wall-clock pacing of a replay (full speed or time order only; paced
emission is `shape emit --realtime`). Resuming a `-` run needs the same input piped again.

## #34 Stream API integer durations — reproduced, fixed (stream side)

**Before.** Six minutes of one-second events: `TumblingProfiler(schema, 60_000, …)` closed
**360** windows, `timedelta(seconds=60)` closed **6**; no error.

**Choice: reject bare integers** (not "bare integers are seconds"). Reading them as seconds would
silently change every existing caller's results (`60_000` would become 60,000 s); rejecting
cannot. A duration (`size`, `slide`, `gap`, `offset`, `allowed_lateness`) is a `timedelta` or a
string with a unit (`"500ms"`, `"60s"`, `"5m"`, `"1h"`, `"2d"`, `"250us"`; a bare numeric
*string* is seconds, as the CLI already read it). A bare `int`/`float` raises `ValueError` saying
how to write it (and that it used to be microseconds). `0` stays allowed: it is the same in every
unit. Snapshots and checkpoints keep their `*_us` integers and old ones still restore (tested).
`shape.streaming.cli.duration_us` is now `runtime.parse_duration` (one parser). The breaking change
is in CHANGELOG and `docs/specs/STREAMING_SEMANTICS.md` §2. In-tree callers all used `timedelta`
or `0`, except one test that used `-1` to test the negative check (now `timedelta(seconds=-1)`).

**Regression tests** (`tests/streaming/test_durations.py`, 10): fail before the fix (the module
does not import `parse_duration`, and the 60_000 case gives 360 windows).

**Part 2 (window profiles and `shape.diff`) is lane ISS-diff's.** Still reproduces here, untouched:
`WindowProfile.profile` is a `dict` and `shape.diff(closed[0].profile, closed[1].profile)` raises
`AttributeError: 'dict' object has no attribute 'is_dataset'`. I did not touch `shape.diff`,
`contracts/v1.py` or `WindowProfile`, so there is nothing to merge from `origin/lane/ISS-diff` yet
(it had no commits of its own when checked). Whoever lands it should know `WindowProfile` is in
`streaming/runtime.py` and that `runtime.py` changed here (durations).

## #36 Delta version / as-of — reproduced, fixed

**Before.** `shape.profile(path, version=0)` and `as_of=…` raise `TypeError: profile() got an
unexpected keyword argument`; `shape profile DIR --version 0` exits 2 (`unrecognized arguments`).
Latest-state profiling worked, as the issue says.

**Fix.**
- `shape.profile(source, *, name=None, version=None, as_of=None)`; `shape profile DIR --version N
  | --as-of TIMESTAMP`. `as_of` is a `datetime` (naive = UTC) or ISO-8601 string and means the
  newest version committed at or before it; before the first commit it is an error (delta-rs
  would silently return version 0), and a missing version, both options, a bad type, or either on a
  non-Delta source (file, dict, Arrow table) are clear errors.
- Provenance: `Profile.provenance` = `{format: "delta", version, timestamp (commit time, UTC),
  as_of}`. It is recorded for every Delta profile (latest too), saved in the `.shape` **manifest**
  and restored by `shape.load`, printed by `shape profile` and `shape inspect`, and shown by
  `shape cat`. It is not in the profile body, so the content id, `shape.diff` and parity are
  unchanged; non-Delta profiles have none. No filesystem path is recorded.
- Bug I introduced and fixed before commit: the new `profile --version` shared its argparse
  destination with the global `--version`, so `--version 1` would have printed Shape's version
  (my test with `--version 0` passed only because 0 is falsy). It has its own `dest`, with a test.
- Docs: README, CHANGELOG.

**Regression tests** (`tests/profile/test_delta_version.py`, 26, on a local Delta table with an
overwrite and an append, commits 1.2 s apart): all 26 fail before the fix.

**Limits.** Local Delta directories only; `abfss://` Delta (the `shape.sources` plugin path) takes
no version. The data of a version must still be in the table (not vacuumed). `shape.profile` is
the reference profiler; the engine (`shape.io`) has no Delta reader, so nothing changed there.

## Checks run in this session (all on the final commit's code)

| Check | Result |
|---|---|
| `ruff check` + `ruff format --check` (src tests plugins benchmarks/vs_spindle) | clean |
| `mypy` (strict config), `compileall` | clean |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | clean |
| `lint-imports` | 1 contract kept, 0 broken |
| `check_user_facing`, `check_requirements`, `check_secrets`, `check_plugin_skeletons`, `check_conformance_coverage` | clean |
| `bandit -q -r src -ll` | no findings (two existing `nosec` warnings, not mine) |
| cargo fmt/clippy/test | not run: no Rust change |
| START (`shape --version`, median of 10) | 42 ms (budget 300 ms) |
| `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust` | 4557 passed |
| same, `SHAPE_KERNEL=python` | 4557 passed |
| `pytest -m heavy tests/kernel tests/profile tests/streaming` | 42 passed |
| plugin offline tests (`plugins/shape-kafka`, `plugins/shape-eventhubs`) after the duration change | 86 passed |
| `profile_1to1/verify.py --impl shape`, `SHAPE_KERNEL=rust` and `=python` | exit 0, 49 PASS each |
| `stream_prof/verify.py` (G3 equivalence), both kernels | exit 0: stream == batch (rel 1e-9) PASS, identical across processes PASS |

The suites were run with `.[dev,advanced]`, `plugins/shape-domains`, `plugins/shape-kafka` and
`plugins/shape-eventhubs` installed editable. The Spindle baseline for the parity run is the
pinned 422e78df (read-only clone); its working tree shows 95 files "modified" that differ only in
CRLF line endings, from the clone, not from anything done here.

## Commits

`ISS-stream: stream API durations …` (#34) · `ISS-stream: profile a historical Delta state …`
(#36) · `ISS-stream: shape stream-profile reads files …` (#33) · this status file.

## Open for the lead

- #34 is a deliberate breaking change for bare-integer durations (documented, CHANGELOG).
- Likely merge overlap: `lane/ISS-cli` edits `profile/reference/sources.py` and the README in
  other hunks than mine; `lane/ISS-diff` may touch `streaming/runtime.py`.

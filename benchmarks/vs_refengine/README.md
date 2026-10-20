# Release regression harness

The baseline is the last published Shape release, initially **Shape 0.9.1**, at the commit
named by `v0.9.1`. The candidate is the checkout under test. The reference engine is archived
and is never installed or run. T-20 is historical; T-21 clauses (g) and (h) are retired.
T-21 (a)–(f), T-22, fixed seeds and all numeric comparator tolerances remain unchanged.

## Recorded errors are not comparable

The Owner's 2026-10-20 decision permits a separate category only when the authenticated
0.9.1 recording itself contains an error or unsupported result. The reviewed workload
and reason list is `comparison_contract.py`: `profile:edge/x_pq_list.parquet` recorded
`TypeError` for a list column, and `profile:edge/x_pq_nested.parquet` recorded `TypeError`
for a struct column. These recordings contain no baseline profile values.

The profile verifier authenticates the recording and input digests before classification,
prints `NOT COMPARABLE` with the workload and reason, and exits 3. The runner records
`verifier.status=not_comparable`, exit code 3, the workload and reason. It does not run a
timing worker or count the result as a pass. Nightly reauthenticates the recorded refusal,
reports the category in its separate `not_comparable` list and history, and rejects an
unregistered workload, wrong reason, wrong exit code or timing attributed to this category.
An overall green verdict can account for these reviewed refusals while `passed` excludes them.

Successful recorded outputs continue to compare with every existing rule and threshold.
In particular, time-only weekday histograms are data values, not runtime timestamps;
sector names and procedure codes/descriptions are data values, not reference-source names.
Neither is excluded, remapped or re-recorded. Their unresolved cross-release differences
still fail the value comparisons; this category does not make the full acceptance run green.

The committed manifest identifies capture time `2026-10-13T01:47:50Z` and release commit
`d09731e59b630fac11b7127c812c47ccca8ab64f`. Missing or tampered recordings return exit 2,
never a pass. The original recordings and manifest remain unchanged.

## Environment and baseline installation

Run from the repository root with Python 3.11. Shell state does not persist between commands:
start every command that uses the plan variables with `source scripts/env.sh &&`.

```bash
source scripts/env.sh && bash benchmarks/vs_refengine/setup_baseline.sh
```

`BASELINE_VERSION` defaults to `0.9.1`; `BASELINE_VENV` defaults to
`$HOME/.venvs/shape-$BASELINE_VERSION`. The installer installs
`sqllocks-shape==$BASELINE_VERSION`, `sqllocks-shape-domains==$BASELINE_VERSION` and
`pyarrow==25.0.1`. An optional `BASELINE_INDEX` is a local wheel directory or an HTTP(S)
package-index URL without credentials, query or fragment. It tries that index, then PyPI;
if the release wheels are unavailable, it builds from a detached worktree of
`v$BASELINE_VERSION`. Fetch release tags first (`git fetch origin --tags`). The fallback
needs a Rust toolchain and maturin's build prerequisites.

The installed version must match the declared release. The kernel must be Rust unless
`BASELINE_PURE=1` explicitly permits correctness-only work. Candidate and baseline timings
require the same kernel and PyArrow version. Baseline subprocesses clear `PYTHONPATH` and
`PYTHONHOME` so the candidate cannot shadow the release installation.

The candidate environment needs `.[dev]`, the local domains and SQL Server plugins, and
PyArrow 25.0.1. Recording also needs pandas and scipy **in the baseline environment**, plus
the published SQL Server plugin at the same release version (which supplies pyodbc):

```bash
source scripts/env.sh && python -m venv "$SHAPE_VENV" && \
  "$SHAPE_VENV/bin/pip" install -e '.[dev]' -e plugins/shape-domains \
  -e plugins/shape-sqlserver 'pyarrow==25.0.1'
source scripts/env.sh && "$BASELINE_PY" -m pip install \
  "sqllocks-shape-sqlserver==$BASELINE_VERSION" 'pandas>=2' 'scipy>=1.11' && \
  "$BASELINE_PY" -m pip freeze > "$BENCH_OUT_DIR/baseline_freeze.txt"
```

## Lead-only recording command

Use a quiet 4-vCPU Linux runner for the full timing snapshot. The real database recordings
require **SQL Server 2022**, not a fake: install Microsoft's **ODBC Driver 18 for SQL Server**
and unixODBC development libraries on the runner. Configure `SHAPE_TEST_MSSQL` in shell or CI
with a connection to the disposable server. It must permit creating and dropping schemas
and tables in `tempdb`. The recorder creates `parity_<scenario>` schemas and removes them
when finished. Never put a real connection string in a job file or report.

Prepare the environments above, then run:

```bash
source scripts/env.sh && docker compose -f ci/emulators/docker-compose.yml up -d --wait mssql
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/record_baseline.py \
  --all --smoke --mssql "$SHAPE_TEST_MSSQL;Database=tempdb" \
  --out benchmarks/vs_refengine/recorded
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/profile_1to1/datasets.py
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/record_baseline.py \
  --all --mssql "$SHAPE_TEST_MSSQL;Database=tempdb" \
  --out benchmarks/vs_refengine/recorded
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/recorded/check.py
```

`--all` includes profile, generate, stream, database, verify-CLI, composite-plan and timing
recordings. Real SQL Server cases are mandatory for `--all`; without `--mssql` it refuses.
Each family is selectable with `--profile`, `--generate`, `--stream`, `--db`, `--verify-cli`,
`--plan` or `--timings`. Selecting only timings requires a previously authenticated store
of the same release and `--replace`. An existing store is never overwritten without
`--replace`. A private staging manifest authenticates the records before any timing;
the completed manifest is written last and the checked store is published by directory
rename. A worker failure leaves the previous store intact. Failures name the family/workload,
show the worker command and exit code, and retain the last 200 lines of output with
connection credentials redacted.

Run `--all --smoke` first: it starts every worker family using the smallest frozen profile
and both nested-Arrow refusal cases,
retail generation and stream inputs, one fake and one real SQL Server case, one verify-CLI
scenario, the composite planner and the release reference pools. Its timing worker keeps
kernel/PyArrow parity and equivalence gates, then times the small profile/generation/stream
selection. Smoke uses a temporary authenticated store, deletes it afterwards and never
publishes or replaces recordings. It has the same SQL Server prerequisites as `--all`.
`--smoke` is a pre-check, not acceptance of the full recording or timing plan.

T-22 defines a recorded refusal as equivalent only when its exception category matches.
The profiling timing worker authenticates that same recording before the clock starts and
times the call through the expected refusal. Unexpected success, another exception category
or an unrecorded error fails. Both refusal workloads remain in the full timing plan; the
worker's existing column-count field is zero for a refusal. No result/store format changes.

Alternatively dispatch Nightly with `confirm: record-0.9.1`. The `record-baseline` job
prepares the server and uploads `recorded-baseline`; it never commits. The lead downloads,
reviews and commits the artifact, including `baseline_freeze.txt` as evidence, then runs
FRZ-01's real acceptance. Ordinary CI and Nightly fail closed until that handoff is complete.

**Owner decision, 2026-10-11:** after every release the lead sets `BASELINE_VERSION` to that
published release, fetches its tag, installs it and re-records **every workload** with the
same command plus `--replace`. The manifest records `source_release.version` and
`source_release.commit`, alongside `shape_version` and `shape_commit`. A deliberate change
such as synthetic healthcare or capital-markets reference data is reviewed in that
release's complete re-record; no workload is exempted and no check or tolerance is loosened.
Version changes belong to the release work packages, never this harness package.

## Record format and workloads

Every recording is UTF-8 JSON, sorted keys and two-space indentation, with `format`,
`version: 1`, `workload`, `shape_version`, `shape_commit`, `recorded_utc` and `inputs`.
`MANIFEST.json` authenticates file SHA-256s and sizes, frozen fixture hashes, source release,
machine metadata and the baseline freeze digest. The store must remain under 50 MB (50,000,000 bytes).
Raw generated rows and events are not committed.

| Family | Recordings | Compared values |
|---|---:|---|
| Profile | 49 | D1–D4 CSV/Parquet, MT CSV/Parquet, 20 EDGE variants and 19 deviation cases; normalized T-22 fields |
| Generate | 16 | Retail small/medium/large, 13 other domains medium; per-table Arrow shapes and content IDs at seeds 42–46 |
| Stream | 2 | Retail order small/medium, each whole and prefix; field order, event count, content ID and JSONL digest at seeds 42–46 |
| Database | 24 | All 12 SQL Server testing cases, both fake and actual SQL Server 2022; normalized profiles and scenario content IDs |
| Verify CLI | 10 | Every unchanged gate scenario, release CLI reports on both seed-42 and seed-1042 inputs |
| Composite plan | 1 | Nine composites, each schema SHA-256 and planned per-table counts at every scale |
| Timing | 1 | Release-only full run, after equivalence, on a 4-vCPU runner |

This totals 103 recording files, plus the manifest. Profile column and table order are
stored explicitly because sorted JSON keys alone cannot preserve those orders. Generated
Parquet file hashes are informational: Arrow content IDs and shapes must agree even if a
platform produces different Parquet bytes. Stream JSONL digests are authenticated.

The 28 frozen domain/mode plans stay in `fixtures/plan.json`. The nine composite plans are
computed **only during recording**, using the release interpreter and frozen composite
schemas, and stored in `recorded/plan/composites.json`. Verification never runs the release
planner for planned counts. Missing composite entries or changed schema digests return
exit 2 naming the composite. Generated row-count checks remain for all 37 cases.

## Verification and timing

```bash
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/profile_1to1/verify.py --impl shape
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/verify.py --impl shape --domain retail --scale small
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/verify.py --impl shape --domain retail --scale small --negative-control
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/stream_1to1/verify.py --scale medium --negative-control
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/db_1to1/verify.py
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/db_1to1/verify.py --mssql "$SHAPE_TEST_MSSQL;Database=tempdb"
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/verify_1to1/verify.py
source scripts/env.sh && python benchmarks/vs_refengine/run.py --quick --dry-run
source scripts/env.sh && python benchmarks/vs_refengine/run.py --quick --out "$BENCH_OUT_DIR/results.json"
```

Baseline generation/stream seeds are exactly 42–46, candidate seed 1042. Every regenerated
baseline output must authenticate against its recording before comparison. Verify-CLI
checks fresh release reports against the recorded reports before comparing candidate
gates. Exit 0 means all checks pass, exit 1 means a regression, exit 2 means an unavailable
or untrustworthy input. Negative-control commands fail if any perturbation is missed.

Schema-v2 results contain `baseline` (kind `shape`, release version, commit and workloads)
and `shape`. `ratio_vs_baseline` is baseline median divided by candidate median, timed in
the same job. Each run uses fresh processes, excludes imports for API measurements,
holds the benchmark lock and interleaves implementations. Quick uses three repetitions;
full uses five. Equivalence precedes timing; generation and stream timed outputs are
verified again before numbers count. The startup gate remains 300 ms. T-19's candidate
regression rule and Nightly's green, subset and seven-night streak rules remain unchanged.

Missing or damaged recordings produce unavailable records with null medians. The committed
builder results reflect that actual unavailable run and publish no numbers. The lead
replaces them with acceptance measurements. Historical timing evidence remains in
`docs/plans/evidence/G1*`, `G3` and `G4`; those ratios are no longer gated.

## Active files and historical harnesses

| Files | Purpose |
|---|---|
| `setup_baseline.sh`, `paths.py` | Isolated release installation and shared environment paths |
| `record_baseline.py`, `recorded_store.py`, `recorded/check.py` | Lead capture, authenticated reads and store validation |
| `profile_1to1/baseline_dump.py`, `verify.py`, `bench.py`, `bench_cli.py` | Common profile normalization, T-22 and API/CLI timing |
| `domain_1to1/generate.py`, `verify.py`, `rowcounts.py`, `bench.py`, `bench_cli.py` | T-21 a–f, fixed seeds, row counts and timings |
| `stream_1to1/verify.py`, `stream_common.py`, `baseline_worker.py`, `bench.py` | Stream structure/statistics, authenticated events and timings |
| `db_1to1/verify.py`, `verify_1to1/verify.py` | Recorded database profiles and unchanged gate scenarios |
| `run.py`, `nightly.py`, `results.schema.json` | Equivalence-first orchestration, schema v2, shard verdicts and history |

Other harnesses and their committed goldens remain historical, unchanged. Importing
`_refpkg.py` raises `RuntimeError("the reference engine is archived (FRZ-01, 2026-10-11); this harness is historical")`.
Their offline unit tests retain every assertion and use test-only import stand-ins which
raise if an archived runtime is accessed; no archived engine is needed by those assertions.

Until the lead records the release timing snapshot, the performance-page generator shows
`release timing snapshot pending: recorded/timings_0.9.1.json` and a hidden owner marker.
The unchanged release-mode documentation owner check refuses that pending state. Once the
snapshot exists, regeneration automatically renders its source link and removes the marker.

Approved FRZ-01 correction: identifier component forms (email, company email, phone and URI)
are captured in each generation recording from the release provider's own template, full
component pools, integer bounds, run defaults and frozen column settings. Only random
selectors are replaced during this capture; formatting and provider selection stay with
the release implementation. Verification uses exact recorded literals and component sets,
never broader hand-written patterns. A changed frozen setting needs its matching re-record;
a missing form fails closed. The component-overlap threshold remains `>= 0.999`.

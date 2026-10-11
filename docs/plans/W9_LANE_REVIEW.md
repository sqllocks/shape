# W9 lane review (read-only), for the 0.10.0 integration

Written 2026-10-11 on `plan/launch-review`. Nothing was dispatched, merged, rebased or pushed
to any lane; every statement comes from reading the branches, the issues and the recorded CI
runs.

## How to read this

- **Base.** Every lane was cut from `main` at `7f700d3` (the plan commits `84ef8b6`/`faba663`
  are the first commits on each). `release/0.9.1` is `f49aa94`, 25 REL-091 commits past that
  base; `main` has not moved. So every lane is **25 commits behind `release/0.9.1`, 0 behind
  `main`**, and must be merged onto the release branch for 0.10.0.
- **CI.** The lead dispatched `ci.yml` by hand on seven lanes (2026-10-08/09). Jobs are read
  from the run's job list; failures are attributed by comparing with the base run on `main`
  `7f700d3` (run 37783656693: 23/25, failing `test (macos-latest, 3.11)` on the heavy
  bounded-memory test `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`,
  and `zero-network` on the two reference-engine harness tests in
  `tests/benchmarks/test_run_harness.py`) and with the `release/0.9.1` head run 38107947559
  (25/26, failing only `bench-quick` at the step "Pinned RefEngine baseline and venv", the
  FRZ-01 item). A failure that also fails on the base is marked *base*; a `bench-quick`
  failure at that pin step is marked *FRZ-01*. Only the rest is the lane's.
- **Overlap.** Two measurements: the files a lane changes that another lane also changes
  (predicts lane-vs-lane conflicts at integration), and a dry `git merge-tree` of the lane
  onto `release/0.9.1` (the conflicts the first merge will actually hit). `CHANGELOG.md` and
  `docs/plans/COMPLETION_PLAN.md` are touched by every lane and are left out of the pairwise
  lists; the changelog auto-merges except where noted.
- **Risks** cover dependencies, workflow edits, test deletions, skips and loosened checks
  found by diffing the lane against its base (`git diff --name-status`, grep for
  `pytest.skip|xfail|skipif`, removed `assert`/`def test_` lines).
- **Verdicts** are one of: ready to integrate; needs a fix round (listed); needs a lead
  decision.

Lane heads (2026-10-11): W9-01 `88a46ab`, W9-03 `a88683b`, W9-07 `07498ab`, W9-08 `e3cf7a5`,
W9-10 `a523717`, W9-11 `68f2b67`, W9-12 `cb17290`, W9-13 `1249af9`, W9-14 `aa7530b`,
W9-15 `4582655`, W9-16 `f05fdd1`; W8-07 (W9-16's dependency) `7a7e002`.

---

## W9-01 — PostgreSQL and MySQL live profiling (issue #2), `lane/W9-01` at `88a46ab`

**What it builds.** `shape.sources` entries `postgresql`, `postgres`, `mysql` in
`plugins/shape-databases` (`sources.py`, 449 lines): catalog Arrow schema with widths, zones,
decimals, UUID and JSON-text metadata; server-side batch reads (`batch_size` 65,536); whole-schema
`profile_database` with declared primary and foreign keys (`evidence: "declared"`), catalog row
counts, `TABLESAMPLE SYSTEM` / keyed CRC32 spread samples, `sampled_rows`, `sample_method`,
dataset `sampling`; `--tables` and `--sample-rows` on `shape profile`; bridge `profile` accepts
the URIs (API 1.2 additive args); credentials masked before job persistence; safe capture uses
the sampled population for count floors. Core changes: `profile()` dispatches a `://` source to
a plugin with `profile_database` (`src/shape/profile/reference/profile.py`), bridge `flow.py`,
`cli/main.py`, `privacy/redact.py`, `privacy/safe_profile.py`, `security/redact.py`.

**Against the issue's Wanted list.** Items 1–6 each have named tests in
`plugins/shape-databases/tests/test_sources.py` (negative and boundary cases listed in the
status file), plus `test_sources_services.py` (emulator and live round trips), the CLI surface
snapshot (additive: `--sample-rows`, `--tables`), the bridge schema, README, `docs/CLI.md`,
`docs/BRIDGE.md` Security, `CHANGELOG.md`. **Nothing on the Wanted list is missing.** Not yet
done: the nightly emulator run of the issue's test plan (`pytest -m emulator`, PostgreSQL 16 and
MySQL 8.4); the existing `databases-e2e` job already picks `test_sources_services.py` up, so one
nightly dispatch covers it. A lead-CI regression (SQL Server profile round trips) was found and
fixed in `88a46ab` with new generic regressions in `tests/privacy/test_sampled_capture_roundtrip.py`.

**CI on the head** (run 37869335295, 2026-10-09): 23 of 25 jobs pass. Failing: `zero-network`
(*base*: the two `test_run_harness.py` tests), `bench-quick` (*FRZ-01*). `test (macos-latest,
3.11)` passes on this lane (it fails on the base). **No lane-attributable failure.**

**Base.** `7f700d3` (main); 25 behind `release/0.9.1`. Dry merge onto the release: one conflict,
`docs/CLI.md` (both sides edited the profile section); `CHANGELOG.md`, `docs/API.md`,
`docs/BRIDGE.md`, `src/shape/cli/main.py` auto-merge.

**Overlap with other lanes** (own files, 24): `src/shape/cli/main.py` and
`tests/cli/cli_surface_v1.json` with W9-08; `src/shape/cli/main.py`,
`src/shape/bridge/handlers/flow.py`, `src/shape/profile/reference/profile.py` with W9-11;
`src/shape/bridge/handlers/flow.py`, `src/shape/profile/reference/profile.py`,
`src/shape/security/redact.py`, `docs/BRIDGE.md`, `docs/CLI.md`, the bridge schema files and
`tests/bridge/test_compat_1_1.py` with W9-16; `plugins/shape-databases/README.md` and
`shape_databases/testing.py` with W9-03. W9-13, W9-14 and W9-15 contain this lane whole.

**Risks.** No new dependency (`pyproject.toml` of the plugin gains three entry points only). No
test deleted; one removed test line; the only `pytest.skip` is the live test naming its missing
setting (the issue's test plan). Behaviour change in core privacy: category-disclosure floors now
use `sampled_rows` where present while sensitivity keeps the population count; covered by the
new round-trip tests. The `profile()` dispatch adds a plugin lookup on every `://` source.

**Verdict: ready to integrate.** Merge first (three lanes are stacked on it), resolve
`docs/CLI.md`, run `databases-e2e` once in nightly, and send the one-line doc fix for
`docs/databases/index.md` / `postgresql.md` / `mysql.md` (they claim integration-plugin reads
that do not exist at 0.9.1; with W9-01 landed they should point at the databases plugin).

## W9-03 — Type fidelity on write (issue #4), `lane/W9-03` at `a88683b`

**What it builds.** One Arrow-to-SQL map per dialect in `src/shape/builtins/sinks/sql.py`
(`tsql`, `tsql-fabric-warehouse`, `postgres`, `mysql`) and `shape_databases/_cloud.py`
(Snowflake, Databricks): integer widths kept, signed `int8` as `SMALLINT` on T-SQL (lead
decision 2026-10-08), unsigned to the next width, `float32` to `REAL`/`FLOAT(24)`, zoned
timestamps normalised to UTC with the instant kept to the microsecond, `TIME(p)`, decimals only
with explicit `(p,s)` (a declared `decimal` without dimensions is refused naming the column),
strings from `max_length` or a declared width, else unbounded (`TEXT`/`VARCHAR(MAX)`/`STRING`),
first-batch sizing removed; `shape generate --to ... --dry-run` prints the resolved DDL; docs and
changelog. Fabric Warehouse, SQL DB and Synapse writers (`plugins/shape-fabric`) follow the same
map. 38 files.

**Against the issue's Wanted list.** Items 1, 2, 3, 5 and 6 have named tests
(`tests/builtins/test_w9_03_types.py`, `tests/cli/test_w9_03_ddl.py`,
`plugins/shape-databases/tests/test_w9_03.py`, `plugins/shape-fabric/tests/test_w9_03.py`).
**Item 4 is not met and the lane has stopped on it:** 16 pinned assertions (the golden SQL
writer tests, W8-04 and W8-04b corpus assertions) fail because item 1 and item 2 change the
emitted DDL. The lane's byte audit of all 418 corpus outputs found exactly 206 changed DDL lines
(192 item-2 string defaults, 14 item-1 temporal types), 24 outputs refused for undeclared
decimals, and zero INSERT or value-byte differences. The lead's third comment on #4
(2026-10-09 02:10 UTC) authorised precisely those updates (the 192 string lines, the 14 temporal
lines, the 24 refusals with inputs unchanged, every changed line listed with its rule). The lane
head was committed at 00:55 UTC, before that comment, and its status still asks the question.
So the remaining work is the authorised pinned-assertion update, not a decision.

One consequence the status records only in passing needs the lead's eyes: the built-in
`retail` domain declares decimal columns without precision, so after this lane
`shape generate retail --to <any SQL sink>` and `shape seed retail` **refuse** with
"missing `address.lat` dimensions" (the lane rewrote `plugins/shape-databases/tests/test_seed.py`
to seed a copy of the retail schema with `precision=18, scale=2` added, and added a test that the
unchanged domain is refused). That follows the issue's "no silent (18,2)" rule, but it turns a
0.9.1 quick-start path (`--to postgresql://...` of a domain) into an error at 0.10.0 unless the
domain schemas declare `(p,s)`, and declaring them in the 14 domain schemas is a change to pinned
domain output that §6.4 and W8-04 guard. The lead decides which (declare precision in the domain
schemas as a separate lane, or document the refusal and the `columns` override).

**CI on the head:** **no run was dispatched** on this lane at all. The status records
CI-matched local runs (ruff, format, mypy 3.11 clean, 473 focused tests pass) and the broad
touched-area selections failing only the 16 pinned assertions in both kernels.

**Base.** `7f700d3`; 25 behind the release. Dry merge onto `release/0.9.1`: conflicts in
`CHANGELOG.md` and `docs/SINKS.md` (REL-091 edited both); `plugins/shape-fabric/README.md` and
`src/shape/cli/generation.py` auto-merge.

**Overlap with other lanes** (own files, 38): `src/shape/cli/generation.py` and `docs/SINKS.md`
with W9-07; `plugins/shape-databases/README.md` and `shape_databases/testing.py` with W9-01;
`plugins/shape-fabric/README.md` with W9-12; `docs/SINKS.md` with W9-16. The largest
lane-vs-lane surface in the set: three sinks' modules in `shape-databases`, four in
`shape-fabric`, the core `sql.py` writer.

**Risks.** No dependency change. 57 removed test lines: the five `test_seed.py` functions that
grep showed as removed are rewritten in place (same names, new `retail_schema` fixture), and the
removed assertions are the old DDL expectations (`VARCHAR(255)`, `NUMBER(38,0)`, naive-UTC
timestamps) replaced by the exact new types; the status says no assertion was skipped, xfailed
or loosened, and the diff agrees. New emulator and live tests are marked and were not run. The
behaviour change is wide (every SQL sink's DDL) and intended by the issue.

**Verdict: needs a lead decision, then a fix round.** Decision: the domain-schema consequence
above. Fix round: apply the lead's 2026-10-09 02:10 authorisation (update the 16 pinned
assertions as itemised, inputs unchanged, each line listed with its rule in the status file),
rerun the corpus tests, then dispatch CI (none exists yet) and run `databases-e2e` and
`sqlserver-e2e` nightly once for the catalog read-backs.

## W9-07 — Delta table features on write (issue #8), `lane/W9-07` at `07498ab`

**What it builds.** Opt-in Delta features on the built-in Delta sink
(`src/shape/builtins/sinks/delta.py`, new `delta_features.py`): `table_properties` on create
and by commit on append; `not_null` and `check` constraints added after the first commit with
atomic refusal of a violating batch; `column_mapping="name"` (reader 2 / writer 5, physical-name
metadata); `deletion_vectors=true` as the table feature only (lead decision 2026-10-08: Shape
never writes deletion-vector actions; no upsert); `generated_columns` with validation and the
documented date-partition example; `timestamp_ntz=true` (reader 3 / writer 7); default writes
byte-for-byte unchanged; `--dry-run` reports the protocol and features; `shape doctor` reports
them; capability refusals name the installed and required deltalake version (verified against
1.6.6, floor `>=0.17` unchanged); docs (`docs/SINKS.md`, `docs/FABRIC_PLATFORM.md`) and changelog.
11 files.

**Against the issue's Wanted list.** Items 1–9 each have named tests in
`tests/generation/test_delta_features.py` (negative and boundary cases listed). The two scope
amendments are the lead's own (no `upsert`, no `rename_columns`), so the issue's "heavy
deletion-vector upsert at 10M rows" test-plan item is correctly absent. Item 9's nightly tables
exist as six `live`-marked parameters in `tests/builtins/test_abfss_sink_live.py` (not run).
**Nothing on the amended Wanted list is missing.**

**CI on the head** (run 37873268070, 2026-10-09): 14 of 25 jobs pass. **Lane-attributable:**
all nine `test (os, python)` jobs fail at the `Run lint-imports` step, and `min-versions` fails
the same contract test (3 failed): import-linter contract "Built-ins load only through the plugin
registry (plan 4.3)" is broken by `src/shape/cli/generation.py` importing
`shape.builtins.sinks.delta` (l.302) and `shape.builtins.sinks.delta_features` (l.286) directly.
The test suites themselves did not get to run on the matrix, so the lane's 53 acceptance tests
have only the builder's local runs behind them. Also failing: `zero-network` (*base*),
`bench-quick` (*FRZ-01*).

**Base.** `7f700d3`; 25 behind. Dry merge onto `release/0.9.1`: one conflict, `docs/SINKS.md`;
`src/shape/builtins/sinks/delta.py` (also edited by REL-091) and `src/shape/cli/generation.py`
auto-merge.

**Overlap with other lanes** (own files, 11): `src/shape/cli/generation.py` and `docs/SINKS.md`
with W9-03; `docs/FABRIC_PLATFORM.md` with W9-11 and W9-12; `docs/SINKS.md` with W9-16.

**Risks.** No dependency or floor change; no workflow edit; no pinned golden or corpus change
(item 7 is tested). One `pytest.skip` (live test naming `SHAPE_LIVE_DELTA_URI`). The builder's
final focused runs record `2 failed` in both kernels from the DuckDB delta-extension download
(environment); CI must confirm.

**Verdict: needs a fix round.** (1) Satisfy the §4.3 import contract: the dry-run feature
report and the doctor check must reach the Delta sink through the plugin registry (`default_host`
/ the sink's entry point) or through a module outside `shape.builtins`, not by importing
`shape.builtins.sinks.*` from `shape.cli`; `lint-imports` must pass locally before the push.
(2) Dispatch CI again; the matrix has never run this lane's tests. (3) Resolve `docs/SINKS.md`
on the rebase; integrate after or together with W9-03, which edits the same two files.

## W9-08 — Kafka and Event Hubs message metadata (issue #9), `lane/W9-08` at `e3cf7a5`

**What it builds.** Emit options on both transports (`key` from a column or `a|b`, static and
column `headers`, `partition` / `partition_key`, `timestamp` broker or `event_time`; the D-12
key moves to header/property `shape-key` when a column is the key; `shape-*` names cannot be
shadowed; unknown columns refused before the first message); read options `with_key`,
`with_headers`, `with_timestamp` (+ `_shape_properties`, `_shape_partition_key` on Event Hubs)
adding typed metadata columns that drift ignores unless selected; `shape emit` and
`shape stream-profile` flags; docs (`docs/EMIT.md`, `docs/CLI.md`, both READMEs) and changelog.
Core: `src/shape/streaming/messages.py` (`StreamMessage` gains `key`, `headers`, `properties`,
`partition_key`; `decode_messages` gains the four flags), new `streaming/emit/metadata.py`,
`cli/emit.py`, `streaming/cli.py`, `diff/core.py`, `drift/engine.py`. 29 files.

**Against the issue's Wanted list.** Items 1–5 each have named tests
(`tests/streaming/test_message_metadata.py`, each plugin's `tests/test_message_metadata.py`,
`test_wanted4_cli_options`, `test_wanted5_documented_options`); the CLI surface snapshot was
regenerated additively as the lead allowed (comment 2026-10-08). Emulator and live keyed round
trips were added but not run. **Nothing on the Wanted list is missing.**

**CI on the head** (run 37866298685, 2026-10-09): 22 of 25 pass. Failing: `zero-network`
(*base*), `test (macos-latest, 3.11)` (*base*, the heavy memory test), `bench-quick`
(*FRZ-01*). **No lane-attributable failure.**

**Base.** `7f700d3`; 25 behind. Dry merge onto `release/0.9.1`: three conflicts,
`docs/CLI.md`, `docs/EMIT.md`, `tests/cli/test_remote_confirmation.py` (REL-091 and the lane
both changed `test_emit_with_yes_gets_past_the_confirmation`); `src/shape/cli/emit.py`,
`src/shape/cli/main.py`, `src/shape/diff/core.py` auto-merge.

**Overlap with other lanes** (own files, 29): `src/shape/cli/main.py`, `docs/CLI.md`,
`tests/cli/cli_surface_v1.json` with W9-01; `src/shape/cli/main.py` with W9-11;
`src/shape/drift/engine.py` with W9-12; `docs/CLI.md` with W9-16. W9-09 (not started) will edit
the same `emitter.py`, `source.py`, `formats.py` and `messages.py` paths and must be built on
top of this lane (see `docs/plans/wp/W9-09.md`).

**Risks.** No dependency change. Test edits to flag for the lead: in both plugins'
`tests/test_live.py` the existing `need()` helper changed from `assert value, "<NAME> is not set
(live tests need it)"` (the Kafka file's docstring: "nothing is silently skipped") to
`pytest.skip(f"missing live setting: {name}")`. That is the databases plugin's convention and the
issue's own test-plan wording, but it changes the live-test policy of two existing files from
fail to skip; the lead should accept it explicitly or ask for the old behaviour back. The
`test_remote_confirmation.py` change strengthens the test with a core-only fake emitter (it also
fixes the real-broker flush that W9-10 and W9-11 hit). The additive CLI snapshot refresh is
listed in the status file.

**Verdict: ready to integrate**, after the rebase resolves the three conflicts and the lead
accepts (or reverses) the live-test skip change. Run `kafka-e2e` and `eventhubs-e2e` nightly
once for the keyed round trips.

## W9-10 — dbt II (issue #11), `lane/W9-10` at `a523717`

**What it builds.** In `plugins/shape-dbt`: `from-dbt` reads `semantic_models` (entities to
keys, dimensions to types with granularity, measures with aggregation; conflicts named) and
lists singular tests in the metadata document without parsing their SQL; `to-dbt-tests` writes
singular tests for rules generics cannot express (`pattern` as dialect regexp for duckdb,
postgres, snowflake, bigquery; true-rate bounds; non-numeric min/max), idempotent with `--merge`,
exact round trip; `dbt-report` gains `impact` (`format: shape-dbt-impact`, `version: 1`) listing
metrics and exposures per drifted or failing column; `dbt-seeds --semantic-models`;
`docs/DBT.md`; the jaffle-shop example gains a semantic model, metric, exposure and singular
test; changelog. 16 files, all in the plugin, docs and example.

**Against the issue's Wanted list.** Items 1–6 each have named tests in
`plugins/shape-dbt/tests/test_w9_10.py` (20 cases) and `test_example_documents_all_resources`.
The nightly `dbt-build` job's expected counts move from 31/23 to 32/24 for the new singular test
(explicit in `test_dbt_build.py`; the two removed assertion lines are those counts). **Nothing on
the Wanted list is missing.** The `-m dbt` build against DuckDB (nightly) has not run.

**CI on the head** (run 37859301967, 2026-10-08): 23 of 25 pass. Failing: `zero-network`
(*base*), `test (macos-latest, 3.11)` (*base*). **No lane-attributable failure.** (The
builder's local full-suite stall in `test_remote_confirmation.py` was the installed-Kafka flush
that W9-08 fixes; CI did not hit it.)

**Base.** `7f700d3`; 25 behind. Dry merge onto `release/0.9.1`: one conflict, `docs/DBT.md`;
`plugins/shape-dbt/src/shape_dbt/commands.py` auto-merges.

**Overlap with other lanes:** none beyond the changelog and plan files. The most isolated lane
in the set.

**Risks.** No dependency change ("No new dependency was added to core or plugin"); no skip or
xfail; the two changed assertions are the documented count bump. `dbt-core` stays out of the
dependency set (the plugin reads files only).

**Verdict: ready to integrate.** Resolve `docs/DBT.md` on the rebase; run the nightly
`dbt-build` job once to confirm 32 resources / 24 tests.


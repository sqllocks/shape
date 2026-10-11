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

## W9-11 — Fabric Eventhouse source (issue #12), `lane/W9-11` at `68f2b67`

**What it builds.** `shape.sources` entry `eventhouse`
(`plugins/shape-fabric/src/shape_fabric/eventhouse_source.py`): schema from
`.show table T schema as json`, rows paged through the query endpoint in a stable order,
`batch_size`, emitter sign-in (`token`, `SHAPE_EVENTHOUSE_TOKEN`, Entra), KQL-to-Arrow type map
(with `decimal256(57,28)` for the full KQL decimal range), `sample_rows` via `T | sample N`,
catalog counts from `.show table T details`, whole-database multi-table profile with the
emitter's `_shape_table`/`_shape_seq` columns excluded, `dedupe=true`; `shape profile`,
`shape diff` and the bridge accept the URI; docs (`docs/plugins/fabric-writers.md`,
`docs/FABRIC_PLATFORM.md`) and changelog; fake transport in `shape_fabric/testing.py`; one
emulator read-back in `test_kusto_emulator.py`; one live read-back. Core: a source may expose
`profile_tables` and the CLI/bridge resolve such a source through `profile_or_load`
(`src/shape/profile/reference/profile.py`, `sources.py`, `bridge/handlers/flow.py`,
`cli/main.py`). 15 files.

**Against the issue's Wanted list.** Items 1–5 each have named tests in
`plugins/shape-fabric/tests/test_eventhouse_source.py` (46 tests, both kernels). **Nothing on
the Wanted list is missing.** The kustainer emulator job and the live read-back were not run.

**CI on the head** (run 37859304475, 2026-10-08): 23 of 25 pass. Failing: `zero-network`
(*base*), `test (macos-latest, 3.11)` (*base*). **No lane-attributable failure.** The status
file's two blockers (42 mypy errors, the Kafka flush stall) were the builder's environment;
CI's mypy and matrix are green, and W9-08 fixes the stall.

**Base.** `7f700d3`; 25 behind. Dry merge onto `release/0.9.1`: one conflict,
`plugins/shape-fabric/tests/test_kusto_emulator.py` (REL-091 touched it too);
`plugins/shape-fabric/src/shape_fabric/testing.py`, `src/shape/cli/main.py`,
`docs/FABRIC_PLATFORM.md`, `docs/plugins/fabric-writers.md` auto-merge.

**Overlap with other lanes** (own files, 15): `src/shape/cli/main.py`,
`src/shape/bridge/handlers/flow.py`, `src/shape/profile/reference/profile.py` with W9-01;
`src/shape/cli/main.py` with W9-08; `src/shape/bridge/handlers/flow.py`,
`src/shape/profile/reference/profile.py` with W9-16; `docs/FABRIC_PLATFORM.md` with W9-07 and
W9-12. The important one: W9-01 and W9-11 each add a plugin-capability hook to `profile()`
(`profile_database` and `profile_tables`) in the same function; they do not conflict textually
against the release, but against each other they will, and two hooks for "a source that yields
several tables" is one too many. The lead should integrate W9-01 first and have W9-11 rebase
onto it, either keeping both hooks with a comment or folding `profile_tables` into the
`profile_database` dispatch.

**Risks.** No dependency change (one entry point added to the fabric plugin). No test
deletions; the only `pytest.skip` is the live test naming its setting. The core change is
additive but sits in the reference profiler's entry function, which the equivalence gates
protect: the diff keeps file profiling untouched, and CI's parity-adjacent tests passed.

**Verdict: ready to integrate, after W9-01.** Rebase onto the release with W9-01 landed,
reconcile the two hooks, resolve `test_kusto_emulator.py`, and run the nightly kusto emulator
job once.

## W9-12 — Power BI semantic models II (issue #13), `lane/W9-12` at `cb17290`

**What it builds.** In `plugins/shape-fabric`: `profile-model` records each table's measures
and the dataset's roles (verified against installed `semantic-link-sempy` 0.14.2:
`list_measures`, `connect_semantic_model(..., readonly=True)`, `evaluate_dax(..., role=)`; the
exact calls and source hashes are in the status file); `semantic-model://` reads under a role
(`as_role`), unknown roles refused with the names listed; `export-model --from-profile`
carries measures and roles into the `.bim`; `known-answer --measures from-profile` with
uncomputable DAX under `skipped`; `shape diff` reports `measure_change` and `role_change`,
scored zero and excluded from the numeric gate; W2-06's `hidden: true` gap closed through a new
optional source hook `profile_metadata` in `src/shape/api.py` (the reference profiler directory
is byte-for-byte unchanged, checked with `git diff --exit-code`); new `docs/SEMANTIC_MODEL.md`,
`docs/FABRIC_PLATFORM.md`, plugin pages, changelog. Core: `src/shape/drift/{core,engine,
model_metadata,policy,semver}.py`, `src/shape/api.py`. 24 files.

**Against the issue's Wanted list.** Items 1–6 each have named tests in
`plugins/shape-fabric/tests/test_w9_12.py` (48 tests, both kernels), including the item-5
parity test both ways. **Nothing on the Wanted list is missing.** The live measure/role fixture
is an Owner decision the issue itself defers.

**CI on the head** (run 37866301140, 2026-10-09): 15 of 25 pass. **Lane-attributable:** all
nine `test (os, python)` jobs and `min-versions` fail the same two tests:
`tests/project/test_semver_cli.py::test_project_schema_lists_every_change_kind` (the
`shape.yml` JSON Schema `src/shape/schemas/shape-project-v1.schema.json` lists every drift kind
with its `breaking|additive|cosmetic` enum; `measure_change` and `role_change` are not in it)
and the docs-inventory assertion in `tests/release/test_docs.py` (the new
`docs/SEMANTIC_MODEL.md` is not in the documented page list). Everything else on the matrix
passed (17,877 tests). Also failing: `zero-network` (*base*).

**Base.** `7f700d3`; 25 behind. Dry merge onto `release/0.9.1`: three conflicts,
`CHANGELOG.md`, `docs/plugins/authoring.md`, `docs/plugins/fabric-commands.md` (REL-091 edited
all three); `docs/FABRIC_PLATFORM.md`, `docs/plugins/cloud-sources.md`,
`plugins/shape-fabric/README.md` auto-merge.

**Overlap with other lanes** (own files, 24): `src/shape/drift/engine.py` with W9-08 (both
add kinds that drift ignores or scores zero); `docs/FABRIC_PLATFORM.md` with W9-07 and W9-11;
`plugins/shape-fabric/README.md` with W9-03.

**Risks.** No dependency change (the plugin's `semantic-link` extra floor stays `>=0.8`; an
older install gets a named error). No skip or xfail; the 6 removed test lines are rewrites in
`test_w9_12.py`'s own module and the Fabric report registry test gained two `NO_THRESHOLD`
entries. Core drift semver tables gain two kinds; the project schema must follow (the failing
test exists to enforce exactly that). 20 setup errors in the builder's broad runs were the
DuckDB delta-extension download (environment).

**Verdict: needs a fix round** (small): add `measure_change` and `role_change` (enum
`breaking|additive|cosmetic`) to `src/shape/schemas/shape-project-v1.schema.json` next to the
other kinds, and list `docs/SEMANTIC_MODEL.md` where `tests/release/test_docs.py` expects every
docs page (the `mkdocs.yml` nav / docs index). Then re-dispatch CI; integrate after W9-08
because of `drift/engine.py`.

## W9-13 — BigQuery sink and source (issue #14), `lane/W9-13` at `1249af9`

**What it builds (on the pushed head): nothing beyond W9-01.** The branch is `lane/W9-01`'s
head plus one commit that adds `docs/plans/lane_status/W9-13.md`. The status file says the
partial implementation (a `bigquery` module, `FakeBigQuery`, acceptance tests for Wanted 1–7,
docs) "remains local and uncommitted" in the builder's sandbox and was not pushed, and that
the lane stopped on a Wanted-3 question: Arrow decimals BigQuery cannot create (negative scale,
scale above precision) under the issue's inequalities.

**Against the issue's Wanted list.** Items 1–7 are all missing from the branch. The lead
answered the question on #14 at 2026-10-09 02:10 UTC (require `0 <= scale <= precision`, refuse
otherwise before any connection, tests for both boundaries); the lane's head was committed at
01:37 UTC, so the answer has not been acted on.

**CI on the head:** none dispatched. Local checks recorded against the uncommitted tree
(ruff, format, mypy 3.11 clean; 849 passed, 1 failed in both kernels: the databases plugin's
exact entry-point conformance list without the new entries).

**Base.** `lane/W9-01` at `88a46ab` (contains W9-01 whole), itself on `7f700d3`; 25 behind
the release. Dry merge onto `release/0.9.1` conflicts only where W9-01 does (`docs/CLI.md`).

**Overlap.** Identical to W9-01's; nothing of its own.

**Risks.** The real risk is lost work: the partial implementation exists only in a builder
sandbox that may no longer exist. The `[bigquery]` extra (`google-cloud-bigquery`,
`google-cloud-bigquery-storage`) is specified by the issue and not yet in any pushed file.

**Verdict: needs a fix round, and it is not a 0.10.0 candidate as it stands.** Resume from the
lead's decision: push the partial tree (or rebuild it from the issue), add the two decimal
boundary tests, update the conformance entry-point list, then dispatch CI. It depends on W9-01
landing first and must be rebased onto the release after that.

## W9-14 — Amazon Redshift sink and source (issue #15), `lane/W9-14` at `aa7530b`

**What it builds (on the pushed head): nothing beyond W9-01.** `lane/W9-01` plus
`docs/plans/lane_status/W9-14.md`. The partial Redshift module, `FakeRedshift`, `FakeS3`, AWS
fakes, acceptance tests (38 red, then 41 passing locally) and docs stay uncommitted. The lane
stopped on Wanted 2: `redshift_connector` binds parameters server-side, and Redshift's `COPY`
needs the S3 location and IAM role as string literals, which the issue's "nothing in a statement
built from a value" rule forbids.

**Against the issue's Wanted list.** Items 1–7 are all missing from the branch. The lead
answered on #15 at 2026-10-09 02:10 UTC (a COPY-only exception for the two non-secret values,
validated against two regular expressions, single-quoted, marked for the security linter, with
rejection and quote-injection tests); the head was committed at 01:38 UTC.

**CI on the head:** none. Local checks on the uncommitted tree: ruff 1 error (a long SQL
line), mypy clean, 997 passed and 2 failed in both kernels (the conformance entry-point list;
one CLI fixture missing its schema version).

**Base.** `lane/W9-01` at `88a46ab`; 25 behind the release; conflicts as W9-01.

**Overlap.** Identical to W9-01's.

**Risks.** Lost work, as W9-13. The `[redshift]` extra (`redshift-connector`, `boto3`) is not in
any pushed file. The PostgreSQL emulator must not be used to claim Redshift behaviour (issue).

**Verdict: needs a fix round; not a 0.10.0 candidate as it stands.** Resume from the lead's
decision (apply the two validation patterns and the tests it names), fix the two local failures
and the lint error, push, dispatch CI; after W9-01.

## W9-15 — Oracle Database sink and source (issue #16), `lane/W9-15` at `4582655`

**What it builds (on the pushed head): nothing beyond W9-01.** `lane/W9-01` plus
`docs/plans/lane_status/W9-15.md`. The partial implementation (thin-mode array DML, version and
parameter reads, 128-byte quoted identifiers, the type map, password and wallet references, the
empty-string rule, catalog sampling, `FakeOracle`, 64 passing local tests) stays uncommitted.
The lane stopped on Wanted 6: python-oracledb 3.0's `fetch_df_batches` cannot read RAW, BLOB,
CLOB or INTERVAL columns that Wanted 4 writes.

**Against the issue's Wanted list.** Items 1–7 are all missing from the branch. The lead
answered on #16 at 2026-10-09 02:10 UTC (keep `fetch_df_batches` as the default; a bounded
cursor fallback with `fetchmany(batch_rows)` and `fetch_lobs=False` for those types, converted
to Arrow per batch, provenance `read_path: "cursor"`, tests that both paths agree); the head was
committed at 01:38 UTC.

**CI on the head:** none. Local checks on the uncommitted tree: ruff 2 errors, strict mypy 2
errors in the partial module, 929 passed and 2 failed in both kernels (conformance list; a CLI
fixture's schema format).

**Base.** `lane/W9-01` at `88a46ab`; 25 behind the release; conflicts as W9-01.

**Overlap.** Identical to W9-01's. The issue also adds an `oracle` service to
`ci/emulators/docker-compose.yml` and a nightly emulator run, which will touch the same compose
file W9-16 edits.

**Risks.** Lost work, as W9-13. The `[oracle]` extra (`oracledb>=3.0`) is not in any pushed
file.

**Verdict: needs a fix round; not a 0.10.0 candidate as it stands.** Resume from the lead's
decision, push, dispatch CI; after W9-01.

## W9-16 — Apache Iceberg sink and source (issue #17), `lane/W9-16` at `f05fdd1`

**What it builds.** Built-in `iceberg` sink and source beside Delta
(`src/shape/builtins/sinks/iceberg.py`, `sources/iceberg.py`, `_iceberg.py`, `catalog.py`):
`iceberg+file://<warehouse>/<ns>/<table>` with a SQLite catalog created on first write, and
`iceberg://<catalog>/<ns>/<table>` for catalogs named in `~/.pyiceberg.yaml` or by options
(`rest`, `sql`, `glue`, `hive`; credentials as references only); overwrite and append, format
version 2 (1 accepted), partition transforms checked against column types, micro-batch
snapshots, schema-mismatch refusal, `shape.fingerprint` in the snapshot summary; the documented
type map with native nested types; `snapshot_id` / `as_of` reads with provenance; namespace
profiles; `shape generate --to`, `shape profile` and the bridge (API 1.2, additive); docs
(`docs/SINKS.md`, `docs/CLI.md`, `docs/BRIDGE.md`, `docs/FINGERPRINT.md`, builtins inventory)
and changelog. Also: the `[iceberg]` extra in `pyproject.toml` (lead-approved wording
`pyiceberg[pyarrow,sql-sqlite,pyiceberg-core]>=0.9`), `.github/workflows/ci.yml` (the core
`test`, `min-versions` and `zero-network` jobs now install `[iceberg]`), `nightly.yml` (a new
Iceberg REST-catalog emulator job), `ci/emulators/docker-compose.yml` (REST catalog + MinIO).
33 files of its own, on top of W8-07.

**Against the issue's Wanted list.** Items 1–5 each have named tests in
`tests/builtins/test_iceberg.py`, `tests/bridge/test_iceberg.py`,
`tests/security/test_iceberg_credentials.py` (105 tests, both kernels; a 0.9.1-floor run of
PyIceberg too), plus the emulator module (not run; no Docker in the builder's session).
**Nothing on the Wanted list is missing.** Item 4's nested-column profiling "as W8-07 defines"
is why the lane sits on W8-07.

**CI on the head** (run 37866440596, 2026-10-09): 13 of 25 pass. **Lane-attributable:** all
nine `test (os, python)` jobs and `min-versions` fail the same two tests:
`tests/release/test_docs.py::test_install_page_lists_every_offline_lock_set` (`docs/INSTALL.md`'s
extras list lacks `iceberg`) and `tests/release/test_shipped_data.py::test_the_repository_passes`
(`shape/builtins/_iceberg.py` imports `urllib.request`, "a network client, outside an explicit
fetch module"); the `build` job fails the same shipped-data check
(`scripts/check_shipped_data.py --wheel`). The other 18,076 tests passed. Also failing:
`zero-network` (*base*; its own zero-network tests may add to it, the log was not read),
`bench-quick` (*FRZ-01*).

**Base.** `lane/W8-07` at `7a7e002` (contained whole), on `7f700d3`; 25 behind the release.
**W8-07 is itself `wip`** (POST_LAUNCH item 9: 4 failures and 36 setup errors in `make check`
on its base), so W9-16 cannot land before W8-07 is accepted or W9-16 is rebased without it.
Dry merge onto `release/0.9.1`: four conflicts, `.github/workflows/ci.yml` (REL-091 changed the
same jobs), `docs/BRIDGE.md`, `docs/CLI.md`, `docs/SINKS.md`; `nightly.yml`, `pyproject.toml`,
`src/shape/io/targets.py`, the bridge schema files auto-merge.

**Overlap with other lanes** (own files, 33): `src/shape/bridge/handlers/flow.py`,
`src/shape/profile/reference/profile.py`, `src/shape/security/redact.py`, `docs/BRIDGE.md`,
`docs/CLI.md`, the bridge request schema and `tests/bridge/test_compat_1_1.py` with W9-01;
`src/shape/bridge/handlers/flow.py`, `src/shape/profile/reference/profile.py` with W9-11;
`docs/SINKS.md` with W9-03 and W9-07; `docs/CLI.md` with W9-08. The widest core footprint after
W9-01, and the only lane that edits the workflows and compose file (W9-15 will too).

**Risks.** New optional dependency (approved) that the core CI jobs now install on every PR
(a deliberate choice recorded in the status file: "actual SQLite/file acceptance runs on every
PR"; the lead should confirm the extra install surface on `min-versions` and `zero-network`).
Workflow edits conflict with REL-091's. The shipped-data policy violation is real (a network
client imported in a built-in module); the fix is the repository's "explicit fetch module"
pattern, not an allow-list. No skip, xfail or deleted test; the single skip is the pre-existing
optional `shape_databases` one. New persisted schema property `format=shape.iceberg.arrow-schema`,
`version=1` with a compatibility test (W1-01 rule met).

**Verdict: needs a fix round, after W8-07.** (1) Move the `urllib.request` use in
`src/shape/builtins/_iceberg.py` behind the explicit fetch module the shipped-data check
accepts; (2) add `iceberg` to `docs/INSTALL.md`'s offline-lock extras list; (3) rebase onto the
release and resolve `ci.yml`, `docs/BRIDGE.md`, `docs/CLI.md`, `docs/SINKS.md`; (4) re-dispatch
CI and run the new nightly Iceberg emulator job once. Land only once W8-07 is accepted.

---

## Recommended integration order for 0.10.0

Dependencies first: W9-13, W9-14 and W9-15 contain W9-01 and need it merged before they can
be rebased; W9-16 contains W8-07 and needs it accepted. Then the order that keeps the merges
small: isolated lanes early, lanes that share core files right after the lane they share them
with, and the lanes still in a fix round or waiting on a decision last so the base they rebase
onto stops moving.

| # | Lane | Why here | Conflicts to expect at that point |
|---|---|---|---|
| 1 | **W9-01** | ready; three lanes stack on it; smallest core footprint of the source lanes | `docs/CLI.md` vs the release |
| 2 | **W9-10** | ready; no overlap with anything | `docs/DBT.md` vs the release |
| 3 | **W9-08** | ready; shares only `cli/main.py` (W9-01, already in) and `drift/engine.py` (W9-12, later) | `docs/CLI.md`, `docs/EMIT.md`, `tests/cli/test_remote_confirmation.py` vs the release; `cli/main.py` vs W9-01 (adjacent hunks) |
| 4 | **W9-11** | ready after W9-01; its `profile()` hook must be reconciled with W9-01's | `test_kusto_emulator.py` vs the release; `profile.py`, `flow.py`, `cli/main.py` vs W9-01 |
| 5 | **W9-12** | one small fix round; shares `drift/engine.py` with W9-08 (in) | `CHANGELOG.md`, `docs/plugins/authoring.md`, `docs/plugins/fabric-commands.md` vs the release; `drift/engine.py` vs W9-08 |
| 6 | **W9-07** | one fix round (import contract); shares `cli/generation.py` and `docs/SINKS.md` with W9-03 (next) | `docs/SINKS.md` vs the release |
| 7 | **W9-03** | lead decision + fix round; largest sink-side diff, so it goes after the lanes it would otherwise displace | `CHANGELOG.md`, `docs/SINKS.md` vs the release; `generation.py`, `SINKS.md` vs W9-07; `shape-databases` README and `testing.py` vs W9-01; `shape-fabric` README vs W9-12 |
| 8 | **W8-07**, then **W9-16** | W8-07 must be accepted first (it is not in this review's scope and is `wip`); W9-16 then needs its fix round and a rebase that resolves the workflow conflict | `ci.yml`, `docs/BRIDGE.md`, `docs/CLI.md`, `docs/SINKS.md` vs the release; `profile.py`, `flow.py`, `redact.py` vs W9-01 and W9-11 |
| 9 | **W9-13, W9-14, W9-15** | not candidates until their implementations are pushed and CI-green; they rebase onto the release with W9-01 in, in any order (they do not touch each other's files; all three add to `shape-databases` and its conformance list, so land them one at a time) | the conformance entry-point list, `shape-databases` README/pyproject, and for W9-15 the compose file vs W9-16 |

Two things to settle before step 1: run FRZ-01 (or accept `bench-quick` red) so that a green
matrix means something again, and decide whether lanes merge or rebase onto `release/0.9.1`
(every lane is 25 commits behind it; a merge keeps the builders' checkouts valid). Nothing in
this order changes a decision, gate or tolerance.

Lanes ready today, in order: W9-01, W9-10, W9-08, W9-11. Fix rounds: W9-12 (two one-line
fixes), W9-07 (import contract), W9-16 (two fixes and a rebase, after W8-07). Lead decision:
W9-03 (domain decimal precision), then its fix round. Not candidates: W9-13, W9-14, W9-15.

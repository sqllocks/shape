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


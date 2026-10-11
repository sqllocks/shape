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


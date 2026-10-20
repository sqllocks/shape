# FRZ-01 / issue 20: explicit recorded-error category

## Owner decision and scope

The 2026-10-20 Owner decision permits tested not-comparable handling only when the
recorded Shape 0.9.1 result itself is an error or unsupported. Each workload and reason
must be committed and reviewed, reported separately and never counted as a pass.
Every successful recording still compares with unchanged thresholds. No re-recording;
capture-clock exclusions apply only to runtime timestamps; reference-label substitutions
normalize names only, never data values. The exact authorization is also recorded in
`public-b-2026-10-18/questions.md` on the lead scratch branch.

This implements the October 11 redefined Wanted contract, using the existing authenticated
0.9.1 recordings. The reference engine remains retired. No recording, fixture, tolerance,
product output, workflow, configuration, release or secret is changed.

## Implemented behavior

`benchmarks/vs_refengine/comparison_contract.py` is the reviewed workload/reason registry:

| Workload | Recorded result | Reason |
| --- | --- | --- |
| `profile:edge/x_pq_list.parquet` | `TypeError` | List-valued Parquet input has no baseline profile values. |
| `profile:edge/x_pq_nested.parquet` | `TypeError` | Struct-valued Parquet input has no baseline profile values. |

The profile verifier authenticates the recording and input digests first. An exact registered
error emits `NOT COMPARABLE` and exit 3, without executing candidate profiling. Successful
recorded values retain all existing comparisons even under a registered workload name.
The runner reports structured status `not_comparable`, workload and reason, and omits timing.
Nightly reauthenticates the refusal and lists it separately from `passed`, including history
and console output. Forged workloads, reasons, result identities, timings, exit codes,
first-run pass claims and tampered recordings fail. An overall green verdict can account
for reviewed refusals while the pass count excludes them. Issue 6 uses the same category.

## Targeted verification

All commands used `/home/agent/bin/reap.py`, pinned uv 0.8.17 with the locked docs/dbt/benchmark
groups, and no dependency synchronization. No local full run or raw archive was created.

- Initial new acceptance tests: 11 red against the unchanged base (missing contract).
- Stronger old-behavior check: with only the new registry present, restoring base verifier,
  runner, Nightly and schema produced exactly 3 red behavior cases; candidate bytes restored.
- Independent review identified kind/dataset identity controls: 2 red before the guard.
- Independent review also identified an ordinary-pass claim for a registered recorded error:
  1 red before the guard. It now fails instead of receiving pass credit. The old Nightly
  synthetic value fixtures explicitly model successful recordings in a module-local fixture;
  their assertions are unchanged. Real-refusal tests use both authenticated error recordings.
- Final focused contract/profile/Nightly/timing/domain/store suite: 107 passed in 2.75 seconds.
- The preceding focused suite passed 106 cases in Python mode; final category controls were
  rerun in Python mode after the review corrections.
- Ruff and format checks pass for all six changed Python files. Configured mypy passes for
  703 source files; documentation wording has zero hits and the user-facing check is clean.
- Deterministic Tuesday/Sunday time-only parsing control preserves the differing weekday
  values despite the current date (Tuesday) coinciding with the capture weekday.

## Remaining value acceptance: parked, not resolved

The original failures and authenticated input/archive digests are in
`public-b-2026-10-18/FRZ-01-evidence.md` on the lead scratch branch. Read-only source review
confirms these four workloads produced values in 0.9.1 and cannot use this category:

| Workload | Unresolved value mismatch |
| --- | --- |
| `profile:edge/x_time_only.csv` | Recorded weekday histogram `[0,1,0,0,0,0,0]`; Sunday yields `[0,0,0,0,0,0,1]`. |
| `profile:edge/x_time_ampm.csv` | Recorded `[0.5,0.5,0,0,0,0,0]`; Sunday yields `[0.5,0,0,0,0,0,0.5]`. |
| `generate:capital_markets:medium` | Synthetic sector/industry data values have vocabulary overlap 0 vs floor 0.999, failed TVD checks, record coherence 0 vs floor 0.995. |
| `generate:healthcare:medium` | Synthetic procedure codes/descriptions have vocabulary overlap 0 vs floor 0.999, failed TVD checks, record coherence 0 vs floor 0.995. |

The profiler's `dtparse._midnight` uses the current date for date-free time strings; the
weekday histogram is a compared data value, not a runtime timestamp. The public profiling
call has no capture-clock context. Forcing the capture date would alter candidate values;
excluding the histogram would bypass a value comparison. A Tuesday-only green result is
calendar coincidence and does not resolve portability. No such workaround is implemented.

Owner commit `904d5d2` intentionally replaced sector and procedure reference contents.
The domains documentation confirms same row positions and weights but changed dataset ids.
The domain verifier correctly uses the baseline vocabulary and reference records; changing
the pool, substituting synthetic scalar values or mapping codes would change what is
compared. Restoring removed values contradicts the approved data replacement. Renaming a
reference source cannot restore equality of these data values. No compliant product or
harness fix preserving both sets of values was identified.

Precise remaining decision: retain these value failures under the current contract, or
separately define a comparison context/policy for date-free clock-derived values and
intentionally replaced reference-data values. Recommendation: keep these acceptance cases
parked with all comparisons intact until that additional decision; do not interpret the
error-category authorization as permission to change them. No expanded policy is implemented.

The PR uses `Refs #20`. Issue 20 stays open: closure requires a later PR only after a Nightly
dispatched on main is green on profile, stream, generate-1/2/3, parity-publish and sqlserver-e2e,
with that run linked. No Nightly dispatch or merge is performed by this lane, and no full
Nightly/live success is claimed.

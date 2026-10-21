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

## Original value failures: superseded by the proven paths below

The original four value-producing workloads cannot use the recorded-error category. Their
historical values and authenticated digests are in `public-b-2026-10-18/FRZ-01-evidence.md`
on the lead scratch branch. The initial blanket parking interpretation was too narrow:
approved category-name aliases can include code-shaped names, and original capture-job
provenance independently establishes the execution calendar date. The implemented paths
below retain all numeric values and comparison thresholds; they do not infer mappings from
frequencies or choose a date from a histogram. The historical parked status is superseded
by targeted acceptance proof, while hosted CI and post-merge Nightly remain outstanding.

## Required main refresh

Merged main `53d3d0c1a740fcca0665c216c3ebdabaaac95e49` after PR 40. Only the additive
Unreleased changelog entry conflicted; both entries are retained. The certifi lock and
prepared-browser fixes merge without code conflicts. This refresh includes the read-only
clock/name clarification above. No local rerun was needed for a source conflict.

The PR uses `Refs #20`. Issue 20 stays open: closure requires a later PR only after a Nightly
dispatched on main is green on profile, stream, generate-1/2/3, parity-publish and sqlserver-e2e,
with that run linked. No Nightly dispatch or merge is performed by this lane, and no full
Nightly/live success is claimed.

## Approved generation name normalization: implementation and proof

Further independent review clarified the Owner's phrase: code-shaped category names can
participate in fixed name aliases when linked by approved reference-row lineage. Blanket
parking because those labels occur as scalar strings was too strong. The new explicit
registry pins complete source/target reference-row digests, unchanged sector/industry IDs
and procedure replacement ordinals. Source names appear only as SHA-256 digests. Each
association is validated against the same lineage entry, not just a bijection. There is
no frequency fitting, numeric/charge matching or generated-row-order matching.

Only field-specific names are canonicalized in memory after authenticating all five
original baseline seeds. Vocabulary pools and reference-coherence name components use
the same contract; numeric/coherence inputs, counts, weights, probabilities and thresholds
are unchanged. Tests reject unknown labels, wrong scope/provenance, collisions/permutations,
changed weights/numerics, skewed frequencies, mismatched descriptions and reassigned charges.

Initial new controls: 13 red against the base without the contract. The actual unchanged
verifier on authenticated cached generation outputs reproduced both original workload
failures and all 14 flags (exit 1 each). With the reviewed name contract, actual targeted
`healthcare:medium` passed 80/80 columns and `capital_markets:medium` passed 64/64 columns,
all original clauses a–f. The existing isolated 0.9.1 interpreter uses Arrow 26 rather than
the historical Arrow 25 environment; every generated baseline seed nevertheless matched
the original recorded digests before comparison. This is value-equivalence proof, not a
claim of frozen-environment timing equivalence. Reports went to `/dev/null`; no raw archive
or recording was created. The two time-only cases use the independently authenticated replay below.
No full Nightly or issue closure is claimed.

## Authenticated capture-date replay: implementation and proof

Original capture run `38332677511`, job `115085741837`, source commit
`e41a67eab08beed36804bf3a84db83fb7f2595f6`, recorded every workload in the step
`Record every release workload, equivalence before timing`, from
`2026-10-13T01:47:38Z` through `2026-10-13T02:17:06Z`. The original workflow used
Ubuntu with no timezone override. The complete capture stayed within the same UTC
calendar date; manifest session start `2026-10-13T01:47:50Z` equals local machine start
`2026-10-13 01:47:50`. The checked store was committed at
`909a941cd294cd25031a8dcaa221e6b49e949674`. This establishes the execution date
independently of the expected weekday histogram, correcting the earlier missing-context premise.

`capture_date_context.json` commits this reviewed provenance and the exact record/input SHA-256
values for only `edge/x_time_only.csv` and `edge/x_time_ampm.csv`. Their authenticated inputs
contain time-of-day strings, no relative `now` or `today` tokens. The helper rejects unknown
replay requests, wrong source/window/timezone/context, changed records or inputs, and date-boundary
ambiguity. It supplies the captured date only at the parser's existing implicit `_midnight`
input boundary before computation, preserving explicit date arguments. It neither changes
system time nor rewrites, rotates or excludes any computed value. Every exit restores the
original function; other workloads retain ordinary execution.

The verifier comparison call and timed worker use the same independently authenticated helper.
Context setup and teardown stay outside the measured profiling interval. The generic profiling
function used by the recorder remains unchanged, so this does not force a historical date into
an authorized future recording. The recordings and all original comparison thresholds remain
untouched. Initial new controls were 12 red; restoring the prior verifier and timed worker with
the new helper present reproduced five behavioral failures, including both fixtures under
Sunday and another calendar date and the timing worker. The new path passes those actual
recorded-value comparisons, rejects changed time distributions/inputs, and restores context
after exceptions. No historical reference timing is remeasured or substituted.

## Final targeted acceptance and checks

- Direct authenticated two-fixture verifier: both time-only workloads PASS, zero mismatches,
  exit 0. Their values remain comparable, distinct from the two recorded-error refusals.
- Final focused harness/profile/domain/CLI/store/Nightly controls: 138 passed in Rust mode
  (7.12 seconds) and 138 passed in Python mode (6.93 seconds).
- Ruff and formatting pass for all 12 changed Python files. Configured mypy passes for
  703 source files; targeted strict mypy additionally passes for the two new harness modules.
  Documentation wording reports zero hits and the user-facing check is clean.
- Recordings, manifest and input fixtures are byte-identical to main. No tolerance, seed,
  numeric value, reference weight or comparison clause was changed or skipped.

All four former value failures now have concrete targeted passing paths. The remaining
acceptance gate is hosted CI and a later Nightly dispatched on merged main, including all
five parity shards, parity-publish and sqlserver-e2e. Those runs have not been claimed green;
this lane performs no merge, Nightly dispatch or issue closure.

## Windows input-manifest path repair — 2026-10-21

[CI 39517332719](https://github.com/sqllocks/shape/actions/runs/39517332719/job/118952937393)
found six Windows 3.14 capture-date tests failing because the verifier serialized relative
input paths with native backslashes, while the authenticated recording uses POSIX keys.
The input bytes and digests were unchanged. A platform-independent regression control using
`PureWindowsPath` reproduced the failure: 1 failed, 1 passed before the repair. The verifier
now uses `as_posix()` for manifest keys, retaining the exact byte hashes and equality check.

Focused verifier, capture-date and timed-worker tests: 35 passed in each kernel mode
(3.41 seconds and 3.09 seconds). Ruff, formatting and user-facing checks pass. Hosted
Windows validation remains pending on the new head; the failed run is not passing evidence.
No recording, fixture, value, threshold or comparison rule changed.

# Owner decisions and remaining dependencies

## Current decisions — Owner2026-10-20

> Approve explicit, tested "not comparable" handling, ONLY where the recorded0.9.1 result is itself an error or "unsupported". List each such workload by name, with its reason, in a reviewed file committed to the repo. Report them separately. They never count as a pass. Every workload where0.9.1 produced values still compares with unchanged thresholds. No re-recording. Capture-clock fields may be excluded only where they are runtime timestamps. Reference-label substitutions are normalized by name only, never by value. Implement Wanted5 of #6 under the same category. Close #20 only through its PR, and only once a Nightly dispatched on main is green on all five parity shards, parity-publish and sqlserver-e2e; link that run.

The error-category registry in [PR42](https://github.com/sqllocks/shape/pull/42) names `profile:edge/x_pq_list.parquet` and `profile:edge/x_pq_nested.parquet`, both recorded TypeError with no result values. Both are separate NOT COMPARABLE results and never pass/timing credit. Targeted red/green and independent review are recorded in its lane. #6 is implemented in [PR43](https://github.com/sqllocks/shape/pull/43), independently approved, with Wanted5 using that category; it depends on PR42 landing and combined current-main checks.

## #20 generation names — IMPLEMENTED under existing approval

Earlier parking of both generation workloads was too broad. Independent reassessment confirms all14 known generation flags are categorical vocabulary/TVD or record-coherence checks on deliberately renamed reference labels. Code-shaped strings can be reference-category names; the approved fixed-name normalization need not alter any numeric value or observation.

PR42 implements tests-first fixed field/domain-scoped bijections for `generate:capital_markets:medium` and `generate:healthcare:medium`. Reviewer frz_review requires authenticated original/new reference provenance and exact stable-ID/procedure-ordinal lineage, never pairing by generated frequencies, numeric charge similarity or generated output order. Preserve every row, null, count, probability, numeric charge, seed and threshold; recompute the existing checks, including code-description/charge association and corruption negatives. No clinical/taxonomy semantic-equivalence claim. Both actual generation workloads now pass fullclausesa-f (healthcare80/80columns; capital64/64), with original five seed digests authenticated. Prior verifier reproduces14flags on identical outputs. No new Owner decision is needed just to implement permitted name aliases.

Root located `/workspace/shape-history/.venv/bin/python` (Shape0.9.1, Arrow26). Its three relevant reference IPC blobs equal recorded-release commitd09731e exactly; use authenticated identical pools only, without claiming the whole environment matches the frozen recording environment. Do not alter that history environment or re-record.

## #20 time-only context — IMPLEMENTED after provenance reconstruction

Earlier parking relied on incomplete capture context and is superseded. Git history for recorded/MANIFEST.json identifies original capture run38332677511 from head e41a67eab08beed36804bf3a84db83fb7f2595f6. GitHub job115085741837 records the entire full-capture step on2026-10-13,01:47:38Z–02:17:06Z. Authenticated manifest recorded_utc is01:47:50Z, while local machine.started is also01:47:50. Original workflow uses ubuntu-latest without timezone override; recorder workers inherit that environment without TZ/tzset manipulation. The complete same-day execution window establishes date context independently of the expected histogram.

Independent frz_review supports narrowly replaying that authenticated implicit parser date BEFORE computation for ONLY `profile:edge/x_time_only.csv` and `profile:edge/x_time_ampm.csv`. Preserve recorded histograms and every numeric comparison; no exclusions, rotations, Tuesday inference from expected output, re-recording or threshold changes. Tests-first implementation is complete in PR42, independently APPROVED exact0b95d31a7f70fcaa2c399b1ff121420b66eb0113. Require committed recording/input/source/run/job/step-window provenance, reject unknown/missing/mismatched context or ambiguous dates/timezones, and test host-weekday independence plus continued detection of altered data/time distributions. Scope only the implicit parsing date, not unrelated runtime timestamps. Timing workers must use the same replay context as verifiers if timings are claimed.

Both actual time-only fixtures now pass every original comparison, exit0/0mismatches. Final targeted138tests pass eachkernel, including hostSunday/2027 and corruption controls. Generation names and time-only context are implemented and independently approved under existing authorization; no additional Owner question is pending for these four workloads. #20 stays open until implementation, independent review, exact-head CI and the required dispatched main Nightly all pass. [Capture run](https://github.com/sqllocks/shape/actions/runs/38332677511); [capture job](https://github.com/sqllocks/shape/actions/runs/38332677511/job/115085741837); [original mismatch evidence](FRZ-01-evidence.md); [current issue status](https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664).

## #14 merged source dependency — PARKED

Owner reconfirms Oct9 decision: check `0 <= scale <= precision` BEFORE connecting and name the column. No new decimal decision is pending. Reuse W9-01 source catalog/sampling interface only after [PR35](https://github.com/sqllocks/shape/pull/35) merges into main; it remains open at latest23:22 read. Decision journal signed/pushed58966e0; no current implementation. Start fresh lane/W9-13-finish from THEN main, without importing the old journal branch's unmerged Lead A history. Lead A ownership was respected; no branch or issue was modified.

Options: wait for merged interface or authorize an explicit scope/interface change. Recommendation: wait and reuse main; do not design a parallel source model. [Issue clarification](https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080).

## Pending service validation after offline acceptance

Future Owner actions, not claimed passing tests: Kafka/Event Hubs account round trips (#9/#10), Azure Schema Registry Avro round trip (#10; scope already approved), Eventhouse account read-back (#12), a semantic fixture containing a measure/role with expected metadata and visible rows (#13), and nightly emulator services (#9/#10/#12/#17). No live success is claimed. dbt introduces no account-dependent live requirement. Details in each lane record.

Generated by Codex

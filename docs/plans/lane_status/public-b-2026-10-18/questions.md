# Owner decisions and remaining dependencies

## Current decisions — Owner2026-10-20

> Approve explicit, tested "not comparable" handling, ONLY where the recorded0.9.1 result is itself an error or "unsupported". List each such workload by name, with its reason, in a reviewed file committed to the repo. Report them separately. They never count as a pass. Every workload where0.9.1 produced values still compares with unchanged thresholds. No re-recording. Capture-clock fields may be excluded only where they are runtime timestamps. Reference-label substitutions are normalized by name only, never by value. Implement Wanted5 of #6 under the same category. Close #20 only through its PR, and only once a Nightly dispatched on main is green on all five parity shards, parity-publish and sqlserver-e2e; link that run.

The error-category registry in [PR42](https://github.com/sqllocks/shape/pull/42) names `profile:edge/x_pq_list.parquet` and `profile:edge/x_pq_nested.parquet`, both recorded TypeError with no result values. Both are separate NOT COMPARABLE results and never pass/timing credit. Targeted red/green and independent review are recorded in its lane. #6 is implemented in [PR43](https://github.com/sqllocks/shape/pull/43), independently approved, with Wanted5 using that category; it depends on PR42 landing and combined current-main checks.

## #20 generation names — ACTIVE under existing approval

Earlier parking of both generation workloads was too broad. Independent reassessment confirms all14 known generation flags are categorical vocabulary/TVD or record-coherence checks on deliberately renamed reference labels. Code-shaped strings can be reference-category names; the approved fixed-name normalization need not alter any numeric value or observation.

Author formats_recover is implementing tests-first fixed field/domain-scoped bijections for `generate:capital_markets:medium` and `generate:healthcare:medium`. Reviewer frz_review requires authenticated original/new reference provenance and exact stable-ID/procedure-ordinal lineage, never pairing by generated frequencies, numeric charge similarity or generated output order. Preserve every row, null, count, probability, numeric charge, seed and threshold; recompute the existing checks, including code-description/charge association and corruption negatives. No clinical/taxonomy semantic-equivalence claim. No implementation/acceptance green is claimed yet. No new Owner decision is needed just to implement permitted name aliases.

Root located `/workspace/shape-history/.venv/bin/python` (Shape0.9.1, Arrow26). Its three relevant reference IPC blobs equal recorded-release commitd09731e exactly; use authenticated identical pools only, without claiming the whole environment matches the frozen recording environment. Do not alter that history environment or re-record.

## #20 time-only context — PARKED remaining acceptance

`profile:edge/x_time_only.csv` and `profile:edge/x_time_ampm.csv` produced values and cannot use the error category. The mismatches are weekday-histogram values, not runtime timestamps: capture-Tuesday versus later Sunday. Both records authenticate `recorded_utc=2026-10-13T01:47:50Z`; recorder code copies one session-start timestamp to every record. It does not explicitly record each workload's execution date/timezone/clock policy. Faithful replay of established recording context would differ from rewriting compared values, but no such per-workload replay contract is established. Do not infer Tuesday from expected histograms, rotate them, exclude them, loosen comparisons or re-record.

Question: retain strict clock-derived value failures, or define the exact recorded execution context/policy for date-free values? Recommendation: keep these two workloads parked with recordings/thresholds/comparisons unchanged until the precise context is established or decided. A Tuesday-only match is a calendar coincidence, not portable acceptance. No qualifying main Nightly is claimed and #20 stays open. [Original evidence](FRZ-01-evidence.md); [current issue status](https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664).

## #14 merged source dependency — PARKED

Owner reconfirms Oct9 decision: check `0 <= scale <= precision` BEFORE connecting and name the column. No new decimal decision is pending. Reuse W9-01 source catalog/sampling interface only after [PR35](https://github.com/sqllocks/shape/pull/35) merges into main; it remains open at latest22:18 read. Decision journal signed/pushed58966e0; no current implementation. Start fresh lane/W9-13-finish from THEN main, without importing the old journal branch's unmerged Lead A history. Lead A branches/issues were not inspected or modified.

Options: wait for merged interface or authorize an explicit scope/interface change. Recommendation: wait and reuse main; do not design a parallel source model. [Issue clarification](https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080).

## Pending service validation after offline acceptance

Future Owner actions, not claimed passing tests: Kafka/Event Hubs account round trips (#9/#10), Azure Schema Registry Avro round trip (#10; scope already approved), Eventhouse account read-back (#12), a semantic fixture containing a measure/role with expected metadata and visible rows (#13), and nightly emulator services (#9/#10/#12/#17). No live success is claimed. dbt introduces no account-dependent live requirement. Details in each lane record.

Generated by Codex

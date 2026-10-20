# Owner decisions and remaining dependencies

## Current status — Owner directive2026-10-20

The comparison-category decision for #20/#6 is now APPROVED under the exact constraints below; #6 is ACTIVE; the approved #20 category is implemented in PR42, while four remaining value-based workloads are parked as described below. Their older questions below are historical. #14 remains dependency-blocked solely until PR35 merges into main; its decimal policy is resolved.

> Approve explicit, tested "not comparable" handling, ONLY where the recorded0.9.1 result is itself an error or "unsupported". List each such workload by name, with its reason, in a reviewed file committed to the repo. Report them separately. They never count as a pass. Every workload where0.9.1 produced values still compares with unchanged thresholds. No re-recording. Capture-clock fields may be excluded only where they are runtime timestamps. Reference-label substitutions are normalized by name only, never by value. Implement Wanted5 of #6 under the same category. Close #20 only through its PR, and only once a Nightly dispatched on main is green on all five parity shards, parity-publish and sqlserver-e2e; link that run.

#14: Owner reconfirms the2026-10-09 decision to check `0 <= scale <= precision` before connecting and name the column. Unpark after #2/W9-01 PR35 merges; reuse that main source interface. No separate decimal approval is pending.


## #20 FRZ-01: comparison contract for authenticated 0.9.1 recordings

Both requested artifacts and original job logs are now retrieved through the GitHub connector. The initial artifact-access problem is resolved and is no longer a blocker. See [exact evidence](FRZ-01-evidence.md) for artifact hashes, all six failing workloads, all 14 generation flags and recorded/observed values.

The four profile mismatches compare recorded nested-input TypeError failures against intentionally added nested support, or capture-Tuesday weekday statistics against Sunday for time-only values with no calendar date. Reconstructed input hashes match the authenticated recordings and the unchanged verifier reproduces all four. The two generation workloads compare approved synthetic reference labels against the older 0.9.1 labels; Owner commit904d5d2 deliberately replaced those labels and the domain documentation warns that fixtures change. No real product regression or fix preserving the current comparison rules was identified.

Decision: how should equivalence handle newly supported baseline inputs, missing capture-clock context, and intentionally substituted reference labels? Options: keep the present strict contract and its red result; authorize explicit, tested noncomparability handling for these cases while retaining recorded values and numeric thresholds; or separately authorize and justify a new baseline policy. Recommendation: define explicit noncomparability handling with tests, preserving all recordings and numeric tolerances. That changes a current pass rule and is prohibited in this run, so no implementation, re-recording, tolerance change, product PR, merge or Nightly dispatch is made. Issue20 remains open and parked.

Issue evidence comment: https://github.com/sqllocks/shape/issues/20#issuecomment-6252771902

## #6 W9-05: unsupported nested-baseline clause

Wanted5 explicitly asks to report nested baseline inputs as unsupported rather than differences. The existing recorded-error equivalence rule instead fails when current Shape succeeds on an input that the baseline rejected. The merged FIX-NESTED dependency intentionally supports those inputs. Current comparison reproductions and exact matching input hashes are in lane/W9-05 (updated journal3de70e3) and FRZ-01-evidence.md. Ten tests were recorded red against main, but no child-feature implementation is shipped.

Options: defer Wanted5 and retain strict exception matching; or authorize explicit unsupported-baseline classification together with the #20 comparison-contract decision. Recommendation: explicit authorized classification, preserving recordings and numeric thresholds. Restoring obsolete nested TypeError failures would regress merged functionality. The lane's Owner action is explicit; no live execution is claimed.

Issue comment: https://github.com/sqllocks/shape/issues/6#issuecomment-6299574643

## #14 W9-13: merged source dependency

The issue explicitly depends on W9-01 and requires reusing its source catalog/sampling model. Latest verified main 35addfc4 still has only database-plugin sink modules. No Lead A branch or issue was inspected or modified. The preserved own lane/W9-13 journal1249af9 contains no implementation.

Options: wait for the dependency to merge and reuse its interface; or authorize an explicit scope/interface change. Recommendation: wait for the merged interface. This run does not design a parallel source model.

Issue comment: https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080

## #14 W9-13: decimal boundary clarification resolved

Existing issuecomment6072817299 (2026-10-09) already requires0<=scale<=precision, refusing invalid decimals before connection while naming the column/rule. It resolves the prior journal's question. No further Ownerdecision is needed; #14 remains parked solely for the missing W9-01 source interface.

## Pending service validation after offline acceptance

These are future Owner actions, not unclaimed passing tests or blockers to the specified offline acceptance: Kafka/Event Hubs account round trips (#9/#10), Azure Schema Registry Avro round trip (#10), Eventhouse account read-back (#12), a semantic model containing a measure and role (#13), and nightly emulator services where images can be pulled (#9/#10/#12/#17). No live test has been run or claimed. dbt introduces no account-dependent live requirement.

Generated by Codex

## #20 remaining value acceptance — after approved error-category implementation

[PR42](https://github.com/sqllocks/shape/pull/42), exact `8f5dfb341d15068f6b2e646b1d3915ea41f9cfe4`, is ready and independently APPROVED. It implements the two authenticated recorded-error workloads and rejects false pass credit, identity/reason/timing forgeries and unknown or value-recording cases. It uses Refs#20 and does not close the issue.

The four remaining workloads produced values and cannot use the approved error category: `profile:edge/x_time_only.csv`, `profile:edge/x_time_ampm.csv`, `generate:capital_markets:medium`, `generate:healthcare:medium`. The weekday histograms are compared data values, not runtime timestamps; the other differences concern intentionally replaced synthetic data values, not only reference names. No compliant product/harness fix preserving both sets of values was identified. A Tuesday-only match is explicitly tested as calendar coincidence.

Options: retain these value failures under the current strict contract, or separately define a comparison context/policy for date-free clock-derived values and intentionally replaced reference-data values. Recommendation: park the remaining acceptance with comparisons/recordings/thresholds intact until that precise additional decision. No such extra permission is inferred from the recorded-error authorization.

[Updated issue comment](https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664). The exact registry/reasons and red→green output are committed in the PR lane record. Nightly has not been dispatched; all-five-shard/parity-publish/sqlserver-e2e green is not claimed.

### Remaining #20 context/name analysis (read-only, no extra authorization assumed)

Both time-only recordings authenticate `recorded_utc=2026-10-13T01:47:50Z`; the recorder copies a single capture-session start into every record. This does not establish each workload's execution date/timezone/clock policy. Faithfully replaying a genuinely recorded execution environment would differ from rewriting compared values, but no such replay is established from current metadata; do not infer Tuesday from the expected histogram. A possible future policy must explicitly define date-free execution context before any change.

The replacement reference tables retain stable numeric sector/industry IDs, rows and weights. Explicit display-name aliases tied to those unchanged IDs could be a partial name-only normalization, with frequencies/coherence recomputed unchanged. Source/column aliases already match, and code/data values still differ; no whole generation workload fix follows from display-name aliases. Do not map procedure/sector code values or fit mappings by frequencies/rank/charges. The four remaining workloads therefore remain parked. At PR42's next mandatory main refresh, clarify these facts in its journal; no extra code or comparison permission is inferred.

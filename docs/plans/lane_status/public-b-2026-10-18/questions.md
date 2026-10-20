# Owner decisions and remaining dependencies

## Current status — Owner directive2026-10-20

The comparison-category decision for #20/#6 is now APPROVED under the exact constraints below; both lanes are ACTIVE. Their older questions below are historical. #14 remains dependency-blocked solely until PR35 merges into main; its decimal policy is resolved.

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

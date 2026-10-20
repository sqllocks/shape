# Public A state

## Governing instructions

Read the attached prompt at `/workspace/attachments/9302a188-787d-4428-9403-3510325ce6a1/4-NEW-WINDOW-shape-public-A.md` after a restart. The Owner’s October 20 throughput directive supersedes its local full-suite and extensive-proof requirements: CI is the full-suite gate; use targeted acceptance/module tests and local lint, format, mypy and wording checks. Run only a §13 portion missing from public CI locally. Do not restart full local make check or full pytest. PRs go ready after targeted tests pass. Refresh by merging main and pushing; rerun local targeted tests only for code conflicts. Merge reviewed, current, green PRs promptly.

Lane evidence from now on: PR link, green CI links on the merged head, acceptance red/green output. Per-command ledger and raw-log archival are retired; historical files remain. Parking, naming restrictions, no skips/xfails/deleted tests, unchanged assertions/gates/tolerances and protected-branch rules remain in force. Keep lanes parallel and skip another session’s work.

Decision-log row: `docs/plans/COMPLETION_PLAN.md` §2.3, refreshed head `5ae9734`, ready lead PR https://github.com/sqllocks/shape/pull/36, independently APPROVED; CI pending. Premium process wording and planning PRs belong to other sessions; Public A does not touch them.

## Issues — 0 of 7 merged

| Issue | Branch / head | Current stage / next action |
|---|---|---|
| #2 W9-01 | lane/W9-01, f16c2c6; ready PR https://github.com/sqllocks/shape/pull/35 | Independent implementation/docs review APPROVED. Own source/security/privacy/SQLServer checks passed, including 727 tests in each kernel. Both stale plugin-list transcripts were corrected from actual 182-entry output and both affected nodes passed. Review approval extends to 8bb2375. Main35addfc merged, docs conflicts resolved preserving both additions; reviewer APPROVED refreshed head. Merge once every triggered exact-head CI job is green and main is current. No local full gate required. |
| #3 W9-02 | lane/W9-02, 9dc9085; ready PR https://github.com/sqllocks/shape/pull/37 | Actual W9-01 dependency stacked. 264 targeted cloud/affected-sink tests and 68 cloud Python-kernel cases pass; strict mypy, Ruff, formatting, wording pass. Actual docs plugin listings184. Independent reviewer APPROVED exact head. CI queued. Merge #2 first, then refresh against main and merge when all exact-head checks green. |
| #4 W9-03 | lane/W9-03, 9869529 | PARKED: Owner approval needed for four extra unbounded string-primary-key refusals. 233 focused cases passed; authorized corpus changes preserved. No PR. |
| #5 W9-04 | lane/W9-04, d9591d0 | PARKED: DuckDB cannot add FK after both tables exist. Recommend documented parent-first inline FK exception. Partial derivation/preparation checkpoint has 77 passing targeted cases. No PR. |
| #7 W9-06 | lane/W9-06, 3ad0940 | Native SQL/cloud/DuckDB writer checkpoint pushed; remaining recursive preflight and matching-source read-back integration in progress. Stack actual #2/#3 branches for source integration now; main merge order remains #2 then #3 then #7. Delta native maps implemented; strict read-back nanosecond precision fix underway. Targeted DB plugin selection passed 554 tests, including 81 new cases. Independent review active. |
| #8 W9-07 | lane/W9-07, 264efcd | PARKED on earlier #5 safe constraint derivation dependency. Independent feature module 91 cases pass; reviewer verified 17 regressions. Two AST integration cases remain red pending #5. No PR. |
| #16 W9-15 | lane/W9-15, 5a215ac | Readiness complete with no Owner blocker. Sink/types/fake checkpoint has 38 targeted tests green; source/helpers/tests proceed in parallel; actual source integration may stack W9-01 now; merge follows #2. Shared-file ownership is coordinated with #7. Oracle plan and test matrix: /workspace/public-a-evidence/W9-15-readiness.md. |

## Parked questions

Exact options and recommendations: `questions.md`.

- #4: https://github.com/sqllocks/shape/issues/4#issuecomment-6298211776
- #5: https://github.com/sqllocks/shape/issues/5#issuecomment-6298361966
- #8 dependency: https://github.com/sqllocks/shape/issues/8#issuecomment-6298362470

## Environment

Git credential gate passed; main was35addfc at last inspection. All commits are signed and pushed only through origin. Prior unsigned lane histories were backed up before exact-lease signed-squash repair. Scratch branch remains `lead/public-a-2026-10-18`.

Pinned uv 0.8.17 is at `/tmp/public-a-uv/bin/uv` (restore externally after an environment reset). Reaper persists at `$HOME/bin/reap.py`. Existing checks have stayed far below 1,000 zombies. Bootstrap completed. No full local suite is active or required under the new directive.

Documentation prerequisites were prepared before the directive: default dbt packages at `/tmp/docs-dbt-packages` and a persisted copy under `/workspace/public-a-evidence/docs-dbt-packages`; Chromium installed and launched; DuckDB 1.5.6 Delta extension LOAD passed; Docker image `shape-docs-examples` built from W9-01 source. These are setup, not merge evidence. W9-01 installed a task-local Rust 1.99.1 with fmt/clippy. No more broad proof archiving or local full-suite wrapper work.

Public CI already runs both core kernel modes in its min-versions job, plus native/heavy/kernel/drift/docs/Fabric/pure-wheel jobs. Changed DB plugin modules were also checked locally in both modes. All triggered exact-head checks remain required.

## Next

Merge reviewed PRs #35, #36 and dependent #37 promptly as each reaches green CI and current main. Merge the resulting main into open implementation lanes once; do not repeat local tests for a clean refresh. Stack actual #2 branch into #3/#16 for real integration now; open ready dependent PRs after targeted checks. Earlier waiting-for-main integration interpretation was unnecessarily conservative; merge order remains unchanged. Continue #7 in parallel. Preserve parked checkpoints until genuine Owner decisions arrive. Final report is required when all seven issues are merged or parked, or another hard stop occurs.

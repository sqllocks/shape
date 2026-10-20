# Public A state

## Governing instructions

Read the attached prompt at `/workspace/attachments/9302a188-787d-4428-9403-3510325ce6a1/4-NEW-WINDOW-shape-public-A.md` after a restart. The Owner’s October 20 throughput directive supersedes its local full-suite and extensive-proof requirements: CI is the full-suite gate; use targeted acceptance/module tests and local lint, format, mypy and wording checks. Run only a §13 portion missing from public CI locally. Do not restart full local make check or full pytest. PRs go ready after targeted tests pass. Refresh by merging main and pushing; rerun local targeted tests only for code conflicts. Merge reviewed, current, green PRs promptly.

Lane evidence from now on: PR link, green CI links on the merged head, acceptance red/green output. Per-command ledger and raw-log archival are retired; historical files remain. Parking, naming restrictions, no skips/xfails/deleted tests, unchanged assertions/gates/tolerances and protected-branch rules remain in force. Keep lanes parallel and skip another session’s work.

Decision-log row: `docs/plans/COMPLETION_PLAN.md` §2.3, commit `ec27f88`, ready lead PR https://github.com/sqllocks/shape/pull/36. Premium process wording and planning PRs belong to other sessions; Public A does not touch them.

## Issues — 0 of 7 merged

| Issue | Branch / head | Current stage / next action |
|---|---|---|
| #2 W9-01 | lane/W9-01, f344810; ready PR https://github.com/sqllocks/shape/pull/35 | Independent implementation/docs review APPROVED. Own source/security/privacy/SQLServer checks passed, including 727 tests in each kernel. CI found two stale plugin-list transcripts, being updated from actual 182-entry output. Push docs fix, confirm review, merge once every triggered exact-head CI job is green and main is current. No local full gate required. |
| #3 W9-02 | lane/W9-02, 15c8b67 | Source checkpoint and 65 targeted cases complete provisionally against W9-01 model. Resume real integration after #2 merge, then ready PR/CI/review. Same agent currently implements #7. |
| #4 W9-03 | lane/W9-03, 9869529 | PARKED: Owner approval needed for four extra unbounded string-primary-key refusals. 233 focused cases passed; authorized corpus changes preserved. No PR. |
| #5 W9-04 | lane/W9-04, d9591d0 | PARKED: DuckDB cannot add FK after both tables exist. Recommend documented parent-first inline FK exception. Partial derivation/preparation checkpoint has 77 passing targeted cases. No PR. |
| #7 W9-06 | lane/W9-06, 526e4f2 | Native SQL/cloud/DuckDB writer checkpoint pushed; remaining recursive preflight and matching-source read-back integration in progress. Source integration waits #2/#3 merge; Delta map coverage/options still to finish. Targeted DB plugin selection passed 554 tests, including 81 new cases. Independent review active. |
| #8 W9-07 | lane/W9-07, 264efcd | PARKED on earlier #5 safe constraint derivation dependency. Independent feature module 91 cases pass; reviewer verified 17 regressions. Two AST integration cases remain red pending #5. No PR. |
| #16 W9-15 | not started; prior lane/W9-15 contains journal only | Read-only readiness complete with no Owner blocker. Implementation MUST wait #2 merge. Oracle plan and test matrix: /workspace/public-a-evidence/W9-15-readiness.md. |

## Parked questions

Exact options and recommendations: `questions.md`.

- #4: https://github.com/sqllocks/shape/issues/4#issuecomment-6298211776
- #5: https://github.com/sqllocks/shape/issues/5#issuecomment-6298361966
- #8 dependency: https://github.com/sqllocks/shape/issues/8#issuecomment-6298362470

## Environment

Git credential gate passed; main was d0e64d5 at last inspection. All commits are signed and pushed only through origin. Prior unsigned lane histories were backed up before exact-lease signed-squash repair. Scratch branch remains `lead/public-a-2026-10-18`.

Pinned uv 0.8.17 is at `/tmp/public-a-uv/bin/uv` (restore externally after an environment reset). Reaper persists at `$HOME/bin/reap.py`. Existing checks have stayed far below 1,000 zombies. Bootstrap completed. No full local suite is active or required under the new directive.

Documentation prerequisites were prepared before the directive: default dbt packages at `/tmp/docs-dbt-packages` and a persisted copy under `/workspace/public-a-evidence/docs-dbt-packages`; Chromium installed and launched; DuckDB 1.5.6 Delta extension LOAD passed; Docker image `shape-docs-examples` built from W9-01 source. These are setup, not merge evidence. W9-01 installed a task-local Rust 1.99.1 with fmt/clippy. No more broad proof archiving or local full-suite wrapper work.

Public CI already runs both core kernel modes in its min-versions job, plus native/heavy/kernel/drift/docs/Fabric/pure-wheel jobs. Changed DB plugin modules were also checked locally in both modes. All triggered exact-head checks remain required.

## Next

Finish #2 transcript fix and merge on green CI. Merge the resulting main into open implementation lanes once; do not repeat local tests for a clean refresh. Resume #3 and start #16 immediately after #2 merge. Continue #7 in parallel. Preserve parked checkpoints until genuine Owner decisions arrive. Final report is required when all seven issues are merged or parked, or another hard stop occurs.

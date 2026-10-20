# Public A state

## Governing instructions

Read the attached prompt at `/workspace/attachments/9302a188-787d-4428-9403-3510325ce6a1/4-NEW-WINDOW-shape-public-A.md` after a restart. The Owner’s October 20 throughput directive supersedes its local full-suite and extensive-proof requirements: CI is the full-suite gate; use targeted acceptance/module tests and local lint, format, mypy and wording checks. Run only a §13 portion missing from public CI locally. Do not restart full local make check or full pytest. PRs go ready after targeted tests pass. Refresh by merging main and pushing; rerun local targeted tests only for code conflicts. Merge reviewed, current, green PRs promptly.

Lane evidence from now on: PR link, green CI links on the merged head, acceptance red/green output. Per-command ledger and raw-log archival are retired; historical files remain. Parking, naming restrictions, no skips/xfails/deleted tests, unchanged assertions/gates/tolerances and protected-branch rules remain in force. Keep lanes parallel and skip another session’s work.

Decision-log row: `docs/plans/COMPLETION_PLAN.md` §2.3, refreshed head `5ae9734`, ready lead PR https://github.com/sqllocks/shape/pull/36, independently APPROVED; CI pending. Premium process wording and planning PRs belong to other sessions; Public A does not touch them.

## Issues — 0 of 7 merged

| Issue | Branch / head | Current stage / next action |
|---|---|---|
| #2 W9-01 | lane/W9-01, f16c2c6; ready PR https://github.com/sqllocks/shape/pull/35 | Independent implementation/docs review APPROVED. Own source/security/privacy/SQLServer checks passed, including 727 tests in each kernel. Both stale plugin-list transcripts were corrected from actual 182-entry output and both affected nodes passed. Review approval extends to 8bb2375. Main35addfc merged, docs conflicts resolved preserving both additions; reviewer APPROVED refreshed head. Merge once every triggered exact-head CI job is green and main is current. No local full gate required. |
| #3 W9-02 | lane/W9-02, e8f9e5b; ready PR https://github.com/sqllocks/shape/pull/37 | Actual W9-01 dependency stacked. Initial264 targeted +68 Python cloud green. Narrow safe-bridge fix showed8 sensitive-artifact regressions red, then76 cloud Python +1278 cloud/bridge default cases green. Strict mypy/Ruff/format/wording pass. Independent reviewer APPROVED exact e8f9e5b. CI queued; #2 must merge first. |
| #4 W9-03 | lane/W9-03, 9869529 | PARKED: Owner approval needed for four extra unbounded string-primary-key refusals. 233 focused cases passed; authorized corpus changes preserved. No PR. |
| #5 W9-04 | lane/W9-04, c92f7a0 | PARKED: DuckDB cannot add FK after both tables exist; Owner question unchanged. Actual approved #7 graph stacked. Original77 helper/preparation tests plus22 new compiler tests green. Independent review found finite Decimal/MySQL backslash edges; genuine-red regressions and fixes in working tree. Writer hooks/rerun safety/Fabric/SQLServer/CLI continue within unblocked scope. No PR. |
| #7 W9-06 | lane/W9-06, c9f0cca; ready PR https://github.com/sqllocks/shape/pull/38 | Actual #2/#3 branches stacked.257 acceptance/module +380 existing affected sink/type/staging cases pass; Ruff/format,8-module mypy,registry/wording/diffcheck green. Matching registered Sources exercised, duplicate new DuckDB Source removed. Final Delta review fix documents UTC/microseconds, fixes nonUTC conversion and passes18 targeted Delta cases; independent reviewer APPROVED exact c9f0cca. Missing Python-kernel plugin acceptance filled:176 targeted cases pass at that same head. Merge order #2 then #3 then #7; exact-head CI required. |
| #8 W9-07 | lane/W9-07, 1fec4e8 | PARKED on #5 Owner decision/main merge. Actual #5 helper stacked; both former AST integration failures resolved.140 targeted default feature/AST/helper tests and103 Python feature/AST tests pass; strict typing/format/wording pass. Now integrating actual approved #7 graph, preserving opt-ins/native nested/default protocol; no PR. |
| #16 W9-15 | lane/W9-15, ad3d264; ready PR https://github.com/sqllocks/shape/pull/39 | Actual #2 stacked.117 targeted sink/source/conformance/bridge cases pass; strict mypy5modules/Ruff/wording green. Real Oracle Free23 emulator healthy;3 acceptance roundtrips pass (not live). Final partial-commit error-count defect fixed:2 red→green regressions,46 Oracle sink module tests green; all3 actual Oracle emulator cases passed again. Independent reviewer APPROVED exact ad3d264. Missing Python-kernel Oracle acceptance filled:96 targeted cases pass at that same head. Merge #2 first; no extra #3 dependency. |

## Parked questions

Exact options and recommendations: `questions.md`.

- #4: https://github.com/sqllocks/shape/issues/4#issuecomment-6298211776
- #5: https://github.com/sqllocks/shape/issues/5#issuecomment-6298361966
- #8 dependency: https://github.com/sqllocks/shape/issues/8#issuecomment-6298362470

## Environment

Git credential gate passed; main was35addfc at last inspection. All commits are signed and pushed only through origin. Prior unsigned lane histories were backed up before exact-lease signed-squash repair. Scratch branch remains `lead/public-a-2026-10-18`.

Pinned uv 0.8.17 is at `/tmp/public-a-uv/bin/uv` (restore externally after an environment reset). Reaper persists at `$HOME/bin/reap.py`. Existing checks have stayed far below 1,000 zombies. Bootstrap completed. No full local suite is active or required under the new directive.

Documentation prerequisites were prepared before the directive: default dbt packages at `/tmp/docs-dbt-packages` and a persisted copy under `/workspace/public-a-evidence/docs-dbt-packages`; Chromium installed and launched; DuckDB 1.5.6 Delta extension LOAD passed; Docker image `shape-docs-examples` was built from W9-01 source, then removed with its unused build cache under the throughput directive to recover emulator space.15GB free after cleanup. These are setup, not merge evidence. W9-01 installed a task-local Rust 1.99.1 with fmt/clippy. No more broad proof archiving or local full-suite wrapper work.

Public CI already runs both core kernel modes in its min-versions job, plus native/heavy/kernel/drift/docs/Fabric/pure-wheel jobs. Changed DB plugin modules were also checked locally in both modes. All triggered exact-head checks remain required.

## Shared CI blocker

Security audits on the current baseline found certifi2026.7.22 GHSA-6q48-6c7w-9gw2; fixed version is2026.10.19. Lead B owns the existing certificate/browser fix PR40. Do not edit, refresh or merge its branch. All five owned ready PR heads still pin the vulnerable baseline; their unfinished workflows were cancelled deliberately to free capacity for the shared fix. No gate was bypassed: after the fix reaches main, merge that main once into each owned ready branch and push, then require every triggered exact-head check green. Independent implementation approvals remain valid subject to reviewing any refresh conflicts.

Parked #8 now stacks actual #5 helper, with its former red AST integration fixed; #5 has a fresh agent finishing unblocked writer/CLI portions on its stacked #7 graph, preserving DuckDB Owner blocker. Neither is unparked or approved for a PR/main merge. Shared file ownership is coordinated; no helper copies.

A transient scratch push authentication error recovered on a dry-run and actual retry; latest git read/write credential checks passed. No config or credential changes.

## Next

Await the other-owned shared dependency fix on main, then refresh all owned ready PRs once and let full CI rerun. Merge reviewed PRs #35/#36 and dependent #37/#38/#39 promptly as each reaches green CI and current main. Merge the resulting main into open implementation lanes once; do not repeat local tests for a clean refresh. Stack actual #2 branch into #3/#16 for real integration now; open ready dependent PRs after targeted checks. Earlier waiting-for-main integration interpretation was unnecessarily conservative; merge order remains unchanged. Continue #7 in parallel. Preserve parked checkpoints until genuine Owner decisions arrive. Final report is required when all seven issues are merged or parked, or another hard stop occurs.

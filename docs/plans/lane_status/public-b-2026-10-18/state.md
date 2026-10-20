# Public B state — 2026-10-20 throughput directive

Read the attached prompt and this state after restart. The Owner throughput directive in chat supersedes earlier local-full/evidence procedures. Keep working until all assigned issues are merged/parked or a specified hard stop occurs.

## Effective rules

CI is the full-suite gate on each exact head, including both kernel implementations. Locally use acceptance/changed-module tests, Ruff/format/mypy/wording and only genuinely uncovered functional acceptance. The native joint-heavy test absent from CI passed in all six lanes. Independent review confirmed that extra unchanged Python performance benchmarks are redundant Cartesian reruns under the new policy: they were interrupted, NOT passed. Do not restart the serial full worker. Do not archive raw logs, gate-state JSON, watcher heartbeats or receipt indexes. Existing historical artifacts remain historical. Keep only PR, green CI links and acceptance red→green output as lane evidence.

All six PRs were marked ready. PR32 merged via API as a merge commit; every remaining lane refreshed onto that main in parallel and signed-off/pushed immediately. Only formats had code conflicts, resolved using the previously reviewed candidate and targeted tests345passed each kernel. Other conflicts were additive changelog/requirements entries, both preserved; no local reruns for clean code merges. All refreshed heads independently APPROVED. Never push main directly or alter gates/tolerances. No release/version/tag/settings/secrets changes.

## Current lanes

Current main:35addfc4c310b85a62cbe7f315e1d50fb8e426f8. Main CI is running/queued; watch for genuine failures under the prompt hard-stop rule.

| Issue | Branch / exact head | PR | Stage |
|---|---|---|---|
| #9 W9-08 | lane/W9-08-finish / 91ef6200266bf962246d1553356b26a442be2303 | https://github.com/sqllocks/shape/pull/32 | MERGED35addfc4; all triggered source-head workflows green |
| #10 W9-09 | lane/W9-09 / 0b313bba0a74332fa7c5c955fceca4bdb92ae0d4 | https://github.com/sqllocks/shape/pull/34 | ready, refreshed, APPROVED; fresh CI pending |
| #11 W9-10 | lane/W9-10-finish / e1ae5787c395714f558252ef0190068077d8d4ad | https://github.com/sqllocks/shape/pull/29 | ready, refreshed, APPROVED; fresh CI pending |
| #12 W9-11 | lane/W9-11-finish / bc98623f582c121cad0bcf56a83a8829fd826437 | https://github.com/sqllocks/shape/pull/30 | ready, refreshed, APPROVED; fresh CI pending |
| #13 W9-12 | lane/W9-12-finish / 170ce0dd549eaca5898d368b7fc3715ccc1a5d53 | https://github.com/sqllocks/shape/pull/33 | ready, refreshed, APPROVED; fresh CI pending |
| #17 W9-16 | lane/W9-16-finish / 8c9fa927dc31540f19755827641b0dbc9a8a3668 | https://github.com/sqllocks/shape/pull/31 | ready, refreshed, APPROVED; fresh CI pending |

PR34 old7a head had an unchanged macOS cancellation-test timing failure; new0b head CI supersedes it. Do not weaken/edit the unowned test. Iceberg Windows local-file URI fix remains in8c,128targeted pass each mode+16current/minimumSDK tests; actual Windows freshCI pending. The old font failure was in unchanged branded-site tests; no assertion edited.

## Decisions and Owner actions

Throughput decision-log row is committed on scratch cf68ff1 and included in dbt PR29 e1ae578 for main. Premium process05/08 edits are owned by Premium Build A; do not touch that repo or another session's PRs. Semantic/dbt explicit Owner-actions headings were added during refresh. Pending service validation is already listed in each lane record; no live success claimed. Semantic fixture/measure/role expected-results decision remains Owner work.

Parked #20: comparison-contract decision, authenticated artifacts retrieved and exact failures/root cause in FRZ-01-evidence.md. Do not re-record, restore obsolete errors/labels, or change pass rules. Latest issuecomment6252771902.
Parked #6: Wanted5 unsupported-baseline classification conflicts with frozen comparison policy; Owner decision required. Existing implementation/test-first branch3de70e3. Updated clarification: issuecomment6299574643; artifact access is resolved.
Parked #14: waiting only for W9-01 source interface on main; do not inspect Lead A branches/issues. Decimal0<=scale<=precision refusal was ALREADY approved in issuecomment6072817299, no further decision needed. Existing journal branch1249af9. Updated clarification: issuecomment6299575080; no new decimal decision is pending.

Exact questions/options/recommendations remain in questions.md. All three issues remain open; no product PR/merge for them.

## Next actions

Poll fresh exact-head CI without persisting JSON/heartbeats. Merge green reviewed current PRs promptly via merge API; refresh other own open lanes from new main, preserve additive entries and let CI rerun. Local tests only for code conflicts. Keep lanes parallel; do not run local fulls again. Re-check main/dependency interface when it moves, and unpark BigQuery only if its needed interface is on main. Do not end just to wait. Final report.md has not been written; only write it at completion/hard stop.

Active/idle recover agents: metadata_recover (streaming reviewer), formats_recover, dbt_recover, eventhouse_recover (Iceberg/semantic reviewer), iceberg_recover. Use followup_task to resume idle agents. All local test commands use /home/agent/bin/reap.py. Last observed zombies22 (<1000); credentials/git/gh operations succeed. Worktrees: /workspace/shape-w9-08/-09/-10/-11/-12/-16. All heavy/full queues are intentionally stopped; old status.json is historical and must not cause a restart.

## Shared CI repair — current priority

[PR #40](https://github.com/sqllocks/shape/pull/40), branch `fix/public-b-certifi-2026-10-20`, head `9a55862198b6030fc64eb30cbed25df98eb5a7c8`, is ready and independently APPROVED. Worktree `/workspace/shape-ci-certifi`; parent main35addfc4. Only certifi lock metadata (2026.7.22 → 2026.10.19), prepared Playwright browser selection, and a changelog entry changed. No dependency range, project version, workflow, test assertion, gate or tolerance changed. Targeted audit old exit1/new exit0, branding test1pass30.95s, lock/static/type/wording checks clean. Full suites delegated to CI. This supporting PR does not add a tenth assigned issue.

The newly surfaced certifi advisory GHSA-6q48-6c7w-9gw2 blocks active PR audit/build/security jobs. Earlier main audits were green before the advisory surfaced; do not ignore the new finding. Semantic docs-test also failed the unchanged Host Grotesk font check; the repair makes it use CI's already-installed pinned browser rather than arbitrary system Chrome. The precise prior font root cause remains unproven.

To free runner slots, root intentionally canceled remaining CI runs on current feature heads PR29, PR30 and PR33: these unchanged-code refreshes must absorb repair main and rerun CI anyway. Their canceled checks are NOT passing. Formats PR34 and Iceberg PR31 retain CI because they validate new combined streaming/Windows code. Main CI also remains running; completed jobs have no observed failures. PR30's queued one-job REPORT_CARD teardown retry was canceled as part of this deliberate hold; no passing retry claimed. Do not retry old feature jobs before the repair lands.

Next: merge PR40 promptly when all exact-head CI is green and it contains current main, then merge new main into all five feature lanes and push. Run local targeted tests only for any actual code conflicts. Keep tests/assertions/gates unchanged. Continue main CI monitoring and park #6/#14/#20 as recorded.

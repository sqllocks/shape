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
| #6 W9-05 | lane/W9-05 / 39760e6927b36dc32f7d0bd7f4977a36719f70ac | https://github.com/sqllocks/shape/pull/43 | ready, independently APPROVED; depends on PR42/main refresh/combined exact-head CI |
| #20 FRZ-01 | lane/FRZ-01-finish / 8f5dfb341d15068f6b2e646b1d3915ea41f9cfe4 | https://github.com/sqllocks/shape/pull/42 | error category ready and APPROVED; four remaining value workloads parked; no closure |
| #14 W9-13 | lane/W9-13 / 58966e0 | none | journal only; dependency-blocked until PR35/main source interface |

PR34 old7a head had an unchanged macOS cancellation-test timing failure; new0b head CI supersedes it. Do not weaken/edit the unowned test. Iceberg Windows local-file URI fix remains in8c,128targeted pass each mode+16current/minimumSDK tests; actual Windows freshCI pending. The old font failure was in unchanged branded-site tests; no assertion edited.

## Decisions and Owner actions

Throughput decision-log row is committed on scratch cf68ff1 and included in dbt PR29 e1ae578 for main. Premium process05/08 edits are owned by Premium Build A; do not touch that repo or another session's PRs. Semantic/dbt explicit Owner-actions headings were added during refresh. Pending service validation is already listed in each lane record; no live success claimed. Semantic fixture/measure/role expected-results decision remains Owner work.

PARTIAL READY / REMAINING ACCEPTANCE PARKED #20: approved error category implemented in PR42 at8f5dfb341d15068f6b2e646b1d3915ea41f9cfe4, independently APPROVED. Four value-recording workloads remain blocked under unchanged value rules; exact question/options/recommendation in questions.md and issuecomment6301325664. Do not count #20 delivered or close it. Value comparisons/recordings/thresholds unchanged.
READY #6: Owner2026-10-20 approval implemented in PR43, independently APPROVED exact39760e6927b36dc32f7d0bd7f4977a36719f70ac. Depends on shared category PR42 landing and a combined main refresh; no harness code copied. eventhouse_recover owns refreshes; frz_review approved full diff.
Parked #14: waiting only for W9-01 source interface on main; do not inspect Lead A branches/issues. Decimal0<=scale<=precision refusal was ALREADY approved in issuecomment6072817299, no further decision needed. Current pushed journal lane/W9-13 head58966e0. Updated clarification: issuecomment6299575080; no new decimal decision is pending.

Exact questions/options/recommendations remain in questions.md. #6 is active; #20 has a ready category PR with remaining value acceptance parked; #14 remains dependency-blocked. All three remain open; no product PR/merge yet.

## Next actions

Poll fresh exact-head CI without persisting JSON/heartbeats. Merge green reviewed current PRs promptly via merge API; refresh other own open lanes from new main, preserve additive entries and let CI rerun. Local tests only for code conflicts. Keep lanes parallel; do not run local fulls again. Re-check main/dependency interface when it moves, and unpark BigQuery only if its needed interface is on main. Do not end just to wait. report.md currently holds an interim credential-interruption checkpoint, clearly marked resolved; replace it with the final report at completion or an unresolved hard stop.

Active/idle recover agents: metadata_recover (streaming reviewer), formats_recover, dbt_recover, eventhouse_recover (Iceberg/semantic reviewer), iceberg_recover. Use followup_task to resume idle agents. All local test commands use /home/agent/bin/reap.py. Last observed zombies22 (<1000); credentials/git/gh operations succeed. Worktrees: /workspace/shape-w9-08/-09/-10/-11/-12/-16. All heavy/full queues are intentionally stopped; old status.json is historical and must not cause a restart.

## Shared CI repair — current priority

[PR #40](https://github.com/sqllocks/shape/pull/40), branch `fix/public-b-certifi-2026-10-20`, head `3f6254768085d6f96683550c8939998170be989f`, is ready and independently APPROVED. Worktree `/workspace/shape-ci-certifi`; parent main35addfc4. Only certifi lock metadata (2026.7.22 → 2026.10.19), its generated requirements-docs.txt pin, prepared Playwright browser selection, its missing Documentation-workflow browser installation prerequisite, and a changelog entry changed. Initial CI caught the missing generated docs pin; the existing exporter regenerated it and its check plus uv lock --check passed. Documentation verify on3991 then exposed the missing pinned browser in docs.yml (CI docs-test already installed it). Added exactly that preparation command, parsed the YAML, and preserved every job, gate and assertion. Independent review APPROVED the final3f62547 head; no full local suite rerun. No dependency range, project version, test assertion, gate or tolerance changed; docs.yml only gains its browser prerequisite. Targeted audit old exit1/new exit0, branding test1pass30.95s, lock/static/type/wording checks clean. Full suites delegated to CI. This supporting PR does not add a tenth assigned issue.

The newly surfaced certifi advisory GHSA-6q48-6c7w-9gw2 blocks active PR audit/build/security jobs. Earlier main audits were green before the advisory surfaced; do not ignore the new finding. Semantic docs-test also failed the unchanged Host Grotesk font check; the repair makes it use CI's already-installed pinned browser rather than arbitrary system Chrome. The precise prior font root cause remains unproven.

To free runner slots, root intentionally canceled remaining CI runs on current feature heads PR29, PR30 and PR33: these unchanged-code refreshes must absorb repair main and rerun CI anyway. Their canceled checks are NOT passing. Only the Iceberg CI workflow remains running on a feature head, to validate the new Windows code. Formats CI and the redundant Iceberg Wheels workflow were also intentionally canceled to prioritize the shared repair; all feature heads require fresh checks after the repair merges. Main CI also remains running; completed jobs have no observed failures. PR30's queued one-job REPORT_CARD teardown retry was canceled as part of this deliberate hold; no passing retry claimed. Do not retry old feature jobs before the repair lands.

Next: merge PR40 promptly when all exact-head CI is green and it contains current main, then merge new main into all five feature lanes and push. Run local targeted tests only for any actual code conflicts. Keep tests/assertions/gates unchanged. Continue main CI monitoring; #20/#6 are ACTIVE under the new Owner decision and #14 remains conditional on PR35.

## HARD STOP — 2026-10-20 20:33 UTC

GitHub GraphQL PR40 polling returned HTTP401 Bad credentials. This is the prompt credential hard stop. Root stopped remote work and interrupted the only active agent watcher. No new merge or authentication configuration change attempted. See report.md for the final handoff. Last successful PR40 final-head snapshot:14success/16running/no observed failure; main20success/7running/no observed failure. Counts:1/9merged11.1%,5/9active55.6%,3/9parked33.3%,8/9unmerged88.9%. Resume only after credentials are restored and the prompt/state are re-read; current CI status is not established.

## RESUMED — 2026-10-20 20:35 UTC

Fresh commands authenticated successfully to GitHub REST and GraphQL without any authentication/settings change. The401 was confined to the inherited environment of the long-running watcher, now stopped; do not use long-lived shell processes for GitHub polling. Use fresh exec commands each cycle so current runtime credentials apply. The scratch checkpoint was pushed successfully as108f18f. report.md is marked interim/resolved, not final. Latest PR40 snapshot:19success/11running/1conditional skip/no failures, exact3f62547 unchanged and APPROVED. Continue required CI/merge/refresh work. User requests full count updates every five minutes plus merge/blocker changes. Latest complete issue counts remain1/9merged11.1%,5/9active55.6%,3/9parked33.3%.

## Owner resumption directive — 2026-10-20

Push audit: all owned implementation/scratch commits were already reachable on origin; no unpublished commit remained. Historical placeholder branch refs differ from old lane remotes but contain zero commits absent from all origin refs; no force push was used. A non-fast-forward rejection on historical lane/W9-11 was not a credential failure. Current implementation tips are in the lane table; support3f62547 and scratch10af006 were already pushed at the audit. New #14 decision journal58966e0 was signed/pushed.

Record the exact #20/#6 approval from questions.md in each lane. Both lanes are unparked and running in parallel while CI gates PR40. No reference data/threshold/value rewriting is approved. #14 decimal policy is resolved; PR35 is OPEN and the lane remains dependency-blocked. Do not inspect or edit Lead A's implementation.

Merge order now: PR40 first (Lead A also depends on it); then PR29/30/33 (PR32 already merged); then34/31; then20/6; then14 after PR35/main dependency. Refresh each own open lane once from main and let exact-head CI run; no local reruns except targeted tests for code conflicts. All PRs ready, all applicable triggered CI jobs including both kernels, independent review and current main required.

Credential rule REPLACED:401/403/failed push is a hard stop only if persistent. Retry git ls-remote origin plus one gh api call at5,10,20minutes, doing local work between attempts; stop only if all three fail. Never print tokens or modify authentication settings. Use fresh command environments for polling.

Current counts after unpark:9total,1merged11.1%,7active77.8%,1dependency-blocked11.1%,8unmerged88.9%. Continue full user count updates every five minutes plus merge/blocker changes. report.md is an interim historical checkpoint and will be rewritten at final completion.

## Parallel work checkpoint — 2026-10-20 20:58 UTC

PR42 ready, signed/pushed8f5dfb341d15068f6b2e646b1d3915ea41f9cfe4; independent frz_review APPROVED exacthead. Targeted acceptance11red; stronger base-behavior3red; review identity2red and forged-pass1red; final107green plus21Python controls. Static checks pass. Worktree/workspace/shape-frz-01 clean. Artifact attached. No merge/Nightly/closure; merge order remains40→29/30/33→34/31→42→6→14. #20's four remaining value workloads are honestly parked, not passed.

#6 eventhouse_recover: currentmain baseline14red eachmode; initial24targetedgreen, finishing schema/capsule/1GB heavy and changed-module boundaries. Source changes uncommitted, no full run. #14 journal58966e0 pushed; PR35 stillOPEN.

PR40 last snapshot21success/9running/1structural skip/no failures; exact3f62547 unchanged and APPROVED. Iceberg PR31 current8c9fa927 Windows3.14 fullcorejob118837250058 SUCCESS; Windows3.11 stillrunning. Existing be2 fix supersedes old create_table failures; do not edit source speculatively. Main35 Documentation/Security complete success; CI last23success/4running/no failures.

Issue counts:1/9merged11.1%,6/9active66.7%,2/9blocked-or-parked22.2%,8/9unmerged88.9%. The ready #20 category PR is partial implementation, not full delivery. Continue user count updates every five minutes plus merge/blocker changes.

## Source-validation milestones — 2026-10-20 21:10 UTC

Iceberg PR31 exact8c9fa927: BOTH Windows3.11job118837250256 and3.14job118837250058 completedSUCCESS. Old create_table failures are resolved on that head. No speculativeextraWindowsfix or localrerun. Prepared-browserfontfix remains in PR40 and both docs jobs pass there; absorb on mandatorymainrefresh.

#6 author/reviewer collaboration corrected privacy/path/closed-contract/engine/literal-glob boundaries with targetedred→green. Arrow25 ParquetFile read-ahead caused file-wide1GBretention; explicit pre_buffer=False preserves batches/order and fixes it. Both1GB cases pass with unchanged256MiBgrowthbound; focused/changed-module selection221passed52.37s. Measurement childprocess is fresh, fixturewriteroutsideit, actualfile>=1GB; no high-water masking. FinalPython/static/normativechecks and independent exactSHAreview pending. OpenreadyPRwith explicitdependencyPR42 onceownchecks pass; don'twaitfor42mainjusttostartCI, but MUSTNOTmerge6before42/currentmaincombinedgates. No harness copying/cherrypicking.

Read-only #20 context/name reanalysis retains4parkedwholeworkloads. Authenticatedcapture-session timestamp exists but faithfulper-workloadclockcontext is unproven; display-namealiases couldpartiallynormalize nameswithstableIDs but codedata stillblockswholegenworkloads. Questions.md capturesprecisedistinction. AtnextmandatoryPR42mainrefresh, clarifyjournalratherthanextraCIchurnnow.

PR40 latest23success/7running/1structural skip/no failures; main35 latest26success/1running/no failures. Remainingjobsareactivelytestingmulti-phase suites, not provisioning; minversions alone took42m47s withtwo full light suites. No relaxedgate is authorized. Userfullcountupdate21:09UTC; nextdueapproximately21:14UTC.

## Ready child-profile PR — 2026-10-20 21:14 UTC

PR43 is ready/non-draft and artifact attached by root. Exact39760e6927b36dc32f7d0bd7f4977a36719f70ac signed/pushed, clean worktree, independent frz_review APPROVED. Refreshed-main initial14red bothmodes; review boundary regressionsred→green. Finaltargeted221inclboth1GBheavy, Python220nonheavy, consumer235, child35, capsules171, conformance39, and directsafe-capture131 allpass; configuredchanged-source mypy19/Ruff/format/wording/requirements/CLI/stableAPI clean. No full local run. Existingfull-roundtripassertion retained with explicitcapture=full; independentdefaultsafe/leaktests added. Childcheckpointpolicy unchanged and no new private-state/profile-capsule leak found.

All six active assigned issues now have ready independentlyreviewed implementations (29/30/31/33/34/43). Plus merged9, codeimplementationready7/9=77.8%; actualdelivery1/9=11.1%; unmerged8/9=88.9%; blocked/parked20+14=2/9=22.2%. PartialcategoryPR42 is not full #20 delivery. Merge priority remains40first, then29/30/33, then34/31, then42/43, then14after35.

Rootattached43 after childattachmenttool stalled; no further artifact attachment needed. Authorsavailableforrefresh: dbt_recover29; eventhouse_recover30/43; formats_recover34/42; iceberg_recover31; root33. metadata_recover currently watches40 with fresh commands and exactheadreviewunchanged. Mainlast26success/1runningUbuntu3.12; completedDocs/Securitygreen. Userfullcountupdate21:14, nextdue~21:19.

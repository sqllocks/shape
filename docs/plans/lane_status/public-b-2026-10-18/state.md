# Shape Public Lead B — current state

Updated 2026-10-20 after PR40 merged and all own open lanes refreshed. Work continues; do not end to wait. Original prompt: `/workspace/attachments/e304682d-bc67-494c-8b2e-3d8b1436dda0/5-NEW-WINDOW-shape-public-B.md`. Latest Owner resumption/throughput directives in chat override conflicting original rules. Scratch branch: `lead/public-b-2026-10-18`, worktree `/workspace/shape`. Every commit signed off and immediately pushed. Credentials work in fresh commands; no authentication/settings changes.

## Progress

Nine assigned issues: 2 merged (22.2%), 6 ready/reviewed (66.7%), 1 dependency-blocked (11.1%). Seven unmerged (77.8%); eight implementations ready including merged issues (88.9%). PR43 catalog-depth integration regression is fixed, signed/pushed and independently APPROVED exact548b976. Supporting PR40 is outside the denominator. Give full counts every five minutes and blocker/merge milestones.

Current main: `5c6d46ff1703db9fd2307bc4ebd4fb087e9af733`, PR30 merged2026-10-20T23:47:06Z. All6 source-head workflows SUCCESS on cad8cfd: CI39504103202, Documentation39504103156, Security39504103173, DCO39504103258, Container39504103180, action-selftest39504103224. Issue12 closed through PR. New-main CI39517243088, Documentation39517243016 and Security39517243183 initialize/run; no green claim yet. Prior main53 Documentation39504029094 and Security39504029453 succeeded, but CI39504029294 automatically CANCELLED when main moved with one job left: never call this a full pass. Earlier main35addfc4 all workflows green (W9-08 record). metadata_watch monitors current main.

## Assigned lanes

| Issue | Branch / exact pushed head | PR | Stage |
|---|---|---|---|
| #9 W9-08 | lane/W9-08-finish / 91ef6200266bf962246d1553356b26a442be2303 | https://github.com/sqllocks/shape/pull/32 | MERGED35addfc4c310b85a62cbe7f315e1d50fb8e426f8; all source-head and merged-head workflows green |
| #11 W9-10 | lane/W9-10-finish / fd5211c42f741ff2ed284ec767c2c36d24a783e0 | https://github.com/sqllocks/shape/pull/29 | Ready; main5c6d46 contained; exact independent APPROVAL eventhouse; fresh CI pending |
| #12 W9-11 | lane/W9-11-finish / cad8cfd96a18453cb426de5b1d22e1cff9f9147a | https://github.com/sqllocks/shape/pull/30 | MERGED5c6d46ff1703db9fd2307bc4ebd4fb087e9af733; all6 source workflows green; newmain gate pending |
| #13 W9-12 | lane/W9-12-finish / 190c7631739dfbafff64c2ed34673c94bc1b1b20 | https://github.com/sqllocks/shape/pull/33 | Ready; main5c6d46 contained; exact independent APPROVAL eventhouse; fresh CI pending |
| #10 W9-09 | lane/W9-09 / 8a7d047b7c48038b08b858b8e3505cc827b13aff | https://github.com/sqllocks/shape/pull/34 | Ready; main5c6d46 contained; exact independent APPROVAL metadata; fresh CI pending |
| #17 W9-16 | lane/W9-16-finish / edf678977bb10ede4f26e0f340eb70499e8a8e82 | https://github.com/sqllocks/shape/pull/31 | Ready; main5c6d46 contained; exact independent APPROVAL metadata; fresh CI pending |
| #6 W9-05 | lane/W9-05 / 548b97651b169f626d891f94d782dfe697d55cfc | https://github.com/sqllocks/shape/pull/43 | Ready; contains main5c6d46; catalog max_depth fix independently APPROVED frz_resume_review exact head; freshCI pending; depends42 |
| #20 FRZ-01 | lane/FRZ-01-finish / 4eb7f38f2b20875d6854e5983a732d7c06d6fbe9 | https://github.com/sqllocks/shape/pull/42 | Complete3-part fix independently APPROVED exact head; all4value acceptance green; freshCI/merge/mainNightly pending; no closure |
| #14 W9-13 | lane/W9-13 / 58966e09218ef3a8c2cdb96ad22ccbcf6fbd6033 | none | Decision journal only; source-interface dependency blocked until Lead A45 (replacement for35) merges |

Worktrees: `/workspace/shape-w9-08`, `-09`, `-10`, `-11`, `-12`, `-16`, `-05`; `/workspace/shape-frz-01` for42. Current authors/watchers: dbt_watch29; eventhouse_watch30/43; root33; formats_watch34/42. Restore a31 author in the free slot when main next moves; metadata_watch independently reviews31/34. All tracked trees clean except root33's old owned `docs/plans/lane_status/W9-12-commands.md` unstaged changes; preserve those, do not commit old raw commands. dbt has known empty untracked `not-json.tmp`; leave it.

All six remaining open implementations refreshed from main5c6d46 and signed/pushed. Independent approvals: eventhouse_watch29/33, metadata_watch31/34, frz_resume_review42. PR43 automatic source merge introduced catalog recursion max_depth omission, caught by independent review. Signed/pushed548b976 repairs it with targeted2red eachkernel→110green child+Eventhouse eachkernel, static/mypy705/wording clean; final exact review APPROVED. Other refreshes had documentation/requirements/changelog conflicts only; all additions preserved and no local reruns. Old lower-priority31/34 canceled runs are superseded by newly triggered exact-head CI/Wheels. Canceled old42 Documentation39509002209 and43 Container39514264890/action-selftest39514264909/Documentation39514265020 to free obsolete queue work; never passes. Current exact-head gates are required.

Current authors/watchers: dbt_watch29; eventhouse_watch43 and independent29/33; root33; formats_watch34/42; iceberg_watch31; metadata_watch currentmain and independent31/34; frz_resume_review independent42/43. Availability through00:30UTC, renew as needed.

## Shared blocker resolved

PR40 https://github.com/sqllocks/shape/pull/40 merged as main53d3d0c after independently approved head `3f6254768085d6f96683550c8939998170be989f` had all4 workflows SUCCESS:30successful jobs and only conditional documentation publish skip. Worktree `/workspace/shape-ci-certifi`, branch `fix/public-b-certifi-2026-10-20`. Repair: certifi2026.7.22→2026.10.19 locked artifacts/generated docs export; prepared Playwright browser selection and missing Documentation browser installation; changelog. No gate/assertion/range/tolerance change. Targeted audit oldexit1 GHSA-6q48-6c7w-9gw2→newexit0; branding1pass30.95s; static/lock/export clean. Source-head links in CI-runtime-repair.md. New-main full gate still pending.

## Next merge actions

1. First group29/33 (30 now merged): merge whichever is fully green, independently approved and current first; do not hold a green group member waiting for another. PR32 and PR30 already merged.
2. Then34/31; then42/43 (43 depends42); then14 after45/main source interface.
3. Every merge requires nonempty complete applicable exact-head checks, ALL triggered workflows successful, current main contained, independent approval. Use PR merge API with exact SHA, merge commit and DCO. No direct main push.
4. After each main move, refresh other own lanes, push, rebind reviews and let CI run. Local targeted tests only for actual product-code conflicts. Preserve additive changelog/requirements entries. No local full pytest/makecheck.
5. Watch new main after each merge. Genuine main failure: one small repair PR if possible; otherwise revert own merge through PR and stop as original prompt requires.

After environment restart, bounded parallel watches through00:30UTC: dbt_watch29/30, eventhouse_watch33/43, formats_watch42, metadata_watch main53. Canceled31/34 core runs stay held until the next main-refresh signal. Spare author/reviewer slots are available; restore31 author and independent reviewers from this prompt/state when new work is needed. Each fresh shell per poll avoids stale inherited GH_TOKEN. Renew watches as needed; don't end to wait. No raw log/gate-state/heartbeat archives. Evidence only PR/run links and acceptance red→green. Current targeted evidence and pending live actions remain in each lane record and interim report.md; no live pass claimed.

## Owner decisions and parking

Throughput decision row is committed on scratch and included in PR29 for main. Premium process05/08 and114–122 are owned by Premium Build A; don't touch Premium or Lead A branches/issues. All owned local implementation commits were pushed; historical placeholder refs contain no unpublished work and must not be force-pushed.

#20/#6: approved NOT COMPARABLE only when recorded0.9.1 itself errors/unsupported. Reviewed committed registry names `profile:edge/x_pq_list.parquet` and `profile:edge/x_pq_nested.parquet`, both recorded TypeError/no values. Separate reporting, never pass/timing credit. Authenticated records/datasets, forged pass/identity negative tests; numeric/value functions unchanged. PR42 targeted107green+21Python controls; red evidence in lane. PR43 Wanted5 uses this category, no harness copy. Initial14red both kernels; final221focused incl both1GB cases with unchanged256MiB growth limit, Python220nonheavy, consumer235/capsules171/conformance39/directsafe131. Conservative child temporal suppression, escaped paths, closed contracts, engine/error boundaries verified. Parquet pre_buffer=False fixes1GB retention without changing batches. Documentation warning refs and isolated INSTALL transcript corrected; final old-head4704 Docs39500618099 green, all8 pages passed. Prior exact-source approvals persist across reviewed automatic refreshes.

All four original #20 value failures are NOW FIXED, tested and independently APPROVED in exact42 head0b95d31. Earlier parking rationale was superseded after provenance review. Generation aliases are fixed field/domain-scoped bijections tied to authenticated original/new reference lineage; never fit by frequency, charge similarity or generated output order. Counts/weights/probabilities/numeric charges and all existing clauses stay unchanged. Actual healthcare80/80 andcapital64/64 pass allclausesa-f; original five baseline seed digests authenticate first. Restored original verifier on SAME outputs reproduces14originalflags.

Time-only replay is a PRE-computation implicit-date input for ONLY2authenticated fixtures, not output exclusion/rotation. Original capture run38332677511/job115085741837, sourcee41a67ea, full capture step2026-10-13T01:47:38Z–02:17:06Z; session recorded_utc01:47:50Z and local machine.started01:47:50 agree; no workflow/worker TZ overrides. Whole-window same-date evidence establishes date independently of expected histograms. Scoped dtparse._midnight context preserves explicit dates/restoresfinally, same verifier+timed context; generic recorder unchanged. Actual bothfixtures0mismatches, exit0; hostSunday/2027, provenance/input/distribution/timing/restore negatives pass. Restoring old profile/timing code reproduces5red controls. Final targeted138pass eachkernel; configuredmypy703+strict2newmodules, Ruff12files/format/wording clean. Reviewer frz_review APPROVED final0b95d31; no reviewer reruns. No recording/manifest/fixture/numeric/threshold edits. Existing isolated history0.9.1 env uses Arrow26, but identical reference blobs and all original output digests authenticate; no whole-environment/historical-timing equivalence claim.

PR42 ready title/body cover all3parts; Refs20 only, not Closes. No additional Owner comparison decision remains. After42merge, dispatch Nightly on main and require all5parity shards, parity-publish andsqlserver-e2e green; link it and close20 only through subsequent PR. Do not claim Nightly pass before it runs. Current questions.md/report.md and https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664 reflect complete fix. PR43 still depends42/main combined checks.

Root deliberately canceled PR31 CI39504110302/Wheels39504110292 and34 CI39504158279 to prioritize firstgroup29/30/33 in hosted runner queue. Cancellations NOTpasses; do not retry until next main refresh. Docs/Security on31/34 are SUCCESS; source/reviews unchanged. Root also canceled obsolete42 intermediate-head queued DCO39508152233/Docs39508152238; only final0b95d31 checks count. New final42CI39509002226/DCO39509002220/Docs39509002209/Security39509002213 are queued/running. No raw archives.

#14: decimal0<=scale<=precision BEFORE connect, name column, already approved Oct9 and reaffirmed Oct20. PR35 remainsOPEN at latest22:56 read. When merged, start fresh `lane/W9-13-finish` from THEN main and reuse interface; don't carry old journal branch's unmerged historical Lead A ancestry. No new decimal-policy question. Dependency options/recommendation in questions.md and issuecomment6299575080.

## Environment and recovery

Use `/workspace/public-b-validation/test-env.sh`, uv0.8.17 locked workflow, no pip in project env. Every test command through `python /home/agent/bin/reap.py`. Post-restart zombies92 (<1000); hard stop above1000. No local full-suite workers: old worker.py controlstoptrue and historical JSON must not be restarted. No GitHub polling in long-lived shells: use fresh exec commands. One403 workflow-running error or404 active-log-not-ready is not credential failure. Persistent401/403/failedpush: retry gitlsremote+oneghAPI at5/10/20minutes while local work continues; only stop if all3fail. Never print tokens/change config.

No skips/xfail/deleted/weakened tests, loosened gates/tolerances, tags/releases/publish, direct protected push, credential/settings changes, self-hosted runners or banned names. report.md is INTERIM; rewrite final only when all assigned issues merged/parked or unresolved hard stop. Continue now after every reset/compaction.

Environment restarted briefly; original prompt and this state were reread immediately. Files and commits persisted, scratch recovery checkpoint follows eff633b. Current source heads/main verified unchanged and credentials work; no local test workers restarted.


## PR43 actual compatibility failure; repair active

Windows3.14 CI job118909326600/run39504152982 failed on exact66ace096 (https://github.com/sqllocks/shape/actions/runs/39504152982/job/118909326600). Author eventhouse_watch reproduced16failed/92passed in targeted existing nested generation/diff/CLI modules. Three causes: fixed-binary/UUID child traversal now rejects formerly opaque fields; legacy parent mean-length drift name changed; generation expects extrema removed by safe redaction. Narrow repairs underway, independent frz_resume_review assisting read-only and reviewing actual delta. Do NOT weaken assertions/redaction/scanner or reconstruct intentionally private extrema from retained unvetted histogram bins; use legitimate public/schema/default bounds when private extrema absent. No full local runs. Main/firstgroup29/30/33 remain failure-free; keep merging that group as soon as exact gates complete. PR43 is temporarily active-fix, not ready; counts1merged6ready1activefix1depblocked.

## PR43 compatibility repair completed 2026-10-20 23:19 UTC

Signed/pushed5ae6460db5f2ce0ee06070434104b71745a63523, independently APPROVED exact head by frz_resume_review. Red16failed/92passed existing targeted nested generation/diff/CLI; green170targeted eachkernel,183changed learn/fit+nested consumers,24scalar cases and2snapshot/merge lifecycle cases. Ruff/format,configuredmypy705,wording/diff clean. Fixedwidth-only bijective internal hex state preserves actual count_distinct and exact/HLL lifecycle; public whitelist drops all surrogate values. Extension storage/null handling and transformed-schema resume/merge covered. Legacy parent length names restored, child distribution paths retained. Missing safe extrema discard incomplete length model/use existing public1–3 defaults; no private histogram reconstruction. Existing assertions,privacy,heavy bounds,gates unchanged. Prior failed66ace CI is not a pass; fresh exact5ae CI required plus42 dependency/currentmain. Counts1merged7ready1dependency-blocked;8of9unmerged88.9%.

## Current integration repair after PR30 merge

Independent frz_resume_review found that incoming reference/profile.py catalog profile_tables recursion calls _profile without forwarding PR43 max_depth. Automatic merge success is insufficient; author eventhouse_watch is adding fake catalog max_depth0/1 targeted red/green and fix. Exact094d9cf is NOT APPROVED. No full local suite. Rebind review only after signed/pushed repair and targeted green. Counts2merged5ready1activefix1dependency-blocked;7of9unmerged77.8%.

## Catalog depth integration fix complete

Signed/pushed548b97651b169f626d891f94d782dfe697d55cfc, independently APPROVED exact head by frz_resume_review. One source line forwards max_depth through catalog recursion unchanged; new fake-catalog0/1 cases also check sampling metadata. Genuine2red eachkernel against094d;110child+Eventhouse module tests green eachkernel. Ruff/format,mypy705,wording/diff clean. No assertions/gates weakened, no reviewer reruns or local fulls. Counts2merged6ready1dependency-blocked;7unmerged77.8%. Fresh exact-head CI and42dependency remain.

## Runner scheduling hold 2026-10-20 23:56 UTC

Root prioritized remaining firstgroup29/33 by requesting scheduling cancellations of later-priority current31 coreCI39517323914 andWheels39517323900 (edf6789), and34 coreCI39517315974 (8a7d047). These are NOTpasses. Lightweight Docs/Security/DCO/Container/selftests continue. Hold31fullCI/Wheels and34fullCI until BOTH29+33merge and root explicitly advances stage; source refreshes may proceed after eachmainmove, but cancel auto-triggered fulls during this hold.42/43fullCI continues for early harness/nested regression detection. No gate weakened or source changed. New29/33/currentmainCI progressing with no failure.

## Dependency routing update 2026-10-20 23:59 UTC

PR35 CLOSED UNMERGED, superseded by LeadA PR45 with identical source tree and corrected DCO trailers, per https://github.com/sqllocks/shape/pull/35#issuecomment-6304433722. Issue2 remains OPEN. Watch PR45 state/mergedAt instead; unpark14 only when that W9-01 source interface merges, then start fresh from currentmain and reuse it. No LeadA branch/issue modified. Decimal decision unchanged.

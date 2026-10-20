# Shape Public Lead B — current state

Updated 2026-10-20 after PR40 merged and all own open lanes refreshed. Work continues; do not end to wait. Original prompt: `/workspace/attachments/e304682d-bc67-494c-8b2e-3d8b1436dda0/5-NEW-WINDOW-shape-public-B.md`. Latest Owner resumption/throughput directives in chat override conflicting original rules. Scratch branch: `lead/public-b-2026-10-18`, worktree `/workspace/shape`. Every commit signed off and immediately pushed. Credentials work in fresh commands; no authentication/settings changes.

## Progress

Nine assigned issues:1merged11.1%,6active-ready66.7%,2blocked-or-partially-parked22.2%;8unmerged88.9%. Seven ready implementations including the merged issue77.8%. Partial #20 category is not full delivery. Supporting PR40 is outside the nine-issue denominator. Give full count updates every five minutes plus merge/blocker milestones; last full update approximately22:03UTC.

Current main: `53d3d0c1a740fcca0665c216c3ebdabaaac95e49` from PR40. Its post-merge workflows CI39504029294, Documentation39504029094 and Security39504029453 are still queued/running; no observed failures, no complete main-green claim. Metadata agent watches. Prior main35addfc4 had all workflows green, recorded in W9-08.md.

## Assigned lanes

| Issue | Branch / exact pushed head | PR | Stage |
|---|---|---|---|
| #9 W9-08 | lane/W9-08-finish / 91ef6200266bf962246d1553356b26a442be2303 | https://github.com/sqllocks/shape/pull/32 | MERGED35addfc4c310b85a62cbe7f315e1d50fb8e426f8; all source-head and merged-head workflows green |
| #11 W9-10 | lane/W9-10-finish / 00d648ce65c9f58ae003168f38a1a8dd46db78aa | https://github.com/sqllocks/shape/pull/29 | Ready; main53 contained; exact independent APPROVAL eventhouse; fresh CI pending |
| #12 W9-11 | lane/W9-11-finish / cad8cfd96a18453cb426de5b1d22e1cff9f9147a | https://github.com/sqllocks/shape/pull/30 | Ready; main53 contained; exact independent APPROVAL dbt; fresh CI pending |
| #13 W9-12 | lane/W9-12-finish / 1535427ec055eb5e197c91a86e10df17347a3be3 | https://github.com/sqllocks/shape/pull/33 | Ready; main53 contained; exact independent APPROVAL eventhouse; fresh CI pending |
| #10 W9-09 | lane/W9-09 / 0178fa0c93ba0bc9133a7a6e63067a97f9add115 | https://github.com/sqllocks/shape/pull/34 | Ready; main53 contained; exact independent APPROVAL metadata; fresh CI pending |
| #17 W9-16 | lane/W9-16-finish / 81cff73ea799039a70b5c584ea7cbfd1e3ec1749 | https://github.com/sqllocks/shape/pull/31 | Ready; main53 contained; exact independent APPROVAL metadata; fresh CI pending |
| #6 W9-05 | lane/W9-05 / 66ace0965ce5442cb931abc77d4b8bdab43f08a9 | https://github.com/sqllocks/shape/pull/43 | Ready; main53 contained; exact independent APPROVAL frz_review; depends42 landing and combined main refresh/CI |
| #20 FRZ-01 | lane/FRZ-01-finish / aa5be107c62ca2d98901a43d627b4e028d626cf7 | https://github.com/sqllocks/shape/pull/42 | Error category ready, main53 contained, exact independent APPROVAL frz_review; four value workloads still PARKED; no closure |
| #14 W9-13 | lane/W9-13 / 58966e09218ef3a8c2cdb96ad22ccbcf6fbd6033 | none | Decision journal only; source-interface dependency blocked until Lead A35 merges |

Worktrees: `/workspace/shape-w9-08`, `-09`, `-10`, `-11`, `-12`, `-16`, `-05`; `/workspace/shape-frz-01` for42. Authors: dbt_recover29; eventhouse_recover30/43; root33; formats_recover34/42; iceberg_recover31. All tracked trees clean except root33's old owned `docs/plans/lane_status/W9-12-commands.md` unstaged changes; preserve those, do not commit old raw commands. dbt has known empty untracked `not-json.tmp`; leave it.

All seven refreshed lanes contain main53 and independent approvals are bound to the exact heads above. Refreshes had only additive changelog conflicts (33/34/42); all entries preserved, no product-code conflicts, so no local reruns. Fresh CI is queued/running with no observed new failure. Canceled older-head runs are NOT passes. Hosted runner queueing is currently visible; don't claim a fixed completion time.

## Shared blocker resolved

PR40 https://github.com/sqllocks/shape/pull/40 merged as main53d3d0c after independently approved head `3f6254768085d6f96683550c8939998170be989f` had all4 workflows SUCCESS:30successful jobs and only conditional documentation publish skip. Worktree `/workspace/shape-ci-certifi`, branch `fix/public-b-certifi-2026-10-20`. Repair: certifi2026.7.22→2026.10.19 locked artifacts/generated docs export; prepared Playwright browser selection and missing Documentation browser installation; changelog. No gate/assertion/range/tolerance change. Targeted audit oldexit1 GHSA-6q48-6c7w-9gw2→newexit0; branding1pass30.95s; static/lock/export clean. Source-head links in CI-runtime-repair.md. New-main full gate still pending.

## Next merge actions

1. First group29/30/33: merge whichever is fully green, independently approved and current first; do not hold a green group member waiting for another. PR32 already merged.
2. Then34/31; then42/43 (43 depends42); then14 after35/main source interface.
3. Every merge requires nonempty complete applicable exact-head checks, ALL triggered workflows successful, current main contained, independent approval. Use PR merge API with exact SHA, merge commit and DCO. No direct main push.
4. After each main move, refresh other own lanes, push, rebind reviews and let CI run. Local targeted tests only for actual product-code conflicts. Preserve additive changelog/requirements entries. No local full pytest/makecheck.
5. Watch new main after each merge. Genuine main failure: one small repair PR if possible; otherwise revert own merge through PR and stop as original prompt requires.

Bounded parallel watches through22:30: dbt29/30, formats34/42, iceberg31, eventhouse33/43, metadata main53. Each fresh shell per poll avoids stale inherited GH_TOKEN. Renew watches as needed; don't end to wait. No raw log/gate-state/heartbeat archives. Evidence only PR/run links and acceptance red→green. Current targeted evidence and pending live actions remain in each lane record and interim report.md; no live pass claimed.

## Owner decisions and parking

Throughput decision row is committed on scratch and included in PR29 for main. Premium process05/08 and114–122 are owned by Premium Build A; don't touch Premium or Lead A branches/issues. All owned local implementation commits were pushed; historical placeholder refs contain no unpublished work and must not be force-pushed.

#20/#6: approved NOT COMPARABLE only when recorded0.9.1 itself errors/unsupported. Reviewed committed registry names `profile:edge/x_pq_list.parquet` and `profile:edge/x_pq_nested.parquet`, both recorded TypeError/no values. Separate reporting, never pass/timing credit. Authenticated records/datasets, forged pass/identity negative tests; numeric/value functions unchanged. PR42 targeted107green+21Python controls; red evidence in lane. PR43 Wanted5 uses this category, no harness copy. Initial14red both kernels; final221focused incl both1GB cases with unchanged256MiB growth limit, Python220nonheavy, consumer235/capsules171/conformance39/directsafe131. Conservative child temporal suppression, escaped paths, closed contracts, engine/error boundaries verified. Parquet pre_buffer=False fixes1GB retention without changing batches. Documentation warning refs and isolated INSTALL transcript corrected; final old-head4704 Docs39500618099 green, all8 pages passed. Prior exact-source approvals persist across reviewed automatic refreshes.

Four #20 value-recording workloads remain PARKED: `profile:edge/x_time_only.csv`, `profile:edge/x_time_ampm.csv`, `generate:capital_markets:medium`, `generate:healthcare:medium`. Recorded time weekday counts differ on Sunday; generation code/description values differ under unchanged vocab/TVD/coherence limits. No rerecord/threshold change/output rewriting. Capture recorded_utc is session-start metadata, not proven per-workload timezone/clock context. Stable-ID display-name substitutions could partially normalize names but don't resolve whole workloads' code values. PR42 journal now clarifies these distinctions. Exact evidence/options/recommendation in questions.md and https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664 . Recommend retain strict comparisons until precise further Owner context/policy. PR42 Refs20 only. Never close20 until qualifying Nightly dispatched on main is green on all5parity shards, parity-publish and sqlserver-e2e; link it and close only through PR. Do not dispatch known-incomplete acceptance merely for a coincidental capture weekday match.

#14: decimal0<=scale<=precision BEFORE connect, name column, already approved Oct9 and reaffirmed Oct20. PR35 remainsOPEN at latest22:00 read. When merged, start fresh `lane/W9-13-finish` from THEN main and reuse interface; don't carry old journal branch's unmerged historical Lead A ancestry. No new decimal-policy question. Dependency options/recommendation in questions.md and issuecomment6299575080.

## Environment and recovery

Use `/workspace/public-b-validation/test-env.sh`, uv0.8.17 locked workflow, no pip in project env. Every test command through `python /home/agent/bin/reap.py`. Last zombies45 (<1000); hard stop above1000. No local full-suite workers: old worker.py controlstoptrue and historical JSON must not be restarted. No GitHub polling in long-lived shells: use fresh exec commands. One403 workflow-running error or404 active-log-not-ready is not credential failure. Persistent401/403/failedpush: retry gitlsremote+oneghAPI at5/10/20minutes while local work continues; only stop if all3fail. Never print tokens/change config.

No skips/xfail/deleted/weakened tests, loosened gates/tolerances, tags/releases/publish, direct protected push, credential/settings changes, self-hosted runners or banned names. report.md is INTERIM; rewrite final only when all assigned issues merged/parked or unresolved hard stop. Continue now after every reset/compaction.

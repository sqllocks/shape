# Public A state

Updated 2026-10-20 23:06 UTC. This file is the current checkpoint; lane records retain acceptance evidence.

## Goal and progress

Continue until all seven assigned issues are merged or legitimately parked. Do not end a turn merely to wait or ask. Original prompt: `/workspace/attachments/9302a188-787d-4428-9403-3510325ce6a1/4-NEW-WINDOW-shape-public-A.md`. Read it and this state after any restart.

**0/7 merged (0% done), 7/7 unmerged (100%). Six active (85.7%); one parked (14.3%).** Five active implementation PRs are independently reviewed and awaiting CI; Delta awaits constraints on main. Current main is `53d3d0c1a740fcca0665c216c3ebdabaaac95e49`: Lead B certificate PR40 merged with all30required jobs green. All ready active heads below contain that main. No direct main pushes.

Give meaningful commentary at least every60seconds while working, quantified totals about every5minutes and immediately on merge/blocker changes. CI counts are a separate metric from issues merged.

## Governing Owner decisions — 2026-10-20

- #4 Option1 implemented: refuse undeclared-width indexed strings before any connection, name the column, document key max_length required; four additional exact corpus refusals approved; never guess450.
- #5 Option1 implemented: DuckDB-only parent-first inline FK dialect exception; preserve prepare-all-before-load, enforcement, named failures and transaction rules.
- #8: merge #5 first, then integrate its final constraint helper and finish. Read-only preparation and tests-first checkpoint are complete; final source integration waits for actual #5 main merge.
- Throughput: CI is the full-suite gate; no full local make check or full pytest. Local targeted acceptance/changed-module tests, Ruff/format/mypy and public wording checker only. Targeted acceptance red against base then green. Independent agent reviews scope/security/Wanted; no full-suite rerun. On clean main refresh, push and let CI run without local reruns; code conflicts get targeted tests only.
- Evidence only PR link, green CI run links on exact merged head, acceptance red/green output. No new raw-log archives, gate JSON, heartbeat or receipt indexes. Open ready PRs. Merge reviewed/current/green promptly via PR merge API, merge commit, exact SHA guard. Every triggered exact-head job including both kernels must pass. Conditional untriggered publish remains skipped; never dispatch releases.
- DCO every commit. No skip/xfail/deleted test, weakened assertion, gate/tolerance change, fabricated result, direct protected push, tag/release/publishing, banned name or credential output. GitHub-hosted runners only. Skip other ownership; never touch Premium, planning114–122, Lead B issues/branches, nightly or parity benchmarks.
- Credentials hard stop only after paired git/API retries at5,10,20minutes all fail; continue local work in between. Never print tokens or change git config.

## Current lanes and exact heads

| Issue | Branch / exact head / PR | Stage and next action |
|---|---|---|
| #2 W9-01 | lane/W9-01 `c72395c01a679268f5e49adf7933ebcc9de32b21`; https://github.com/sqllocks/shape/pull/35 | FIRST merge candidate; independently APPROVED exact head.727targeted per kernel, source/security/privacy checks and actual182plugin-list transcripts pass. Latest CI16/32required green,8running/8queued,0failures. No local reruns after clean refresh. |
| #3 W9-02 | lane/W9-02 `7f3acc7f8ea2c56efe506bd7b3f9a64fcea4bd19`; https://github.com/sqllocks/shape/pull/37 | APPROVED exact head; actual#2stacked. Sensitive-artifact8red→green,76cloudPython+1278cloud/bridge default green; exact9conformance cases green. CI pending; merge35first. |
| #7 W9-06 | lane/W9-06 `a3320ebe3e28f6aa26c52da0d4adabd4593c86b2`; https://github.com/sqllocks/shape/pull/38 | APPROVED exact head; actual#2/#3stacked.257acceptance+380affected+176Python green, strictmypy15modules. Native nested data and Delta UTC/microseconds checks pass. CI pending; merge37first. |
| #16 W9-15 | lane/W9-15 `79d06a1462b80b8f06285799a557d483f910b9d7`; https://github.com/sqllocks/shape/pull/39 | APPROVED exact head; actual#2only dependency.117targeted+96Python,46Oracle sink tests,3actualOracleFree23 emulator cases green (not live). Actual Windows CI test isolation fixed: incomplete optional-driver fake red1→46modulepass; route1pass eachkernel. Complete scoped fake exercises actual default_connect, retains11exactboundrows and adds exactbinding-size assertion. No production/dependency/workflow change. Current CI queued; superseded unfinished run canceled. Merge35first. |
| #5 W9-04 | lane/W9-04 `fcbc4209fabe8b5b92e0669c468c1f11d263daa2`; https://github.com/sqllocks/shape/pull/44 | All6Wanted complete, APPROVED exact head; ready/clean.13realPG/MySQL rollback/refusal reruns pass eachkernel; fullretail21750rows PG/MySQL/DuckDB/Fabricfake pass eachkernel. Named constraints/defaults/catalog/trust/destructive preflight/transaction cleanup pass targeted checks.100integrated cases+strictmypy8/static green.418-file unchanged corpus gate passes: only28FK statements+14GO lines added; all legacy-none hashes exact, no other values/nonSQL/refusal/file-set/version changes. Optional DuckDB renderer runtime-import fix red1→green. Latest fcbc moves3URIpreview cases byte-for-byte into optional DB plugin tests: missingplugin3red→core12green; combined15green eachkernel. No tests/assertions/gates removed. CI queued; merge38andprior sources first. |
| #8 W9-07 | lane/W9-07 `897f62663725e0ec88f334fa80d75f014410abe4`; noPR | ACTIVE dependency wait. Feature checkpoint approved;122targeted eachkernel+17independent+192compatibility green. Read-only final helper audit complete. Tests-only897f626 gives8failed/2passed on2b0f623: six derivedCHECK protocol omissions, two pending#5CLI parser cases; explicit-feature controls pass. No source/helper integration yet. After44actualmain, activate owner to integrate final helper/outputpolicy/CLI and fix red protocol cases, targeted bothkernels, review, readyPR, CI, merge. |
| #4 W9-03 | lane/W9-03 `63ab15c3face23d2db989f396291bcba6ececec5`; https://github.com/sqllocks/shape/pull/41 | PARKED on NEW frozen bridge1.1 policy conflict. Original Owner width decision complete. Ordinary actualCI regressions fixed;152design/bridge/contract/seed+106tapes+85CLI+8docs green, independent87green.390outputs/28approvedrefusals. Exact parked-lane code review approved63ab; NOT merge approval because frozen gate blocks. Frozen artifacts/assertions untouched. Owner question below; do not unpark without clear authorization. |

Ready lead bookkeeping PR36 (outside7): `lead/public-a-throughput-2026-10-20`, exact `605411ddd702cf8a86f59f1e33b8359702984de2`, https://github.com/sqllocks/shape/pull/36. One additive Owner-throughput row in COMPLETION_PLAN§2.3. Independently APPROVED; current-main CI pending. Do not touch Premium process wording/planning ownership.

## Merge queue and monitoring

Merge35first, then37/39, then38, then44, then8.41onlyafterOwnerresolution and5/8main. Lead36also needs green/current/review. After each merge fetchmain, merge it once into other active ready heads with signed merge commits, push; no local rerun unless code conflicts. CHANGELOG append conflicts keep both additions. Refresh review if needed. Main may move through other ownership; check at least every5minutes.

PR35 CI run: https://github.com/sqllocks/shape/actions/runs/39504199380. Other five workflows at exactc723 succeeded: Security39504199362, Documentation39504199512, Container39504199388, DCO39504199401, action-selftest39504199405. Do not record incomplete runs as green merge evidence. Two conditional publish skips are expected.32required jobs plus2untriggered skippedpublish checks. Full platform tests and Python-kernel minversions still running at last poll.

Prepared but NOT submitted `/tmp/public-a-merge35.json` guards exactc723, merge_method=merge and signed commit message. Submit only after all triggered checks/workflows green, exacthead still approved and currentmain ancestor. Record actual mergeSHA and green run links in lane record afterward. PRs35/36/37/38/39/41/44 already attached to this chat; attach8whencreated.

Poll all active exact heads about5minutes, diagnose actual failures promptly with targeted tests only. Cancel only superseded/parked own unfinished workflows; never current valid runs or other ownership. Superseded39run39504386297 canceled after79d06a1. Earlier24old/parked plus4old44runs canceled; none counts as evidence. GitHub queue is substantial; no invented ETA. Latest zombies177, below1000hardstop.

## Parked question

#4 exact frozen-policy question: https://github.com/sqllocks/shape/issues/4#issuecomment-6302585629 (authorized comment ends Generated by Codex). See questions.md. Correct canonical unbounded strings change ONLY five historical response.result.ddl declarations in frozen docs/bridge/vectors/1.1/design.json: two star month/day-name NVARCHAR255→NVARCHARMAX; three postgres3nf city/country VARCHAR255→TEXT. Existing frozen test requires everyfield exceptapi_version unchanged. No version-aware legacy path exists, no guessed widths introduced. All other response fields unchanged; frozen vectors/schema/tests unchanged. Current nonfrozen vectors updated exactly. Recommend explicit Owner policy amendment authorizing only those five corrections; alternative requires explicit compatibility/version scope amendment; otherwise remain parked. No reply observed through23:04UTC. Original three Owner questions remain RESOLVED.

## Ownership and worktrees

Root scratch `/workspace/shape`, `lead/public-a-2026-10-18`; state/questions/report only, push after every signed commit. Initial handoff0f38dcffe2091f2904289f5245b2184cb5900d82 (including7dd2c2a) was pushed; all own ahead branches pushed. Implementation worktrees `/workspace/public-a-W9-01`,02,03,04,06,07,15. Throughput `/workspace/public-a-throughput`.

Reviewer `/root/reviewer_resume` and issue agents completed/standby. Use collaboration.followup_task to activate completed agents; send_message alone does not start them. Oracle `/root/w9_15_ci` completed/releasesclean79d06a1. Delta owner `/root/w9_07_resume` activates AFTER44main. #5owner `/root/w9_04_finish` releasescleanfcbc. #4owner `/root/w9_03_finish` parkedstandby. Source owners `/root/w9_02_cloud_finish`(#3), `/root/w9_02_resume`(#7). User explicitly authorizes issue agents+independent reviewer. Do not edit an agent-owned worktree concurrently.

Root support branches clean/pushed, relevant changes already cherry-picked into lanes: W9-04-retail ec3d700, W9-04-fabric efb1707, W9-03-docs a64b380. No separate PR needed. Do not recreate evidence archives.

## Environment and pending live acceptance

Use `/tmp/public-a-uv/bin/uv`0.8.17, `uv run --locked --no-sync`, prepared .venv perworktree. Every test through `python3 /home/agent/bin/reap.py`; no pip into uv environment. Git configured DCO identity Jonathan Stewart <surefooted@gmail.com>; never change config. Shell mutations use set-e. No tokens. Network and GitHub credentials currently work; transient proxy502 and push lookup errors recovered with git/API0 and actualpush0, not persistent credential failure.

Docker PG16/MySQL8.4 emulators healthy under shape-w9-04, owned#5. OracleFree23 emulator retired after3actual successful cases. SQLServer local attempt removed only its own temporary container/image after disk filled; ODBC drivers absent; NO localSQLServer pass claimed. ActualSQLServer compoundCHECK matching-append emulator test defined in existing selected tests, no nightly edits. Disk about6GBfree. Cloudruntime skill was read earlier; no cloudskills currently available.

No live test is claimed passed. Pending Owner accounts/settings: #2PG/MySQL external source roundtrips; #3Snowflake/Databricks; #4cloudtype/UTC; #5cloudconstraints and SQLServer actualemulator followup; #7cloudnestedreadback; #8sixDeltafeaturetables SHAPE_LIVE_DELTA_URI plus normal storage credentials; #16externalOracle catalog rights/MAX_STRING_SIZE parameter-view access. Offline/emulator evidence is separate from live.

After all7mergedorparked, rewrite report.md with each issue status/PR/mergeSHA/greenCIlinks/redgreen/livepending/parkrecommendation, sign/pushscratch and post same finalsummary. Do not finalize merely to wait.

# Public A state

Updated 2026-10-26; final integration in progress. This file is the current checkpoint; lane records retain acceptance evidence.

## CURRENT head — refreshed after B50, one final batch

PR49 head1e3349dd461b401fe3ee1df93e98e7c9252d574b is signed/PUSHED. Parents26234a4 (approved fiveDBTpath correction, realpage1passed292.42s) and actualmain953ccec74e9a9a86e5b37723126ae3d1832432d5 (B50merged). OnlyCHANGELOGconflicted; retainedbothUnreleasedentries. No codeconflicts; Ownercleanrefresh rule meansno repeatlocaltests. B50 introducesNOBridgefrozen/schema/vectorchanges, so207archivesremaincorrect. Reviewercheckingexactcomposition. Both /tmp/public-a-poll-final.py and /tmp/public-a-merge-final.py nowtarget1e3349d. Requireall42checks+7workflowsoverallgreen/currentmain/review before guardedPRmerge. Same3/7done4/7left,zeroparked.

Initialpushfailed transientauth, environmentcurrentrevision234 reportsGH_TOKENready. Paired gitlsremote+ghrepoAPIexit0, immediate pushretryexit0. No credentialprinted/configchange. No persistentcredentialhardstop.

New runs (initiallyPENDING): CI40292839349,Wheels40292839579,Documentation40292839344,Security40292839726,Container40292839555,DCO40292839320,actionselftest40292839470. DCOgreen. No previousheadresultcountsasmergeevidence. Neverendjusttowait. Continueuserupdates and diagnoseactualfailures; onceallgreenmergepromptly. MainB50refreshalreadydone; don'trepeatunlessanotheractualbasemove.

## Latest checkpoint — DBT transcript correction ready

Local integration head26234a4cc0c8d9ea28dd00a1052f761f4f42a727 (signed/pushed on isolated docs progress branch; integration push pending review/current-main check). Parent1a715 CI docs-test failed ONLY five recorded DBT warning prefixes: actual private<work> vs historical shared<checkout>.170otherdocs tests passed, includingCONTRIBUTING and all other affected install pages. Fix changes precisely five expected path tokens, preserving commands/assertions/normalization/warnings/counts. Actual DBT reference page passed292.42s including private native build and plugin tests. Original failure: https://github.com/sqllocks/shape/actions/runs/40289326030/job/121460404509 . No rerun-to-hide or new test relaxation.

Own unfinished1a715 CI and Documentation workflows canceled as superseded; all completed green results remain historical, not merge evidence. No B/main workflow changed. At08:29UTC BPR50 is in final Windows drift checks, stillopen; root checking for actualmerge before nextfullCI to avoid needless repeatedbase refresh. Main still682. Root owns releasedintegration; reviewer validatesexact26234a4. No localfullsuite.

## Latest checkpoint — docs isolation fix pushed

PR49 exact current head is `1a715e5f96fbaa67a023f6036196589572248450`, independently approved and pushed to origin. Base remains6827598. Prior601 finished41/42required checks green; only docs-test failed with native child SIGBUS. All8 core platforms, both database-plugin jobs and Wheels passed. These old-head results are not final merge evidence.

Five docs editable installation commands wrote to the shared source while parallel pages imported its native library. Commands now install into existing page-private project copies, and VIRTUAL_ENV matches each private interpreter. Five effectful regressions: old commands5red, fixed5green perkernel; no production/assertion/gate changes. Real CONTRIBUTING passed in initial parallel run (6passed including regressions); INSTALL initially failed because this shell lacked cached Rust PATH, then passed249.39s with the correct toolchain. That setup failure is not a product regression. Exact SIGBUS causality was not independently proven, but shared-write hazard is reproduced and removed. Reviewer approved exact1a715.

Both guarded scripts now target1a715. New exact-head CI required. LeadB PR50 remains open with Windows3.14 running its core suite at08:00UTC; do not edit/merge B work. Push proceeded rather than wait indefinitely. If B merges first, refresh actual main once under the Owner rule.

Read-only Nightly40272534667 databases-e2e job121408250397 confirms missing python-oracledb, not a demonstrated Oracle product regression: workflow starts only postgres/mysql and invokes all emulator tests without Oracle extras/service/env. Fix belongs to nightly.yml owner (B/Owner); do not edit prohibited nightly.yml. Existing actual Oracle Free23 local3passed remains valid; no live-account claim. Record configuration follow-up in final report.

## Goal and progress

Continue until all seven assigned issues are merged or legitimately parked. Do not end a turn merely to wait or ask. Original prompt: `/workspace/attachments/9302a188-787d-4428-9403-3510325ce6a1/4-NEW-WINDOW-shape-public-A.md`. Read it and this state after any restart.

**3/7 merged (42.9% done), 4/7 remaining (57.1%). All four remaining issues are in ONE READY integration PR #49; zero parked.** #2/PR45, #16/PR39 and #3/PR37 are merged. Current main is682759841dcffd56dff62f74dc90ac18286cc600. Final PR https://github.com/sqllocks/shape/pull/49 closes #4/#5/#7/#8 together, exact head601158e575cbffd3d1aa773b59b5507b0e0af8f4 on lead/public-a-integration-2026-10-26 in /workspace/public-a-W9-06. Root owns the clean/released batch. All builders and independent reviewers have finished; every required scope is approved at this head. Current exact-head CI is running; poll /tmp/public-a-poll-final.py. Do not make additional changes without an actual failure or base move. Once every triggered check is green/current, merge promptly through the merge-commit API with exact SHA guard and DCO message. Historical PR38/41/44 are closed superseded; their issues stay open until49 merges. All Owner decisions are resolved, including Bridge1.3; frozen artifacts remain unchanged. Latest CI corrections cover current-server documentation version fields, a Python3.14 fixture literal in traceback source and portable file-URI failure injection on Windows. All original assertions remain intact. An additive five-module CI acceptance step now runs before the unchanged full core suite on every existing platform. Their precise evidence and current run IDs are in the final sections below. Main advanced while the previous head was at41/42green, forcing this ONE current-base refresh. Jobs conflict uses main security/locking implementation byte for byte plus our regression; four newly introduced 1.2 archive schemas now reflect the latest main publication. Refreshed targeted tests355passed EACHkernel, APIs0break, conformance33/33,191plugins/108requirements. Reviewers APPROVED exact57da; new CI is running. Raw full PR whitespace check includes preserved historical CRLF/EOF bytes; do not claim it is clean or alter frozen bytes. The new-base database-plugin job found a BigQuery URI billing-cap alias regression caused by our copied nested options. Minimal validated plan.options merge fixes it; six additive tests give4red/2controls then331green per kernel. Reviewer approved exact601. Original assertions and all gates stay intact; current exact-head CI is restarting.

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
| #2 W9-01 | lane/W9-01-dco-refresh `5799635244b321bd422f27ec45475272b5983c36`; https://github.com/sqllocks/shape/pull/45 | MERGED PR45 on2026-10-25 as43d3f9aebeb8860489bc46f475203ba2faa8212e, exactapprovedhead5799635. All32required jobs and all6workflows green; two conditionalpublish jobs untriggered/skipped.727targeted perkernel and targetedred/green recorded in W9-01 lane. GreenCIlinks below. No localfullsuite or clean-refresh rerun. |
| #3 W9-02 | lane/W9-02 `fed6366d9222df91e7a2a50695e63ed667c697e3`; https://github.com/sqllocks/shape/pull/37 | MERGEDviaAPIas314749d37aba9141a2ff68a33cfdb91e8a87cca5; all32requiredchecks+6workflowsoverallgreen, exactheadreview/currentbase/DCOverified. Issue3closedthroughmerge. Prioracceptancered/greeninlane;195integrationcasesperkernel+2docspagesgreen. ExternalSnowflake/DatabrickslivevalidationpendingOwneraccounts. |
| #7 W9-06 | Included in `lead/public-a-integration-2026-10-26`; historical PR38 closed as superseded | Native nested implementation and reviewed CI acceleration retained from3aa3571. One final batch PR/CI replaces the per-issue queue. |
| #16 W9-15 | lane/W9-15 `4496683c4aca24d8da2e0ddd57970eb8b5bc0c38`; https://github.com/sqllocks/shape/pull/39 | MERGED viaAPI as3b19d05b940816a74a0ce1ec177365138ce0ed32; all32requiredchecks+6workflowsgreen, exactheadreviewapproved/currentbase/DCOverified. 117targeted+96Python,46sinktests,3actualOracleFree23emulatorcases(notlive), trustdocs28eachkernel. Issue16closedthroughmergedPR. GreenCIlinksbelow; externallivecatalog/MAX_STRING_SIZErights remainOwneractions. |
| #5 W9-04 | Included in batch from6d9f0dd; historical PR44 closed as superseded | All six Wanted items composed into foundation07c1cf1 and independently reviewed. Preserve enforcement, named errors, prepare-all, transactions and 418-file corpus. Issue5 remains open until the batch merges. |
| #8 W9-07 | Included in batch from06cb5d4; no standalone PR | Sole writer nested_duckdb2_review integrated feature foundation38e85b3. Red11/control5 reproduced; targeted integration40green after fixes. Broader targeted/default compatibility/corpus checks underway in both kernels. Actual5-main wait is superseded by the Owner batching direction; final5 helper is already in the batch. |
| #4 W9-03 | UNPARKED, included in batch from63ab15c via foundationb3b321e | Owner2026-10-26 approved version-specific rendering: frozen1.1/1.2 unchanged; CLI/API/newBridge1.3 corrected. Type writer Nested owns batch; Oracle owns isolated1.3 branch. Indexed refusal applies every version. No frozen fixture/baseline edits. |

Ownerthroughputdecisionrowisnowonactualmain90throughLeadBdbtmerge. During37refreshpreservedcanonicalmainCOMPLETION_PLANbyte-for-byteinsteadofduplicatingtheearlierproposedec27/e4ccrow. AllOwnerdirectivesremainbinding. SeparateleadbookkeepingPR36 remainsclosedsuperseded; no reopening/countingasremaining.

## Current integration and merge plan

One batch replaces the old per-issue PR/CI queue. Preserve internal code order #7 → final #5 → #8 → #4 with Bridge1.3, then one main merge closing issues4/5/7/8 together. The latest Owner instruction supersedes waiting for #5 to reach main before integrating #8 within this batch. Do not open the batch PR until targeted tests and independent review are complete. Progress pushes have been verified to trigger zero workflow runs.

Root opened and attached ONE READY integration PR #49. It is now undergoing exact-head CI. Every triggered check, including Wheels and both kernels, must pass on its exact current head. Current-main ancestry, independent approval and DCO remain required; use the merge-commit API with an exact SHA guard. #4 was unparked by the explicit Owner ruling below and is now included. Historical park paragraphs below are superseded.

Root owns the released `/workspace/public-a-W9-06` batch. ReviewerOct25 approved the exact final composition; Delta reviewer approved Delta product and the cancellation repair. Root owns state, PR management and main monitoring. Latest batching direction supersedes the historical queues below.

Main5c6d46f refresh completed once per active readybranch. Only3DOC conflicts: CHANGELOG keepsbothentries; docsDBT/authoring counts183 for45 and185 for37/38/39/44 verified against declarations/actual installed output. All code auto-merged cleanly; no localtest reruns. Reviewer APPROVED all five active exactimplementationheads above; leadrow37also independently approved. Current CI is executing slowly amid runner queues; old-head results are not merge evidence. Additional independent read-only integration review of all four overlapping CLI/profile/bridge/source dispatch modules at45/37 passed: PG/MySQL/cloud andEventhouse routes, sampling, optional boundaries and safe capture preserved; no runtime reruns.

Root caught omitted DCO trailer in published W9-01 refresh7ccce366821b33713d392327691d9fbe43faa74e. Preserved publishedhistory, created replacementbranch/PR45 with exactidenticaltree and intendedparentsc723+5c6d46f, signedhead d0f30a0. Local unchanged DCOchecker reports0missing; independentreview verifies tree/parents/trailer. Closed supersededPR35, issue2remainsopen until45merges. Original lane/W9-01 branch preserved. Do not merge35 or count its canceled runs.

PR45 MERGED successfully through PRmergeAPI asmergecommit43d3f9a, signedmessage and exactheadguard5799635. Do not resubmit its oldtemporarymergeJSON. FourremainingPRs need theirnewcurrentexacthead CI+review beforemerge. PR45/37/38/39/41/44 attached;35/36 closedsupersededhistorically; attach8whencreated.

Poll all active exact heads about5minutes, diagnose actual failures promptly with targeted tests only. Cancel only our unfinished superseded/parked runs OR dependency-blocked heads guaranteed to require a fresh main merge before eligibility. Preserve all current eligible-candidate/main/other-ownership runs. Dependency-paused runs never count as green; require every triggered job on each final eligible exact head. This scheduling policy below replaces the earlier self-imposed superseded-only restriction. Superseded39run39504386297 canceled after79d06a1. Earlier24old/parked plus4old44runs canceled; none counts as evidence. GitHub queue is substantial; no invented ETA. Latest zombies11, below1000hardstop.

## Historical parked question — resolved by the October26 ruling

This section is historical evidence only. The Owner chose version-specific rendering, implemented in PR49; #4 is active, no answer is pending.

#4 exact frozen-policy question: https://github.com/sqllocks/shape/issues/4#issuecomment-6302585629 (authorized comment ends Generated by Codex). See questions.md. Correct canonical unbounded strings change ONLY five historical response.result.ddl declarations in frozen docs/bridge/vectors/1.1/design.json: two star month/day-name NVARCHAR255→NVARCHARMAX; three postgres3nf city/country VARCHAR255→TEXT. Existing frozen test requires everyfield exceptapi_version unchanged. No version-aware legacy path exists, no guessed widths introduced. All other response fields unchanged; frozen vectors/schema/tests unchanged. Current nonfrozen vectors updated exactly. Recommend explicit Owner policy amendment authorizing only those five corrections; alternative requires explicit compatibility/version scope amendment; otherwise remain parked. No reply observed through2026-10-25T16:11UTC. Original three Owner questions remain RESOLVED.

## Ownership and worktrees

Root scratch `/workspace/shape`, `lead/public-a-2026-10-18`; state/questions/report only, push after every signed commit. Initial handoff0f38dcffe2091f2904289f5245b2184cb5900d82 (including7dd2c2a) was pushed; all own ahead branches pushed. Implementation worktrees `/workspace/public-a-W9-01`,02,03,04,06,07,15. Throughput `/workspace/public-a-throughput`.

Environment reset evicted old issue/reviewer agents. Current independent reviewer `/root/reviewer_oct25` APPROVED fourlatestheads; `/root/oracle_ci_oct25` completed and released itsdocfix/worktree. Earlier404refresh reviews remainhistorical. Root owns released implementation worktrees. Spawn replacement issue agents as needed; old names below are historical. Use collaboration.followup_task to activate completed agents; send_message alone does not start them. Oracle `/root/w9_15_ci` completed/releasesclean3d996a6. Delta owner `/root/w9_07_resume` activates AFTER44main. #5owner `/root/w9_04_finish` releasesclean092bc3a. #4owner `/root/w9_03_finish` parkedstandby. Source owners `/root/w9_02_cloud_finish`(#3), `/root/w9_02_resume`(#7). User explicitly authorizes issue agents+independent reviewer. Do not edit an agent-owned worktree concurrently.

Root support branches clean/pushed, relevant changes already cherry-picked into lanes: W9-04-retail ec3d700, W9-04-fabric efb1707, W9-03-docs a64b380. No separate PR needed. Do not recreate evidence archives.

## Environment and pending live acceptance

Use `/tmp/public-a-uv/bin/uv`0.8.17, `uv run --locked --no-sync`, prepared .venv perworktree. Every test through `python3 /home/agent/bin/reap.py`; no pip into uv environment. Git configured DCO identity Jonathan Stewart <surefooted@gmail.com>; never change config. Shell mutations use set-e. No tokens. Network and GitHub credentials currently work; transient proxy502 and push lookup errors recovered with git/API0 and actualpush0, not persistent credential failure.

Docker PG16/MySQL8.4 emulators healthy under shape-w9-04, owned#5. OracleFree23 emulator retired after3actual successful cases. SQLServer local attempt removed only its own temporary container/image after disk filled; ODBC drivers absent; NO localSQLServer pass claimed. ActualSQLServer compoundCHECK matching-append emulator test defined in existing selected tests, no nightly edits. Disk about6GBfree. Cloudruntime skill reread onOct25; currentmanagednetwork unrestricted/enforced, observationscurrent, GHcredentialvariable ready. Actual git/APIcalls both0. Do not print values or bypassproxy.

No live test is claimed passed. Pending Owner accounts/settings: #2PG/MySQL external source roundtrips; #3Snowflake/Databricks; #4cloudtype/UTC; #5cloudconstraints and SQLServer actualemulator followup; #7cloudnestedreadback; #8sixDeltafeaturetables SHAPE_LIVE_DELTA_URI plus normal storage credentials; #16externalOracle catalog rights/MAX_STRING_SIZE parameter-view access. Offline/emulator evidence is separate from live.

After all7mergedorparked, rewrite report.md with each issue status/PR/mergeSHA/greenCIlinks/redgreen/livepending/parkrecommendation, sign/pushscratch and post same finalsummary. Do not finalize merely to wait.

## Latest main404 refresh

Main moved at~01:38UTC through LeadB semantic metadata. All five activeheads above merged404once and pushedsigned. Conflictsonly append-only requirements YAML and CHANGELOG; preservebothentries. No source/test/workflow conflicts or localtestreruns. Reviewer APPROVED allfive exactheads after verifying parents/DCO/publishedheads and bothconflictappendentries. Prior source-counts183/185unchanged. Refresh CI must pass exactnewheads; cancel only superseded ownunfinishedruns. Firstcandidate45 remains first; no Ownerquestion or userwait required.

## Owner note — 2026-10-25 Contract Lead boundary

Owner assigned the former Premium planning window as Premium Contract Lead: writes answered CR-0118(OIDCredirects),CR-0119(keywhitespace),CR-0120(revocation) into specs and settles proposedcontractCRs. It owns everydraftWP, blockedPE-08-09 andPE-08-16specrevision. Buildleads mustnoteditthose; continue readyPE-06/07/08work and pick newlyreadyWPs only after ContractLeadPRs reachbase through normalreadinessrecompute. ContractLead flags changes for activeWPs in reports/contract-application/2026-10-25/questions.md; check aftereachmergebatch. Speed/credential-retry directivesremain.

This session remains ShapePublicLeadA with itsoriginalsevenpublicissues; no PremiumWP/spec was edited and no cross-repository scope switch is implied. Checked the reportpath in this publiccheckout after45merge: absent. Preserve the ContractLead ownership boundary; Premium-specific readiness belongs to its BuildLead sessions.

## W9-01 merged evidence

PR https://github.com/sqllocks/shape/pull/45 ; exacttestedhead5799635244b321bd422f27ec45475272b5983c36 ; merge43d3f9aebeb8860489bc46f475203ba2faa8212e. Acceptance red/green output already in docs/plans/lane_status/W9-01.md. Alltriggered32jobs passed, bothkernel modes included. Greenexact-head workflows:

- CI: https://github.com/sqllocks/shape/actions/runs/39528689508
- Documentation: https://github.com/sqllocks/shape/actions/runs/39528690005
- Security: https://github.com/sqllocks/shape/actions/runs/39528689826
- Container: https://github.com/sqllocks/shape/actions/runs/39528689918
- DCO: https://github.com/sqllocks/shape/actions/runs/39528689413
- action-selftest: https://github.com/sqllocks/shape/actions/runs/39528689400

## Latest main43d refresh

PR37/38/44 refreshed/pushed to43d once. FourDOCconflicts only: CHANGELOG,DBT/authoringpluginheaders,requirements. Reviewed eachmainrequirementIDblock is unchanged inours; mainchangeloglinesall retained; choosingreviewedcloudstack185headerspreservesmain183entriespluscloudsources. No sourceconflicts/localtestreruns. Oracleagentrefreshedmain3603e74 thentrustdocfixa697f47. Reviewer_oct25 APPROVED allfour exactnewheads: publishedhead/currentbase ancestry/DCO/manualdocs resolutions checked. Separate OracleSIGBUS cause unknown; greennewCIrequired, no pre-existing claim. OldCI32green on37/38/44 cannot count onnewheads; currentheadCIrequired. 45isDONE; next37and39areunblocked.

## Main red — small repair PR46 in progress

Freshmain43dCI failed audit/security/build on4advisories in2lockedtools: filelock3.32.7(CVE-2026-112940/112913/112907), setuptools84.0.0(GHSA-xmj3-9gf7-h52w). Build failure isits auditstep. PR45 exactheadCIwasgreen; freshscan aftermerge exposes the sharedbaseline. Userhardstoprule applies: pauseordinarymerges; fix throughone smallPR ifpossible, otherwise revertourmerge throughaPR andstop. No directmainpush or gatechange.

Ready repair https://github.com/sqllocks/shape/pull/46 ; fix/public-a-audit-2026-10-25 ; exactb81ceb8c6a1f77bb817b177748d21dbd2b6438cf ; /workspace/public-a-ci-repair. Independently APPROVED exacthead; currentbase43d; DCOvalid. OnlyCHANGELOG/internaldev-docsgroups/uv.lock/generatedrequirements-docs.txt changed. Entirepublished[project]metadata unchanged; changedlockedversions exactlyfilelock3.32.7→3.20.3 andsetuptools84.0→84.1.

filelock4.2.1 cannotresolve withpublishedSnowflakeconnector<5 requiringfilelock>=3.5,<4. No override/rangechange used. Internaldevpin3.20.3 isunaffected: firsttwoadvisoriesaffect3.20.4..4.2.0, third3.22..4.2.0;3.20.3fixesolderCVE-2026-22701 andPyPImetadata lists0knownadvisories. Independentreview verifiedsame plussetuptools84.1.0clean. Publishedruntime/optionaldependencyranges remain unchanged.

Acceptance red on cleanlockedbase:

```text
Found 4 known vulnerabilities in 2 packages
filelock   3.32.7  CVE-2026-112940     4.2.1
filelock   3.32.7  CVE-2026-112913     4.2.1
filelock   3.32.7  CVE-2026-112907     4.2.1
setuptools 84.0.0  GHSA-xmj3-9gf7-h52w 84.1.0
```

Green sameunchangedauditafterlockedupdate:

```text
No known vulnerabilities found
```

Targetedpackagingacceptance:12pluginwheels built withupdatedsetuptools; existingdependencyfloortest1passed. uvlockcheck,publicwording,diffclean. No localfullsuite. PR46 triggersadditionalwheelchecks throughnormalPRworkflow; everytriggeredjobmustgreen exacthead, no release/publishdispatch. Latest1green/19running/19queued (39registered), currentgatemayregisterconditionalpublishskipslater.

Canceled onlyownfourfeaturebaseline-blocked unfinishedworkflowruns afterrepairready: theirunchangedlockscontainknownvulnerablepins and cannotpassaudit. MainCIcontinues toidentifyanyotherfailure; otherownershipuntouched. After46merges andmainCIrestored, refreshfouractiveheads once, pushno localrerunsunlesscodeconflicts, renewreviewasneeded and resume37/39→38→44→8. Never countcanceledruns as merge evidence. Rootownsrepair; reviewer_oct25 approved.

Repair46generatedexportfollowup: original465a9c0CIlock-checkfoundrequirements-docs.txtstale. Unchangedgeneratorcheck red→CONTRIBUTING-prescribed--write→green. Newf52add9changesonly2generatedversionlines, matcheslock. All4existinguv-environmentcontracttests passed. Independentreview APPROVED exactf52add9/DCO/currentbase43d; priorsecurity/publishedrangereview retained. CIneedsalltriggerednewheadjobs; do notmergeold465head. No fullsuiteor gatechange.

Repair46minimum-versionfollowup: main43dminversions1failed/18578passed on existingDuckDBreadbacktest. CIinstalledDuckDB2.0.0, whichremovedrelation.fetchone(). Newb81ceb8replacesonlytheexistingconsumerwith.fetchall() andassertscomplete[(small_table.num_rows,)] result; exactcountpreserved, no newskip/deletedcase/production/gatechange. Local2.0failednode1red4.37s→wholefingerprintmodule43greenlocked1.5.6/native4.94s and43green2.0/Python5.98s, zeroskips. Ruff/format/wording/diffgreen. Independentexactb81approval/DCO/currentmain43dverified; priorsecurity/exportreviews retained. Same smallrepairPR46 addressesauditandminimum-versionfailures; currentheadCIrequired, old465/f52heads superseded. Prepared/tmp/public-a-merge46.jsonguardsb81/Signed-off-by/mergecommit, NOTsubmitted.

Read-onlyW9-06DuckDB2compatibilityreviewpassed: sink/source/nestedreadback paths useconnection.execute/fetchone/fetchall, sourceprefers.to_arrow_reader; removedrelation.fetchone notused inthesechangedpaths. No extraedits/testsneeded. Worktreeclean/released.

Verifiedsourcepluginregistrationcounts(root/pluginTOMLshape.*entrypoints+literalGROUPS/BUILTINS dedup, matchinghostfallback): currentmain43d=183; cloud37/nested38/constraints44=185 each; Oracle39=185withdifferententries; cloud+Oracleunion=187. CloudaddsDatabricks/SnowflakeSources; OracleaddsOracleSource+Sink. Allsharedtargetsidentical,no removedmainkeys. IMPORTANTfuturemerges: identical185docheaders mayauto-merge whilecombinedactualcountis187; recompute/updateDBTandauthoringfulltranscriptsfromactualmergedregistrations, do notblindlykeep185. Read-onlyutilityreviewedat/tmp/public-a-count-plugins.py (ASTliteral/tomllib,read-onlygitshow/ls-tree); mayneedregenerateafterenvreset. No repositoryhelperorproofarchiveadded.

Latestrepairb81CI:32successful,9running,0queued,0failed;43registeredcheckruns includes2untriggeredpublishskips, so41triggeredchecksrequired. Security/audit/lock-exportgreen. Remainingplatform/minversionsuitesstillgates. Main43dhasonlyknownaudit/build/security+DuckDBminversionsfailures beingrepaired; no furtherfailuretypeobserved. Normalfeaturemergesremainpauseduntilrepair46lands safely; fourbaseline-blockedheadsretainimplementationapprovalandneedfreshCIafterrepairmainrefresh.

Main43dCIcompleted:26green,4failed,0running. Failuresareexactlybuild/audit/security(all4tooladvisories) andminimumversions(removedDuckDB2relation.fetchone). Allothertriggeredmainchecks passed. ThisconfirmsPR46targetsallobservedmainfailureswithinonesmallPR; no unrelatedproductionfailurefound. Latestapprovedrepairb81CI34green/7running/0queued/0failed, plus2untriggeredpublishskips. Stillwait forall41triggeredchecks/workflowsoverallgreen andcurrentmainancestorbeforeAPImerge. No normalmergesyet.

## Repair46 Windows failure — unresolved, do not dismiss

Currentb81CI38green/2running/1failed asof18:17UTC. Windows3.11job121231082157 inrun40213846736 failed existing tests/regressions/test_issue_1_nested_profile.py::test_a_delta_read_that_fails_after_the_scan_started_exits_2_not_hangs: run2exit0 expected2;18525otherspassed,94.52%coverage. FailuremessagecontainsstderrRuntimeWarningonly; childstdout/artifactstate unavailable. Main43dWindows3.11passedthiscase, butthatdoesNOTprovefailurepre-existing/unrelated. No newskip/weakenedassertion/speculativeproductionfix made.

Separateagentdelta_exit_ci_review tracedfullmaterialization(shape.api.profile→reference.read_delta→deltafs.local_table→DeltaTable.to_pyarrow_table nativeFS), no sample/cache bypass. CLIerrorsmap2,closedpipe141,lifecycle forwardscode; no knowncontrol-flowpath explains0. Unchangedtargetedtest6children passedLinux/native7.48s; outside-repoprobe30physical/30activefilesconfirmedcorruptselectedfileactiveandbytesunchanged;12additionalchildrenall2. No definitiveWindowscause or safecodefix found. This is18correctlocalruns, NOTproof Windowsflake/pre-existingcause.

OneexactfailedWindowsjobrerunwithdebugloggingisplannedasdiagnostic, notrerununtilgreen. POSTjobrerunreturned403 becauseworkflowstillrunning (explicitoperationalreason, notcredentialfailure); waitforrun40213846736completed thenrequestjob121231082157rerun ONCE. As of18:17workflowinprogressattempt1,twootherjobsstillrunning. No dispatchofotherownedworkflows orfullmatrix. Keepallassertions/timeoutsunchanged. Reviewer_oct25 isassessingnewrisk; priorcodeapprovaldoesnoteraseCI failure. Do notmergerepairunlessallrequiredlatestexact-headCIgreen ANDreviewapprovalremainsappropriate. Ifno safeonesmallPRrepairispossible,userhardstoprequiresrevertour45merge throughPRthenstop; no protectedpush/gatebypass. Normalfeaturemergesstillpaused.

Repair46workflowattempt1completed18:21UTC:40green/1failed/0running (plus2untriggeredpublishskips). OnlyfailedWindows3.11corruptDeltareadcase remains. Exactjob121231082157rerunwithdebuglogging REQUESTEDONCE successfullyaftercompletion; checkrun40213846736attempt2, donotreissue request. Rejectedearlier403requestdidnotstartarerun. Reviewer_oct25 keepsscopedb81codeapprovalbutblocksmergeuntiltheunchangedfullWindowsjob(includingall6corruptreadattempts) andallotherrequiredchecksaregreen. A pass doesnotprovecause/pre-existingstatus: preservefirstfailurelink+unknowncause inrecord/finalrisk. Repeatedsamefailure→no more retries/no speculativefix; seeksmallcausalfixor userhardstoprevert-and-stop path.

## Prepared integration after repair46

Read-only merge prediction: repair b81 into each of37/38/39/44 conflicts only in CHANGELOG; preserve both entries, no code conflicts predicted. Do not refresh until repair actually reaches main. Cloud37+Oracle39 combined merge has three conflict paths: CHANGELOG, plugins/shape-databases/pyproject.toml, src/shape/bridge/handlers/flow.py. Preserve one shape.sources table containing databricks/mysql/oracle/postgres/postgresql/snowflake, Oracle sink/extra, and union safe-capture URI schemes for cloud plus Oracle/TCPS. Preserve both new changelog entries and existing Eventhouse entry once. Docs listings auto-merge187 unique entries but both DBT/authoring headers remain185: update187, refresh editable metadata before transcript capture. Conformance auto-merges expected12 plugin entries. Code conflicts require targeted cloud/Oracle/conformance/bridge and two affected docs transcript nodes, plus static checks; no full local suite.

Read-only Delta07 preparation: sql_constraints.py safe-AST helper is already byte-identical between tests checkpoint897f626 and final44head8243809. After actual44main, integrate final shared wiring rather than copying AST again. Preserve44 SQL policy vs Delta constraints bool/mapping namespace split, --contract parsing/constraint_contract/target_options and optional-sink-safe preview. The07 protocol preview currently omits contract-derived CHECK options; six of eight red cases address this, remaining two require44 parser. No premature07 integration or implementation edits made.

Delta07 followup from read-only preparation: CLI parse_sink_config retains raw noninteger strings. Final44 namespace split treats any constraints string as SQL policy, but07 Features.parse accepts true/false/JSON-mapping strings as Delta feature configuration. During07 integration after actual44main, recognize only actual SQL policy names and preserve raw Delta strings/mappings; add targeted CLI true/false/structured constraints regression. Protocol preview must consume same derived per-table options as execution and union protocol versions/features. Preserve prestorage validation, named AST refusals, native mapped-NTZ refusal, appendOnly-before-user-rows ordering and default protocol; do not imply cross-table Delta atomicity.

## Repair46 merged; feature queue resuming

PR https://github.com/sqllocks/shape/pull/46 MERGED via guarded PRmergeAPI as c1ff1c8adec2225a7e458c50c5cf7c70625b17b1, exacttested/reviewedhead b81ceb8c6a1f77bb817b177748d21dbd2b6438cf. All41requiredchecks and6workflowsgreen. Windows diagnostic rerun job121252062691 passed: core18526passed (existing63platformskips,92deselected), heavy51passed, bothkernels449passed each(existing1platformskip each), bothdrifts7passed each. No tests/skips/assertions/gates altered byrepair. FirstWindowsfailurejob121231082157 remains unexplained; rerunpassdoesnotprovepre-existingcause. Preserve https://github.com/sqllocks/shape/actions/runs/40213846736/job/121231082157 asmaterialrisk. Exactlyone diagnosticrerunaccepted; no retryuntilgreen.

Greenexact-headworkflowlinks:
- CI https://github.com/sqllocks/shape/actions/runs/40213846736 (attempt2)
- DCO https://github.com/sqllocks/shape/actions/runs/40213846872
- Container https://github.com/sqllocks/shape/actions/runs/40213846631
- Security https://github.com/sqllocks/shape/actions/runs/40213846712
- Wheels https://github.com/sqllocks/shape/actions/runs/40213846671
- Documentation https://github.com/sqllocks/shape/actions/runs/40213846756

Checked ContractLead reports/contract-application/2026-10-25/questions.md after46mergebatch inworktree+neworigin/main: absent. Refresh37/38/39/44 once toactualrepairmain; no localtests unlesscodeconflicts. Current37head4b13dbc679eb61ae1305c9d161ea86516c1595d2 signed/pushed, onlyCHANGELOGappendconflict; current39head4496683c4aca24d8da2e0ddd57970eb8b5bc0c38 signed/pushed, onlyCHANGELOGappendconflict/lockauto-merge; publishedrangesandrepairpinsretained. Needindependentrefreshreviews/currentheadCI. MainrepairCInowall40triggeredchecksgreen; ordinaryeligiblefeaturemergesmayresumeonceexactheadCIgreen. Sevenissuecountunchanged1merged,5active,1parked; maintenance46isoutsideseven.

Allfourrepairrefreshespushed/released:37 4b13dbc,38 0cdb108,39 4496683,44 07b3616. OnlyCHANGELOGappendconflicts; repairpins/export/fingerprintreadbackpreserved; Oraclelockauto-mergesnewextra. No codeconflicts/localtestreruns. Reviewer_oct25nowownsindependentread-onlyrefreshreview. NewmainandfeatureCIrunning.

Reviewer_oct25 independently APPROVED allfourcurrentpublishedrepairrefreshheads:37 4b13dbc,38 0cdb108,39 4496683,44 07b3616. VerifiedPRheads/currentbaseancestry/intendedparents/DCO, onlyCHANGELOGmarkerremovalpreservingbothsides, entirepublishedmetadataunchanged, exactlytwoapprovedlockedtoolversionchanges, generatedexport/fingerprintreadbackretained. Oraclemanifestextraoracledb>=3.0/locked26.0.1retained. CIpendingexactheads. Deltaagentnested_duckdb2_reviewnowowns07tests-onlyCLIregressioncheckpoint; no44helper/sourceintegrationuntilactual44main.

Delta07tests-onlycheckpoint06cb5d44fc10590a7f9825e5ec59f977dd104a3d signed/pushed/released. New CLI constraints string acceptance3red/3controls pass onunchangedproduction viareap.py/lockeduv,1.13s; true/false/JSONmapping eachmisclassifiedSQLpolicyatgenerationbinding. ActualCLIpreviewcontrolsassertexactprotocol; no-storage/nativewriter guards andnamedconstraintschecks retained. Existing8red/2derivedprotocolcasesunchanged. Ruff/format/publicwordingpass. No source/helperintegration/PRyet; stillwait44actualmain. Rootreadtestsandlanerecord; finalindependentreviewaftergreenintegration.

## Main repair verified on merged head

Actualmainc1ff1c8 freshCI isfullyGREEN:40triggeredchecks, bothkernelmodesincluded, all4triggeredworkflowsoverallgreen; nofailures/runningjobs. Thisclearsknownmainredafter45. FirstrepairWindowsfailureunknownriskstillpreserved; freshmainWindows3.11alsopassedunchanged. Greenmerged-headruns:
- Security https://github.com/sqllocks/shape/actions/runs/40226657845
- Wheels https://github.com/sqllocks/shape/actions/runs/40226657792
- Documentation https://github.com/sqllocks/shape/actions/runs/40226657904
- CI https://github.com/sqllocks/shape/actions/runs/40226657799

## Oracle W9-15 merged

PR https://github.com/sqllocks/shape/pull/39 ; merge3b19d05b940816a74a0ce1ec177365138ce0ed32 ; exactapprovedtestedhead4496683c4aca24d8da2e0ddd57970eb8b5bc0c38 ; all32triggeredchecks+6workflowsoverallgreen. GuardedPRmergeAPI mergecommit/Signed-off-by used. Actualissue16closed/completed. Targetedred/greenretainedinW9-15lane. Greenexact-headruns:
- DCO https://github.com/sqllocks/shape/actions/runs/40226721600
- action-selftest https://github.com/sqllocks/shape/actions/runs/40226721595
- Documentation https://github.com/sqllocks/shape/actions/runs/40226721586
- Security https://github.com/sqllocks/shape/actions/runs/40226721653
- Container https://github.com/sqllocks/shape/actions/runs/40226721599
- CI https://github.com/sqllocks/shape/actions/runs/40226721627

AftermergebatchcheckedContractLeadquestionspathinneworigin/main:absent. RootmergedactualOraclemainONCE into37/38/44; allhadflow.py/pluginmanifest/CHANGELOGconflicts;44alsoappend-onlyrequirementsYAML. ResolvedURIunioncloud+Oracle/TCPSsafeonly, one6entrysources table+OracleSink/extra; sourcecount187 andbothdocheaders187. Allmain/lanechangelogentriespreservedwithduplicateEventhousedeclarationremovedonce;44bothrequirementsblocksretainowner:databases. No frozen/gatechanges. Mergesstagednotyetcommitted. Agentsnowowntargetedtests/static/docs andsignedpush:oracle_ci_oct25→37, nested_duckdb2_review→38, constraints_refresh_oct25→44. No concurrentrooteditsinthoseworktrees. ReviewexactnewheadsthenCI; canceloldownsupersededunfinishedrunsonlyafternewpush.

## Current Oracle integration refreshes complete

37 ef2ec455079081c09f5479ece25d50923240440e;38 821f65e2e25e83b8d9cec2c9b3f6cd92694f9e48;44 ba67c2ba6d4fcd8a8de01505326b400a5aba33a2. Allsigned/pushed/clean/released; intendedoldhead+actualmain3b19parents; independentlyAPPROVEDexactremotes/DCO/ancestry/manualresolutionandautoOracle_sqlhelperintegration. No additionalimplementation/test/gateedits. Each195targeteddefault+195Python(cloudsources,Oraclesource+sink,conformance,bridgeflow)green;bothDBT/authoringdocspagesgreenonce(harnessforcesPython);Ruff/format/mypyflow/wording/conformance32/32green. Metadatafresh187registrations/187docrows/6sources/OracleSink+extra/repairpins; publishedrangesunchanged.38DBTfirstinvocationomittedpersistedRustenv; correctedcommandandreranonlyfailingnode49.85sgreen, authoringfirstpass. Oldown38CIrun40226747394canceledafternewpush; old37/44alreadycompleted, no currentrunscanceled. NewCIisgate; no localfullsuite. Rootownsallreleasedworktrees;07waitsactual44main.

## Oracle merged-head CI verified

Actualmain3b19d05b940816a74a0ce1ec177365138ce0ed32 freshCIall30triggeredchecksgreen, nofailure/runningjobs, bothkernelmodesandWindows3.11included. Alltriggeredworkflowsoverallgreen. This ismerged-headevidenceforPR39, separatefromitsapproved32-greenPRhead. Remaining37/38/44stillneedtheirownexactheadCIbeforemerge. Greenmerged-headruns:
- Security https://github.com/sqllocks/shape/actions/runs/40235435418
- Documentation https://github.com/sqllocks/shape/actions/runs/40235435374
- CI https://github.com/sqllocks/shape/actions/runs/40235435384

## DBT main90 refresh complete

LeadBmergeddbtmain90fbff999bc95e255b3dbd90b76278441a735b41. Refreshedremaining37/38/44 ONCE andpushedsigned:37 fed6366d9222df91e7a2a50695e63ed667c697e3;38 38cac0e2e1d729d3dbce81b34bfdb80879f1e05b;44 f10fe3bee76a621289c79638b699a2d89a2939d5. 38/44mergesZEROconflicts.37onlyDOCconflictsCHANGELOG(preserveEventhouse+DBT) andCOMPLETION_PLAN(keepalreadylandedcanonicalOwnerrowunchanged); additionaloneblankEOFtrimmanifestTOMLsemanticsverifiedidentical. No runtime/dependencyconflictsorchangesfromresolution. No localtests/sync/rebuildperOwnercleanrefreshrule. MainnewDBTcontentretained,187sources/docrowsstillcorrect,all95mainrequirementblocksidentical; uniqueIDs37=96/38=97/44=98. Repairpins/ranges/Oracleextraretained. Reviewer_oct25 independentlyAPPROVEDall3exactpublishedheads/parents/currentbase/DCO/cleanworktrees; noreviewerruntime reruns. NewexactCIrequired. Onlysupersededown37unfinishedCIcanceledafternewpush; otheroldrunsalreadycompleted.

Whitespaceverification:37PR-scopegitdiffcheck0.38/44exit2solelyexistingextraEOFblankinpluginmanifestfrompriorOracleintegration; importedmainDBTtimestamptrailing-spacespreserved. No claimgreenfor38/44whitespacecheck; no requiredCIgatechanged.37trimsEOFsemanticallyunchangedanditsactualmainmergewillpropagatetrimthroughnormalrefresh.

## Throughput scheduling — dependency-blocked CI deferred

Observedhostedjob-startdelay50–53minutesinpriorcloudmatrix; current37wasqueuedwhile38/44consumedrunners. Allremainingactiveissuesformactualdependencychain:37→38→44→07; no independenteligiblelaneisbeingserialized. Current38/44headsMUSTmergefreshactualmainafterupstreamPRsland, sotheircurrentfullsuitescannotserveaseventualexact-head/current-base mergeevidence. Afterverifyingexact38cac/f10headsandunmergeddependencies,canceledonlytheirunfinishedownworkflows. KeepPRsready/reviewed, statusCI-PAUSEDnotgreen; no park/skip/assertion/workflow/gatechange. Preserve37andmain90runs,andallotherownership. IndependentreviewerassessedthisiswithinOwnerthroughputauthorizationandpreservesordering,parallelworkandallgates; no permissionflow.

Afteractual37mainmerge, refresh38and44ONCE/pushsignedasrequired. RunalltriggeredCIon38nexteligiblehead; pause44newheadCIagainuntilactual38mainmerge. After38main, refresh44/runalltriggeredCIasusual. Anynewindependenteligiblelanerunsparallel. Nevermergedeferred/canceledheadwithoutallrequiredexact-headgreenchecks. No rawCIarchives/receiptsadded. Nextactionmonitor37/main, promptlydiagnosefailuresandmerge37whenreviewed/current/allgreen.

## DBT merged main verified green — 2026-10-26

Actualcurrentmain90fbff999bc95e255b3dbd90b76278441a735b41: all30triggeredchecksgreen, alltriggeredworkflowsoverallgreen, bothkernelmodesincluded. RootdidnoteditormergeLeadBbranch; thisiscurrentbasehealthverification.37stillneedsremainingexactheadjobs;38/44remainCIdependency-paused(notgreen). Greenbaseworkflows:
- Security https://github.com/sqllocks/shape/actions/runs/40243508316
- Documentation https://github.com/sqllocks/shape/actions/runs/40243508311
- CI https://github.com/sqllocks/shape/actions/runs/40243508314

## Cloud W9-02 merged — 2026-10-26

PR https://github.com/sqllocks/shape/pull/37 ; merge314749d37aba9141a2ff68a33cfdb91e8a87cca5 ; exactreviewedtestedheadfed6366d9222df91e7a2a50695e63ed667c697e3 ; all32requiredchecks+6workflowsoverallgreen. GuardedPRmergeAPImergecommit/Signed-off-by used; actualissue3closed. Targetedred/greeninW9-02lane. Greenexact-headruns:
- Container https://github.com/sqllocks/shape/actions/runs/40243748808
- DCO https://github.com/sqllocks/shape/actions/runs/40243748750
- Security https://github.com/sqllocks/shape/actions/runs/40243748751
- action-selftest https://github.com/sqllocks/shape/actions/runs/40243748743
- Documentation https://github.com/sqllocks/shape/actions/runs/40243748741
- CI https://github.com/sqllocks/shape/actions/runs/40243748742

After37mergebatchcheckedContractLeadquestionspathinnewmain:absent.38mainrefreshstaged; keepsnestedCHANGELOGentries, choosesmainmanifestEOFtrimafterTOMLsemanticidentityverification. No runtimeconflicts.44single314refreshsigned/pushed6d9f0dd,187registrations/all98requirements/mainblocksretained; pendingCIpauseverificationbyconstraints_refresh_oct25.

## Owner acceleration directive — 2026-10-26

Ownerexplicitlyorderedwhateverstepsneededtospeedthemulti-dayrun. Broadenleadmaintenance scope toCIperformancewhilepreservingalltests/assertions/jobs/coverage86/kernels/gates/no-skip/no-settings/no-selfhostedrules. RootacknowledgedavoidabledependencyCIrunnerwasteandpausedit. MeasuredcompletedWindows3.11job97.87min: coveredcore75.53min,setup4.43,heavy7.27,kernels8.20,drift1.12. CIsetupoverall59.6runner-min; testjobsstarted9.6–36.7minaftercreation.

Newleadworktree /workspace/public-a-ci-speed branchlead/public-a-ci-speed-2026-10-26 atactualmain314, ownedagentdelta_exit_ci_review. Patch3files: internaldevpytest-xdist==3.8.0+uvlock(applicabilityextends3.11,published[project]unchanged); conditional-n2--dist=loadscopeONLYnonLinuxcore(existingrealtimefilterunchanged); existingSwatinem/rust-cache@v2 before nativeuvsync(nonbenchmark), releasekeynative-ci-release/workspacesrust/shape-kernel, existingdebugcacheuntouched. Allselectors/ignores/coverage86/heavy/kernels/drift/minversions/Linuxserialunchanged. Targetedparallelsecurity/regex/joint/plugin/bridgevalidationunderwayviareapusingprepared38env; no localfullsuite. ResidualunmarkedtimingbudgetsremainunchangedandfullCIwillvalidate. Reviewer_oct25 independentlyreviewingcache/parallelism/scope. Agentcommitwillbesigned/pushedwithoutnewPR,thenreviewedpatchdeltascopiedinto38stagedmergebeforefirstpush, avoidingaseparatefullCIcycle; updatePR38descriptionwithOwnermaintenance scope. BPR31CIeditisonlyminversionicebergextrasline, disjoint; do nottouchBbranch. Ifbasemovesagainapplypatchdeltasandpreserveallnewmainmetadata, notwholefilecopies.

## CI acceleration delivered to next eligible head

Leadpatch1cd2fd4abff3810601c4835cd2ea5fc49d06e932 signed/pushedwithoutseparatePR. CI/dependencyfilescopiedbyte-exactintothenecessary38refresh; final3aa357150c35381fbb0e2972896a99da7441ca20 signed/pushed,parents38cac+actualmain314. PR38title/bodyrewrittenforthenativenestedfeature+OwnerCIthroughputscopeandmerged45/37dependencies. RootresolvedonlynestedCHANGELOGretention/manifestEOFformat;TOMLsemanticsunchanged. Allsrc/plugins/testsproduction/assertiontreesunchangedfromapproved38parentexceptmanifestwhitespace. NewCIUnreleasedentryadded.

Targetedproof:428paralleltests40.76s(representativesecurity/unmarkedtiming/regex/joint/cloud/Oracle/bridge),zero newskips;2loadscopeworkermodules+Pythonchildcoverageprobe2pass/100%withunchanged--cov-fail-under86;uv-environment4testsviareap,lock/docs export/wording/diffgreen. RootpostcompositionDeltaexit+jointprofileparallelbatch74pass72.88s,5existingforkdeprecationwarnings;lock/export/wording/diffgreen. No fullsuite locally. Agentusedprepared38Python3.12envtruthfully, no native6mrebuild;actualWindows/macOSlatency/isolationstillCIgate, no speedupmeasurementclaimed.

Reviewer_oct25 independentlyAPPROVEDleadpatchandfinalexact3aa3571:alljobs/matrices/selectors/ignores/coverage86/env/oldstepstructuresidenticalafteronlycache/n2additions;published[project]unchanged;alllockedpackageversionsunchanged,onlydevxdist3.8+applicabilityextension3.11. ReleaseCargo keynative-ci-release,separatefromdebug;standardCargo/branchcacheinvalidation;benchquick/minversions/Linuxserial/heavy/kernels/driftuntouched. Finalremote/head/parents/DCO/clean/compositionverified. RequireALLtriggerednewheadCI(includingautomaticWheels)greenbeforemerge.

44actual314refresh6d9f0ddsigned/pushed/released,all6workflowsnowterminal:DCOsuccess/fivecanceled,zero running. CIisPAUSEDnotgreen, awaitingactual38main;no CIpatchcopiedearly. Source07stilltests-only06cb5d4awaits44actualmain. Currentmaintains3merged/3active/1parked.

## Owner batching override — 2026-10-26

The user explicitly directed all work onto one PR, fix everything, and run CI once at the end. Root accepted this override of the original one-issue-per-PR and per-issue refresh/CI sequence. Internal dependency code order remains; the final merge contains all three issues together. #4 still requires its separate frozen-policy ruling; the user asked for its explanation but has not approved an amendment yet.

Root created `lead/public-a-integration-2026-10-26` at3aa3571, reusing the existing `/workspace/public-a-W9-06` prepared editable environment by switching branches. Verified shape and database imports resolve to that worktree. Native Rust source tree is unchanged58701a06... compared with the #8 checkpoint, avoiding an unnecessary rebuild. Old PR38 CI/Wheels were canceled as superseded. Old PR38/44 were closed through the API with a batch-branch link; issues7/5/8 were verified OPEN. Foundation07c1cf1 had zero workflow runs. Preserve current main and other ownership's workflows.

Foundation07c1cf1b66a6af8d6f543d7c50ea66145fc19e8c merges final #5 head6d9f0dd into #7 plus CI speedup3aa3571. Only changelog conflicts required resolution. ReviewerOct25 independently approved the immutable foundation: approved implementations, safe AST, transaction safeguards, native mappings and CI patch remain intact; no frozen #4 artifacts are included.

Feature foundation38e85b3 merges #8 checkpoint06cb. Docs/FABRIC_PLATFORM/generation conflicts retain final #5 contract and target wiring. Integration acceptance reproduced11red/5controls. New target/profile/real-write boundary tests showed6red/1control before fixes, then the focused40-case selection passed. Default byte compatibility81cases passed across both kernels. Current static results: Ruff/format74changed Python files, strict mypy37source modules, wording, conformance32/32, stable/plugin API zero breaking changes, golden418 match, plugin count187. Additional writer/destination49passed/2emulator cases deselected per kernel. Main affected selections and legacy-none corpus proof continue. No local full suite or intermediate CI.

Latest published batch checkpoint: c38fed14d682cfb4a0a07fddf4ee301697ffd194. The writer will report the final immutable head and all targeted results. Root then obtains final independent Wanted7/5/8/security/gate review, opens one ready PR, and uses one full CI round on the finished batch. Actual CI defects, if any, must still be fixed without weakening gates.

## Owner ruling resolves final #4 policy blocker — 2026-10-26

The Owner explicitly chose VERSION-SPECIFIC RENDERING. Do NOT amend frozen outputs. Bridge1.1 and1.2 keep historical DDL, including the five guessed widths: dim_date.month_name/day_of_week_name NVARCHAR(255); dw.city.city/city_city.city/city_city.country VARCHAR(255). Preserve every frozen field, exact assertion, fixture and baseline. CLI/PythonAPI and new Bridge minor1.3 use corrected NVARCHAR(MAX)/TEXT. Keep existing negotiation rules, including1.0 compatibility. Indexed-string width refusal is unchanged on EVERY version; legacy presentation cannot bypass canonical validation or fabricate max_length before validation. Stable-API changes additive only. New1.3/CLI/API positive/negative/boundary tests, docs/BRIDGE.md1.3 and Unreleased entry required. User confirmed inclusion of ALL remaining issues in ONE final PR. This explicitly authorizes the new BRIDGE minor, not a package version bump/tag/release.

#4 source63ab15c merged into batch foundationb3b321e35727a9e90e1881dffd4af3ceff246b92 after c38fed1. Final5AST/coordinator/output/Delta/CIperf files retained byte-exact. Historical docs/bridge/vectors/design.json and tests/bridge/data_1_1.py restored byte-identical c38, no frozen mutation. Type tests213passed each kernel. A real offline DatabaseSink.ddl Plan omission lost constraint/nested options: new3red then fixed to retain options, validators unchanged. Combined authorized nonfrozen generation corpus390files+28namedrefusals: type-only none policy byte-identical approved4; adding5 permits only26FK+12GO in26files across7approved specs because two old FK outputs are now approved indexed refusals. Values/nonSQL/file/refusal sets remain exact. These previously authorized nonfrozen corpus manifest updates do not alter frozen Bridge baselines.

Builder ownership: Nested owns batch shared type/writer/generation integration and test-input corrections. Oracle owns `lead/public-a-bridge13-2026-10-26` in reused `/workspace/public-a-W9-15`, based on b3b321e; native Rust source tree unchanged58701a... and imports verified that worktree. Oracle exclusively owns src/shape/builtins/sinks/sql.py postvalidation presentation hook, src/shape/design/ddl.py private wrapper, bridge protocol/handler/new helper/schema/vector/tests/BRIDGE docs until handoff. Nested does NOT edit these reserved files. Other writer fixes remain disjoint. Root merges reviewed Bridge branch back into batch after both finish.

Bridge progress: new five-column acceptance2red→2green; expanded67new1.3/CLI/API/negative/boundary/refusal-matrix cases; default relevant695tests green including all frozen1.0/1.1/1.2 replay. Historical root/current vectors and1.0/1.1 data retained; new1.3 resources/current alias additive. Generic CURRENT-SERVER version literals change exactly1.2→1.3 as required by the authorized minor; old nonfrozen108 CLI-parity cases now request explicit1.3 with exact assertions preserved and strict named refusals for invalid TSQL/Fabric keys. Frozen DDL/full-field assertions remain unchanged. Registry-diff1.2 uses existing published additive-field matching where c38 already returned class/class_reason; no production projection or fixture change to hide it. Python-mode/connection-guard/final audit continuing.

Unblocked3-issue batch c38fed14d682cfb4a0a07fddf4ee301697ffd194 was fully green before4: red11/5controls +extra6red/1control →40integrationgreen;1040affected tests per kernel,49additional per kernel,81bytecompat cases,418default+418legacy-none corpus both kernels, static/API/conformance green. Now complete4+Bridge1.3 and rerun only necessary affected checks before ONE final PR/CI. All four open issues will close only through that final merged PR.


## Final batch composition and review checkpoint

Batch471f0e8 includes runtimef16ab28 and Bridge57540d via clean signed merge18fb173.
All four issues remain active in ONE final PR; historicalPR41 is now closed
superseded with issue4 still open. Formal independent Delta product review
APPROVED18fb173. Shared type/Bridge/DDL composition226passed EACHkernel.
FrozenGitblob audit:1.0schema53/vectors29 and1.1schema75/vectors54 unchanged;
newarchived1.2schema99/vectors108 byte-identical actualpublishedmain314.
Ownerbatch+Bridge1.3 decisionrows now in batchCOMPLETION_PLAN.

Independent review caught TSQL/Fabric UNIQUE-string width validation missing
(the PK path already rejects); nested owns separate public-a-unique-width
worktree, tests-first patch preserving nullable semantics and approved28refusals.
MySQL existing TEXTindexed behavior separately diagnosed; no extra corpus
refusals approved or introduced. Reviewer continues type/Bridge security audit.

Freshmain314 macOS3.11 CI job121346549307 failed existing cancellation regression
(expectedcancelled, got succeeded;18568pass). Real cross-JobStore lost-update
race confirmed; PR37/batch do not change that code. Oracle owns isolated minimal
repair with deterministic red/green; fold into SAME final batch under Owner
fixeverythingonePR/speed scope, no second PR/CI cycle. No rerun-to-hide failure.
Final exact-headCI remains mandatory beforemerge.


## SINGLE FINAL PR OPEN — #49

https://github.com/sqllocks/shape/pull/49 is READY, exacthead
23c90c0f6fff5f6ed3d418b9a2986677ecde83f3, basedactualmain314749d.
IncludesALLfourremainingissues4/5/7/8 plusOwnerCIacceleration andminimal
pre-existingmaincancellationracerepair. Closesallfouronlywhenmerged.
NootherimplementationPR remains active. PR49attachedtochat.

UNIQUEfix6e8fac8 mergedwithonlycorecanonicalkeyargumentconflictresolved
toPKunionUNIQUE; fullBridgepostvalidationhookretained. ReviewerOct25
APPROVEDfinalexact23c for4/5/7+Bridge/CI/security; DeltareviewerAPPROVED
8productandjobs6037repair. All118newcommitsDCOchecked.
PostconflicttargetedBridge/frozen/UNIQUE/constraintselection280passed
EACHkernel (11.90sdefault,16.26sPython). Ruff/format/mypy3shared
modules/wording/diffgreen. Broader43changedmodulesstrictmypy passed;
114changedPythonRuff/format passedbeforelast3modulepatch.
Golden390files+28refusalsgreen, noextraMySQLrefusals.

FullCIstartedONLYnowonthefinishedbatch. Exactheadruns:
- CI https://github.com/sqllocks/shape/actions/runs/40259512617
- Wheels https://github.com/sqllocks/shape/actions/runs/40259512606
- Security https://github.com/sqllocks/shape/actions/runs/40259512623
- Container https://github.com/sqllocks/shape/actions/runs/40259512521
- Documentation https://github.com/sqllocks/shape/actions/runs/40259512533
- DCO https://github.com/sqllocks/shape/actions/runs/40259512500
- action-selftest https://github.com/sqllocks/shape/actions/runs/40259512531

ThesearePENDING,notgreenevidence. Pollalltriggeredchecks includingWheels
andbothkernels;diagnoseanyfailuretargetedwithoutweakeninggates. Keep
frequentuserupdates; noendingtowait. Onceallgreen/current/reviewed,merge
promptlyviaAPImergecommitwithexactSHAguardandsignedmessage. Check
ContractLeadquestionspathaftermerge. RecordCIgreenURLs/mergedSHAinfinal
reportonthisscratchbranch,withoutanotherbookkeepingPR/CIcycle.


## PR49 corrected exact head — abdcdf1

FirstcompletedbatchCI found one missed current-server docs transcript version:
BRIDGE runnable example expects1.2, serverreturns1.3.165otherdocstestspass.
Rootreproducedtargetedred1/2.76s, correctedonlyfour responseversionfields
in2runnableblocks(nohistorical/frozen/resultbody/request/assertionchange),
then targetedgreen1/3.81s. Signed/pushed
abdcdf1f0833cc2dc2d871f655367478bbbb09bd, parent23c; ReviewerOct25
extendedexactheadAPPROVAL. NewcurrentheadCIrequired; old23cresults
notmergeevidence. CorrectedimmediatelytoavoidwaitingobsoleteheadCI.
Currentruns: CI40260449004, Wheels40260449160, Security40260449076,
Container40260449310, Documentation40260448973, DCO40260449020,
action-selftest40260449057. AllPENDINGinitially. SamePR49/allfourissues.
Pollscript /tmp/public-a-poll-final.py updatedtonewSHA.


## PR49 test-fixture correction — 5941963

Exactcurrenthead5941963d8adc7d5adc90be51800823389b3b9248, parentabdc.
MacOS3.14 corefinished19004pass+1failure: testtracebackcontaineddummykey
fromitsinlinecallsource, becausePython3.14rendersthewholemultilinecall.
Productionalreadyraises sanitizedValueErrorfromNone; noactualcredential
leakwasobserved. Minimaltest-onlyfixtureassignskeybeforecall/passesvariable;
ALL3originalordering/privacyassertionsandfulltracebackformattingunchanged.
Addedpostcatchassertprovesfakewriterreceivedactualdummykey.93Deltafeature
casespassEACHkernelonprepared3.12;no local3.14verificationclaimed.
Ruff/format/wordinggreen. ReviewerOct25APPROVEDexact5941963+DCO.
Signed/pushedcurrentbranch; samePR49/all4issues, no secondPR.
NewfullCIrunsonthecorrectedexacthead; oldheadresultsnotmergeevidence:
CI40262750522,Documentation40262750397,action-selftest40262750353,
Container40262750433,Security40262750425,Wheels40262750438,DCO40262750448.
InitiallyPENDINGexceptDCOgreen. PollscriptupdatednewSHA.


## PR49 portable URI correction and early acceptance — 7c2091c

Exactcurrenthead7c2091cc7612b742b24ebf5403a054355902b13a, parent
b991043cf1eb00ca5be59ca1cba607fef961ef68 (parent5941963). Windows3.14
CIhad2failure-injectionfixturesDIDNOTRAISE/18960pass becauseURIpath
wascomparedtonativeWindowspath. CanonicalPath.resolve().as_uri()comparison
nowmatchestargetexactly; originalprivacyassertionsremain. Addedspaces/#/%
pathcasesreproduced2red/2plaincontrols locally, then95Deltafeaturecases
passedEACHkernel. Addedexact-oncetargetinjectionassert. No productionchange
orlocalWindowspassclaimed. ReviewerWindowsdiffscanfoundnootherconcrete
pathcomparisonfault. Prior3.14literal-tracebackfailurepassedintheWindowsrun.

CIfront-loads5corewriteacceptancemodulesbeforeunchangedfullcorematrix;
matchinglocalselection251passed7.63s. Sixadditiveworkflowlinesonly;
independentbytecomparisonprovesallprevioussteps/selectors/coverage86/
matrices/heavy/kernels/driftunchanged. Wording/diffgreen. ReviewerOct25
APPROVEDexact7c2091c,remote/DCOverified. NewexactheadCIrequired.
Currentruns: CI40265587355,Wheels40265587311,Security40265587323,
Container40265587370,Documentation40265587636,DCO40265587315,
action-selftest40265587405. InitiallypendingexceptDCOgreen.
Pollandguardedsingle-PRmergescriptsupdatedto7c2091c:
/tmp/public-a-poll-final.py and /tmp/public-a-merge-final.py.
Invoke merge script ONLY once allrequiredjobs/workflowssuccess; itchecks
exacthead/currentmainancestryandusesmergecommitAPI+SHAguard+DCO.


## Exact-head cross-platform acceptance is green

On PR49 exact7c2091c, the new existing-matrix acceptance step passed ALL8
combinations: Linux3.11/3.12/3.13/3.14, macOS3.11/3.14, Windows3.11/3.14.
This verifies portable URI injection, privacy assertions, SQL types/UNIQUE,
Bridge1.3 and constraint acceptance across platforms. No failed check currently.
FullCI/remainingchecks stillRUNNING; do notmergeuntilallrequiredgreen.
Samecurrentrunsandguardedscriptsabove; no furtherbranchchangesarepending.


## ONE refreshed base after Public B completed its batch — 57da256

Actualmainmoved682759841dcffd56dff62f74dc90ac18286cc600 while7cCI
was41/42green. Read-onlyfetchconfirmedmergeconflict; no oldheadresultis
mergeevidence. Signed/pushedrefresh57da2566365c440b36b65d33f028c187443ef176
parents7c+682. Jobs.pyexactmainretainsBsecurityACL/advisorylockfix andour
separatedeterministicregression. Bothdocsectionsretained;108requirements
exactA/Bunion;191registrations. Allnightly/benchmarkcontentexactmain.
Fournew1.2archiveJSONscopiedexactlatestpublishedmain;all207archive
blobsnowmatch682,all211existing1.0/1.1frozenblobsunchanged. Root1.3
schemasretainBfieldswithonlyserverversionmetadatachanged. No broad
regenerationoroldfixture/assertionchanges. OracleexactarchiveAPPROVED.

Targetedjobs+Bridgeallreplay/1.3/Iceberg/storedprofiles/docs1.2:
355passedEACHkernel(19.96sdefault,30.71sPython). No localfullsuite.
Ruff/format/mypyjobs,wording,stable/pluginAPI0breaking,conformance33/33,
uvlockcheckgreen. uvsyncactual1.69spreparationreusedcachednativebuild
(Rusttreeunchanged). ReviewerOct25APPROVEDexact57da,remote/DCO/
mainancestry/options/108requirementblocks/CIgateequivalenceverified.
RawPRdiffwhitespaceincludespreservedarchivedCRLF/EOF;donotalterfrozen
bytesorclaimrawwhitespacecheckclean.

CURRENTexactheadruns: CI40273519563,Wheels40273519573,Security40273519553,
Container40273519543,Documentation40273519574,DCO40273519595,
action-selftest40273519568. AllPENDINGinitially. SamePR49/all4issues.
/tmp/public-a-poll-final.py and /tmp/public-a-merge-final.py BOTHupdated
to57da. Guardedmergescriptchecks42+success/all7workflowsoverallgreen/
currentmainancestry+baseSHA,thenPRmergeAPImergecommitexactSHAguard+DCO.
DO NOT invoke untilallgreen. Continueupdates/poll,nomorecodechanges
withoutactualfailureoranotherbasemove. Finalreportafterall7closed.


## BigQuery billing guard integration fixed — 601158e

57CIWindowsdatabase-pluginsfailed1/1257pass: URImax_bytes_billed64
didnotrefuseestimate65. PairedLinuxjobauto-canceled;neitherisgreen.
Cause:A_base.plan copiesoptionsfornestedvalidation; BBigQueryplan mutated
itsoldlocaloptsafterbaseplanning. Minimalfixfresh-mergesvalidatedURIcap
intoactualplan.options,preservingcaller isolation/explicitprecedence/nested
metadata. OtheradapteroverrideauditfoundNOsecondaliasdependency.

Signed/pushed601158e575cbffd3d1aa773b59b5507b0e0af8f4 parent57, SAMEPR49.
New6regressions4red/2controls then331BigQuery/jobs/base/nestedcasesgreen
EACHkernel(10.16/10.45s). Cap63/64boundaryallowed,65refusedwithonlydry
queryrecords/zeroloads/tables; originalassertionsunchanged. Ruff/format/
mypy/wordinggreen. ReviewerOct25APPROVEDexact601security/scope/DCO.
No paidquery/liveaccount/localfullsuite. No Bbranch/issueedited;W9-13
receivesintegrationevidenceonOURbranch. PRdescriptionupdatedwithguardfix,
all4closingreferencesretained. RootbatchFF601/pushedclean.

CURRENTexactheadruns: CI40274846869,Wheels40274846890,Security40274846877,
Container40274846871,Documentation40274846901,DCO40274846889,
action-selftest40274846865. InitiallypendingexceptDCOgreen. Both
/tmp/public-a-poll-final.py and /tmp/public-a-merge-final.py updated601.
RequireallnewexactheadjobsincludingBOTHdatabase-pluginjobs/8platform
corejobs/kernels/7workflowsoverallgreen,currentmain682ancestor,then
guardedAPImergecommitwithDCO. Allpolicyquestionsresolved; no park.

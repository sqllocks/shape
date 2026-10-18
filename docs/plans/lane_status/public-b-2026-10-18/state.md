# Public B state

Scratch: `lead/public-b-2026-10-18`. No product merges yet. Latest main: `d0e64d53eecfe03b29caa5dd111598f3b9a9ace2`.
Historical unsigned own branches are preserved; signed clean `-finish` continuations apply only assigned lane changes. W9-09 is a new branch.

| Issue | Branch / head | Stage | Next action |
|---|---|---|---|
| #20 | no product branch | PARKED comparison contract | FRZ-01-evidence.md and questions.md |
| #6 | lane/W9-05 `3de70e3` | PARKED unsupported-baseline contract | no child-feature implementation |
| #9 | `lane/W9-08-finish` `91ef6200266bf962246d1553356b26a442be2303` | [PR#32](https://github.com/sqllocks/shape/pull/32); {'SUCCESS': 24, 'IN_PROGRESS': 8, 'SKIPPED': 2} | resume_metadata; independent review APPROVED; common local gates pending |
| #10 | `lane/W9-09` `d6dc9f33b4593215f4eb561b7a95788fe67363f5` | [PR#34](https://github.com/sqllocks/shape/pull/34); {'SUCCESS': 8, 'QUEUED': 19, 'SKIPPED': 2, 'IN_PROGRESS': 5} | formats; independent review APPROVED; common local gates pending |
| #11 | `lane/W9-10-finish` `6438d907727e2b71c3993230615cc10a9376b2e7` | [PR#29](https://github.com/sqllocks/shape/pull/29); {'SUCCESS': 17, 'IN_PROGRESS': 9, 'SKIPPED': 1, 'QUEUED': 4} | resume_dbt; independent review APPROVED; common local gates pending |
| #12 | `lane/W9-11-finish` `a7e450ad105a71c334d00eeed1c8b7291aac6153` | [PR#30](https://github.com/sqllocks/shape/pull/30); {'SUCCESS': 17, 'QUEUED': 12, 'SKIPPED': 2, 'IN_PROGRESS': 3} | resume_eventhouse; independent review APPROVED; common local gates pending |
| #13 | `lane/W9-12-finish` `5e583f2e0b71798943d947fbb017374ce801d015` | [PR#33](https://github.com/sqllocks/shape/pull/33); {'SUCCESS': 11, 'QUEUED': 15, 'SKIPPED': 2, 'IN_PROGRESS': 6} | root; independent review APPROVED; common local gates pending |
| #17 | `lane/W9-16-finish` `632360ae51adc88fce42bdbefb44136a05986a6e` | [PR#31](https://github.com/sqllocks/shape/pull/31); {'QUEUED': 30, 'IN_PROGRESS': 9, 'SUCCESS': 1} | resume_iceberg; independent review APPROVED; common local gates pending |
| #14 | preserved lane/W9-13 `1249af9` | PARKED dependency/decimal decisions | questions.md |

All six draft PRs are attached. Independent feature reviews APPROVED. Formats safe unsigned/UUID fix APPROVED at693daf7; Protobuf nested message-index fix d6dc9f33b4593215f4eb561b7a95788fe67363f5 signed/pushed and independent APPROVED,214focused per mode, finalsource frozen. Semantic CI integration fix signed/pushed5e583f2 and independently APPROVED;87focusedpass, DRIFT_REPORT and finalCONTRIBUTING fullreplays pass; allcurrent CIchecks nofailure. No gate/test assertion changes.

Executor restarted around17:36UTC, killing old live test sessions. Those results are INTERRUPTED and not passes. Durable detached workers now use one sequential queue each and shared `/tmp/public-b-full-check.lock`, saving per-job head/tree/exit/zombies/logs. DbT141889 `/tmp/w9-10-queue/state.json`; Eventhouse113611 `/tmp/w9-11-detached-status.json`; Iceberg113704 `/tmp/w9-16-queue/status.json`; metadata113789 `/tmp/w9-08-validation/status.json`; semantic141454 `/tmp/w9-12-queue/state.json`; formats178847 `/tmp/w9-09-check-worker-state.json`. Eventhouse core-rust is active under shared lock, exact a7e450a clean; dbt native retry is queued after confirmed Docker-run ENOSPC (prior exit2, 4720passed, no passing claim); others wait. No completed full common acceptance after recovery yet.

Source `/tmp/public-b-test-env.sh` only after lock acquisition: pinneduv0.8.17, onboardingRust, process-localumask022, documented DockerproxyIP/CA, pinned DBT packages. Base core/content Arrow25 excluding onlyFabric, separate Fabric Arrow19 directory both modes; union includes all offline tests. Every test under `$HOME/bin/reap.py`. Zombies last85 below1000. No live passes claimed. Main has not changed since initial snapshot.

Docker VFS buildcache reported2.9GB but occupied15GB clonedlayers; unusedcacheprune restored15GB free. All queues now clean unusedcache after acquisition beforejobs, recording exits; no source/harness changes.

Next: monitor exact-head hosted CI and detached workers, diagnose actual own failures. Merge only all required gates green, review bound and currentmain contained; use API mergecommit, then merge main into other own lanes retaining both changelogs and refresh CI. No LeadA branches/issues, settings, release actions, tokens or names outside permitted scope.

Latest18:12UTC cycle: Iceberg zero-network oldhead had1failure18686passes: INSTALL offline lock-set inventory omittednewiceberg. Signed docs-onlyfix632360ae, unchanged testred→green and independentAPPROVED; obsoletea73 CI cancelled afterheadverification, currentchecks rerun. EventhousefullCONTAINERcompleted withoutENOSPC, currentremainingdocs; disk2.4GBfree under retainedVFS cache, cleanbeforeeachnextjob. No full commonpass or merges yet.

18:19UTC runtime storage update: shared helper now exports only pytest temporary retention policy failed (same option existing docsCI uses), with no test selection/assertion changes. All queuedworkers source/allowlist helper AFTERlock; formats waiting worker restarted178847 beforetests. Eventhousefullnative reached45% nofailures afterdocspages, overlay2.4GBfree/tmp2.3GBfree/zombies102. Active117 retains6.2GB successfulpageoutput; author is preserving diagnostics and cleaningonlyconfirmedfinishedpages. No active testroot deleted. Initial root cleanup check found stopped106 alreadyautoexpired, no removal occurred.

18:23UTC integration preparation: read-only merge-tree previews predictproduct conflicts onlystreamingpair. Formats preparesuncommitteddetached/tmp/public-b-stream-integration; metadata independentlyreviews; no PR/sourcehead changes orcommits. Other4pair previews onlydocs/requirements. Detailsintegration-plan.md. Eventhouse currentnative53% nofailures; archived657 successful reference-page evidencefiles+manifest copiedtoscratch evidenceaftercredential/banned-name scan. Authorremovedonly67finishedself-containedpage dirs afterSHAverification andtwicecheckingproc cwd (currenttestFabricgeneration), noactivepage/currenttestfilesremoved. /tmp7.3GBfree, overlay2.4GBfree, zombies103.

18:27UTC streaming preview independently APPROVED tree d6d5ec550bfbe9295b5273a2e285374c7671f436;12 newcrosscases independentlypass. Two realinteractions: Kafka keyoption variable collision (combination-only) anddeadletterreason positionalmisalignment afterbinaryencode skip (ALSO standaloneB09d6dc9f3). Rootrequired standaloneformatsfix testsfirst beforefinalCI; formats isdoingit now, sourceheadwillchangeandneedsfreshreview/CI. Preservecombination-onlyevent_keyfix in uncommittedpreviewpatch foractualmainrefresh. No productmerge/push ofcombinedtree.

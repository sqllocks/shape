# Public B state

Scratch: `lead/public-b-2026-10-18`. No product merges yet. Latest main: `d0e64d53eecfe03b29caa5dd111598f3b9a9ace2`.
Historical unsigned own branches are preserved; signed clean `-finish` continuations apply only assigned lane changes. W9-09 is a new branch.

| Issue | Branch / head | Stage | Next action |
|---|---|---|---|
| #20 | no product branch | PARKED Owner comparison contract | artifacts/logs retrieved; six exact failures and values in FRZ-01-evidence.md; no allowed gate-preserving fix |
| #6 | lane/W9-05 `3de70e3` | PARKED Owner decision | Wanted5 conflicts with recorded-error equivalence; updated Owner action; no product implementation |
| #9 | `lane/W9-08-finish` `91ef6200266bf962246d1553356b26a442be2303` | [PR#32](https://github.com/sqllocks/shape/pull/32); 25/34 CI complete; failures: none | resume_metadata; detached local acceptance queue; exact-head CI and review required |
| #10 | `lane/W9-09` `d6dc9f33b4593215f4eb561b7a95788fe67363f5` | [PR#34](https://github.com/sqllocks/shape/pull/34); 8/33 CI complete; failures: none | formats; detached local acceptance queue; exact-head CI and review required |
| #11 | `lane/W9-10-finish` `6438d907727e2b71c3993230615cc10a9376b2e7` | [PR#29](https://github.com/sqllocks/shape/pull/29); 13/31 CI complete; failures: none | resume_dbt; detached local acceptance queue; exact-head CI and review required |
| #12 | `lane/W9-11-finish` `a7e450ad105a71c334d00eeed1c8b7291aac6153` | [PR#30](https://github.com/sqllocks/shape/pull/30); 15/34 CI complete; failures: none | resume_eventhouse; detached local acceptance queue; exact-head CI and review required |
| #13 | `lane/W9-12-finish` `5e583f2e0b71798943d947fbb017374ce801d015` | [PR#33](https://github.com/sqllocks/shape/pull/33); 5/33 CI complete; failures: none | root; detached local acceptance queue; exact-head CI and review required |
| #17 | `lane/W9-16-finish` `a73b434b22368fe17f201270d9434420cefee1c0` | [PR#31](https://github.com/sqllocks/shape/pull/31); 29/44 CI complete; failures: none | resume_iceberg; detached local acceptance queue; exact-head CI and review required |
| #14 | preserved lane/W9-13 `1249af9` journal only | PARKED dependency and decimal boundaries | W9-01 absent from main; await merged interface; questions.md has options |

All six draft PRs are attached. Independent feature reviews APPROVED. Formats safe unsigned/UUID fix APPROVED at693daf7; Protobuf nested message-index fix d6dc9f33b4593215f4eb561b7a95788fe67363f5 signed/pushed and independent APPROVED,214focused per mode, finalsource frozen. Semantic CI integration fix signed/pushed5e583f2 and independently APPROVED;87focusedpass, DRIFT_REPORT and finalCONTRIBUTING fullreplays pass; allcurrent CIchecks nofailure. No gate/test assertion changes.

Executor restarted around17:36UTC, killing old live test sessions. Those results are INTERRUPTED and not passes. Durable detached workers now use one sequential queue each and shared `/tmp/public-b-full-check.lock`, saving per-job head/tree/exit/zombies/logs. DbT141889 `/tmp/w9-10-queue/state.json`; Eventhouse113611 `/tmp/w9-11-detached-status.json`; Iceberg113704 `/tmp/w9-16-queue/status.json`; metadata113789 `/tmp/w9-08-validation/status.json`; semantic141454 `/tmp/w9-12-queue/state.json`; formats141965 `/tmp/w9-09-check-worker-state.json`. Eventhouse core-rust is active under shared lock, exact a7e450a clean; dbt native retry is queued after confirmed Docker-run ENOSPC (prior exit2, 4720passed, no passing claim); others wait. No completed full common acceptance after recovery yet.

Source `/tmp/public-b-test-env.sh` only after lock acquisition: pinneduv0.8.17, onboardingRust, process-localumask022, documented DockerproxyIP/CA, pinned DBT packages. Base core/content Arrow25 excluding onlyFabric, separate Fabric Arrow19 directory both modes; union includes all offline tests. Every test under `$HOME/bin/reap.py`. Zombies last85 below1000. No live passes claimed. Main has not changed since initial snapshot.

Docker VFS buildcache reported2.9GB but occupied15GB clonedlayers; unusedcacheprune restored15GB free. All queues now clean unusedcache after acquisition beforejobs, recording exits; no source/harness changes.

Next: monitor exact-head hosted CI and detached workers, diagnose actual own failures. Merge only all required gates green, review bound and currentmain contained; use API mergecommit, then merge main into other own lanes retaining both changelogs and refresh CI. No LeadA branches/issues, settings, release actions, tokens or names outside permitted scope.

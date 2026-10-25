# Public B interim report — 2026-10-20

Work is continuing. This is not the final completion report. The earlier transient credential interruption resolved with fresh command environments; authentication settings were unchanged. Current main is `404fed823b271525b8802254312ded28f1c5aaa8` after PR33 merged. Its full post-merge gate is still running; no completed main-green claim.

## Progress

Nine assigned issues: three merged (33.3%), six ready/reviewed (66.7%), none dependency-blocked. Six remain unmerged (66.7%). All nine implementations independently approved (100% implementation readiness); delivery remains33.3%. Supporting PR40 outside denominator.

| Issue | Status | PR | Merge SHA |
|---|---|---|---|
| #9 Kafka/Event Hubs metadata | Merged | [32](https://github.com/sqllocks/shape/pull/32) | `35addfc4c310b85a62cbe7f315e1d50fb8e426f8` |
| #10 Kafka/Event Hubs formats | Implemented, ready, independently approved; exact-head CI running | [34](https://github.com/sqllocks/shape/pull/34) | None |
| #11 dbt | Implemented, ready, independently approved; exact-head CI running | [29](https://github.com/sqllocks/shape/pull/29) | None |
| #12 Eventhouse | Merged; all6 source-head workflows green, merged-head gate pending | [30](https://github.com/sqllocks/shape/pull/30) | `5c6d46ff1703db9fd2307bc4ebd4fb087e9af733` |
| #13 Semantic measures/roles | Merged; all6source workflows green, newmain gate pending | [33](https://github.com/sqllocks/shape/pull/33) | `404fed823b271525b8802254312ded28f1c5aaa8` |
| #17 Iceberg | Implemented, ready, independently approved; prior Windows repair passed both versions; current core/Wheels held for next main refresh | [31](https://github.com/sqllocks/shape/pull/31) | None |
| #6 Nested profiling | Catalog max_depth fix complete at548b976, independently approved; fresh CI pending; depends on PR42 | [43](https://github.com/sqllocks/shape/pull/43) | None |
| #14 BigQuery | Implemented, independently approved; hosted full CI held until priorstages | [47](https://github.com/sqllocks/shape/pull/47) | None |
| #20 Frozen comparison | Three-part value fix and Windows manifest portability repair complete, independently approved20cfff2; fresh CI/merge/Nightly pending | [42](https://github.com/sqllocks/shape/pull/42) | None |

All current feature heads and branches are recorded in [state.md](state.md). Their existing reviews remain bound to those heads. Canceled checks are not passes. No open feature PR has a complete green exact-head gate established.

## Acceptance and CI

Targeted acceptance red → green results below summarize the existing lane records. Historical full-suite interruptions are not passes. CI is the full-suite gate under the October20 throughput decision, recorded in the decision log and in PR29. No new raw-log archive or gate-state snapshot was created.

| Issue | Red against base | Green targeted acceptance |
|---|---|---|
| #9 | `15 failed, 6 passed in 2.60s`; validation `4 failed, 1 passed, 6 deselected` | 271 passed in each kernel; validation `5 passed, 6 deselected` |
| #10 | `28 failed in 1.06s`; later dead-letter regression 2 failed | 345 passed, 38 deselected in each kernel after the actual code-conflict refresh; dead-letter regression 2 passed |
| #11 | All ten initial acceptance cases failed before implementation | 142 offline passed in each kernel; 8 actual DuckDB/dbt acceptance passed |
| #12 | Acceptance collection failed: source module missing; CLI test failed before hook | 46 passed in each kernel |
| #13 | `40 failed, 7 passed` against unchanged main | 171 focused passed in each kernel |
| #17 | Original suite 18 failures: new module missing; expanded suite collection failed | 128 focused passed in each kernel; 16 adapter cases passed with SDK0.12 and minimum0.9 |
| #6 | 14 acceptance cases failed in each kernel against base; later boundary regressions red | 221 focused/changed tests including both 1 GB cases; Python 220 nonheavy; capsules 171; conformance 39; corrected docs workflow green |
| #20 | Category11initial red; original generation verifier reproduces14flags; prior profile/timing code reproduces5red controls | Final138targeted eachkernel; actualhealthcare80/80 andcapital64/64 allclausesa-f; both time-only fixtures0mismatches; unchanged original digests/thresholds |

The native joint-heavy functional selection missing from CI passed in every implemented lane. No live account success is claimed.

PR32 merged source head `91ef6200266bf962246d1553356b26a442be2303` had every triggered workflow green:

- [CI](https://github.com/sqllocks/shape/actions/runs/39147911536)
- [Documentation](https://github.com/sqllocks/shape/actions/runs/39147911936)
- [DCO](https://github.com/sqllocks/shape/actions/runs/39147911789)
- [Security](https://github.com/sqllocks/shape/actions/runs/39147911639)
- [Container](https://github.com/sqllocks/shape/actions/runs/39147911784)
- [Action self-test](https://github.com/sqllocks/shape/actions/runs/39147911556)

Merged-head main workflows all completed successfully:

- [CI](https://github.com/sqllocks/shape/actions/runs/39483030770), all 27 jobs passed.
- [Documentation](https://github.com/sqllocks/shape/actions/runs/39483030734).
- [Security](https://github.com/sqllocks/shape/actions/runs/39483030728).

## Shared repair and next actions

[PR40](https://github.com/sqllocks/shape/pull/40) merged as `53d3d0c1a740fcca0665c216c3ebdabaaac95e49` after independent exact-head approval and all four triggered workflows passed (30 successful jobs; conditional documentation publish skip only). Source-head green links and targeted red/green are in CI-runtime-repair.md. The new main's full gate remains pending; its security/build checks passed.

All seven open lanes were refreshed, signed/pushed and independently approved. Current exact heads are in state.md. Root deliberately canceled lower-priority31 coreCI/Wheels and34 coreCI to release hosted runner slots for first group29/30/33; these are NOT passes and must rerun after next main refresh. Other fresh gates are queued/running. PR43 had a genuine compatibility failure on the superseded66ace head, repaired and independently approved at5ae6460; fresh CI is required. Merge whichever first-group member is fully green/current/reviewed first; then refresh other lanes, then34/31, then42/43, then14 after45/main interface. No local fulls. Product-code conflict refreshes require only targeted tests.

PR42 at `0b95d31a7f70fcaa2c399b1ff121420b66eb0113` implements recorded-error classification, authenticated name aliases and scoped pre-computation capture-date replay. No original recording/manifest/fixture/numeric value or threshold changed. Reviewer frz_review approved complete exact source. All four previous value failures pass targeted acceptance; final138tests pass eachkernel and static checks pass. It uses Refs20; no closure or main Nightly pass is claimed. PR43 still depends on42 landing and combined exact-head/main checks.

## Pending Owner/service actions

- #9: Kafka/Event Hubs account round trips; nightly broker emulator coverage.
- #10: broker/account round trips and Azure Schema Registry Avro round trip; nightly emulator coverage. Existing Azure scope approval is already resolved.
- #11: no account-dependent live requirement; actual file-based DuckDB/dbt acceptance passed.
- #12: Kusto/Eventhouse emulator and account read-back.
- #13: Owner supplies a model with a measure and row-level-security role, expected metadata and role-visible rows. Existing live test covers relationships/table reads; add measure/role read-back assertions after fixture approval. No role deployment or live query occurred.
- #17: REST/MinIO emulator validation when images are available; no account-live pass is claimed; both Windows3.11 and3.14 core CI jobs passed on8c9fa927.

## Remaining dependency and required post-merge acceptance

Only #14 remains dependency-blocked: PR45 (replacement for closed-unmerged35) must merge before reusing W9-01's source interface. Its decimal rule is already approved:0<=scale<=precision before connecting, naming the column. Start fresh from then-main, not its historical journal ancestry. [Issue clarification](https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080).

#20 has no pending Owner comparison question after the provenance reconstruction. The two explicit recorded-error workloads never count as passes. Four value workloads now pass unchanged checks. After implementation merges, dispatch Nightly on main and require all five parity shards, parity-publish and sqlserver-e2e green; link the run and close20 only through a subsequent PR. No Nightly was dispatched or claimed yet. [Current issue status](https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664). Exact safeguards and provenance are in questions.md and the reviewed FRZ lane record.

All owned local implementation commits are pushed. Current branch heads/reviews/next actions are in state.md. Lead A branches/issues and Premium were not modified. This report remains INTERIM while active merge gates run.

PR43 superseded-head failure: Windows3.14 job118909326600 failed in existing nested generation/diff/CLI compatibility tests; targeted reproduction16failed/92passed. The reviewed repair is described below. Main and the first merge group have no observed failure.

PR43 compatibility repair signed/pushed5ae6460db5f2ce0ee06070434104b71745a63523 and independently APPROVED by frz_resume_review. Targeted16red→170green eachkernel;183changed consumers,24scalar cases and2snapshot/merge cases green; static/mypy705/wording clean. Fixedwidth-only internal exact/HLL support preserves actual distinct statistics without exposing surrogates; legacy length names restored; incomplete safe length models use existing public defaults1–3 without reconstructing private extrema. Fresh exact-head CI and42 dependency remain gates.

## PR30 merged; new integration gate

PR30 merged2026-10-20T23:47:06Z as5c6d46ff1703db9fd2307bc4ebd4fb087e9af733. Exact reviewed headcad8cfd had all6 workflows green: [CI](https://github.com/sqllocks/shape/actions/runs/39504103202), [Documentation](https://github.com/sqllocks/shape/actions/runs/39504103156), [Security](https://github.com/sqllocks/shape/actions/runs/39504103173), [DCO](https://github.com/sqllocks/shape/actions/runs/39504103258), [Container](https://github.com/sqllocks/shape/actions/runs/39504103180), [action-selftest](https://github.com/sqllocks/shape/actions/runs/39504103224). Acceptance collection/CLI red→46green eachkernel; live/emulator pending as above. New-main [CI](https://github.com/sqllocks/shape/actions/runs/39517243088), [Documentation](https://github.com/sqllocks/shape/actions/runs/39517243016), [Security](https://github.com/sqllocks/shape/actions/runs/39517243183) pending. Prior main53 CI automatically canceled on base move, not a pass. All6 remaining implementations refreshed/signed/pushed; reviews29/33/31/34/42 APPROVED. PR43 review found catalog recursion omitsmax_depth; targeted fake catalog0/1 fix in progress. No full local reruns.

PR43 catalog integration repair completed: exact548b97651b169f626d891f94d782dfe697d55cfc signed/pushed and independently APPROVED. New fake catalog0/1 cases genuine2red eachkernel→110green child+Eventhouse eachkernel; static/mypy705/wording clean. Forwards max_depth unchanged and preserves sampling metadata. No full local or reviewer reruns.

Scheduling at23:56UTC: current31 coreCI39517323914/Wheels39517323900 and34 coreCI39517315974 canceled to prioritize29/33/main runner capacity. Cancellations are NOTpasses. Hold these later-priority fulls until both firstgroup PRs merge and root advances stage; lightweight gates continue.42/43 CI remains active for regression detection.

Dependency routing: LeadA closed35 unmerged and replaced it with [PR45](https://github.com/sqllocks/shape/pull/45), identical source tree with corrected DCO trailers. Issue2 remains open;14 stays parked until replacement45 merges. [Supersession evidence](https://github.com/sqllocks/shape/pull/35#issuecomment-6304433722).

## Targeted comparison results — qualifying main Nightly still pending

All four original workloads with recorded values pass their unchanged comparisons in the reviewed PR42 implementation:

| Workload | Targeted result |
|---|---|
| `generate:healthcare:medium` | PASS, 80/80 columns and every original clause a–f |
| `generate:capital_markets:medium` | PASS, 64/64 columns and every original clause a–f |
| `profile:edge/x_time_only.csv` | PASS, zero mismatches, exit0 |
| `profile:edge/x_time_ampm.csv` | PASS, zero mismatches, exit0 |

These two workloads are reported separately and **never count as passes or receive timing credit**:

| Workload | Category | Recorded0.9.1 reason |
|---|---|---|
| `profile:edge/x_pq_list.parquet` | NOT COMPARABLE | TypeError on list-valued Parquet; no baseline profile values |
| `profile:edge/x_pq_nested.parquet` | NOT COMPARABLE | TypeError on struct-valued Parquet; no baseline profile values |

The reviewed registry is `benchmarks/vs_refengine/comparison_contract.py` in PR42. No recordings, value comparisons or thresholds changed. These targeted results do not establish the required post-merge main Nightly acceptance or close20.

PR42 update01:19UTC: CI39517332719 Windows3.14 failed six capture-date cases because str(relative_path) uses backslashes for canonicalPOSIX manifest lookups. Author addingportableWindows/POSIX red test andas_posix serialization fix; strict digests, records, values and thresholds unchanged. Independent frz_review_resume reviewing repair. No full local runs.

PR42 portability repair complete:20cfff275c14d1ac44e77575fbc9be769181f2dc signed/pushed, independently APPROVED frz_review_resume. Regression1red/1green→35focused tests green eachkernel; static/user-facing and configuredmypy703sources green. Canonicalpathkey only, strict hashes/values/records/thresholds unchanged. FreshCI39526872370 pending; oldfailed4ebrun neverqualifies.

## PR33 merged — third assigned issue delivered

PR33 merged2026-10-21T01:36:10Z as404fed823b271525b8802254312ded28f1c5aaa8, closing13. Exact approved190c763 had32successful checks plus2structural publishskips; all6workflows SUCCESS: [CI](https://github.com/sqllocks/shape/actions/runs/39517317496), [Documentation](https://github.com/sqllocks/shape/actions/runs/39517317492), [Security](https://github.com/sqllocks/shape/actions/runs/39517317518), [DCO](https://github.com/sqllocks/shape/actions/runs/39517317485), [Container](https://github.com/sqllocks/shape/actions/runs/39517317531), [action-selftest](https://github.com/sqllocks/shape/actions/runs/39517317589). Acceptance40red/7green againstbase→171focusedgreen eachkernel. Owner measure/role livefixture remains pending, no livepass claimed. New-main [CI](https://github.com/sqllocks/shape/actions/runs/39528275403), [Docs](https://github.com/sqllocks/shape/actions/runs/39528275422), [Security](https://github.com/sqllocks/shape/actions/runs/39528275442) pending. Allfive remaining implementation branches refreshed/signed/pushed and independentlyapproved; no productconflicts orlocalreruns.29 is finalfirstgroup PR.31/34 currentfulls canceled/helduntil29merge+explicitstageadvance; cancellations neverpasses.

Runner priority01:50UTC:42core39528369224 and43core39528332280 canceled/helduntil34+31merge+rootexplicitadvance, alongside31/34fullholduntil29merge. Laterstageswillrequirefreshmainrefreshandallgreenexact-headCIbeforemerge. Lightweightcheckscontinue; no source/tests/gates changed and cancellations neverpasses.

Oct25 recovery: currentmainc1ff1c8 includesLeadA45;14unparkedonfreshlane/W9-13-finish fromc1ff(nohistoricaljournalancestry). Five other implementations refreshed/signed/pushed andindependentlyapproved:29 550c114,31 6497953,34 41e67fe,42 2cb1411,43 02d6913.31/43realsharedsource/privacy/bridgeconflicts repairedwithtargetedbothkernels(31 536each,43 258each+22validationeach) andstaticgreen, nofull localruns. Alllaterfullsheldpermergepriority, neverpasses. Our404mainCI39528275403/Docs39528275422/Security39528275442 nowverifiedSUCCESS. Currentc1CIrunningno failures;Doc/Sec/WheelsSUCCESS. NoqualifyingmainNightly for20yet.

## BigQuery implementation and review complete

[PR47](https://github.com/sqllocks/shape/pull/47) ready exactsigned+pushedadd7dd416917d96a4b4ccd671d77493d0c6af5c0, independentlyAPPROVED metadata_oct25 all7Wanted/security/scope.38initial/56expandedacceptanceREDagainstunchangedc1ff→70BQGREENeachkernel;276changedmodulesGREENeachkernel. Reviewerblockers genuinelyred→green:fragmentrejection4red,fragmentdisplay3red→8green,first-loadmismatch+commit_rowscleanup1red; previousconfirmedstreamcommits preserved. RealSDKconfig/factoryofflineproof,strictadaptermypy1/core704,Ruff/publiccheck/requirements/conformance/source-links green. Noaccount/livepass. CoreCI40230079267 canceled/helduntilpriorstages; lightcheckscontinue.9/9implementationsreviewed,3/9delivered,6/9unmerged. Later47refreshafter43mustreviewshareddepth/sourceintegrationandallfreshhostedgates.

# Public B interim report — 2026-10-20

Work is continuing. This is not the final completion report. The earlier transient credential interruption resolved with fresh command environments; authentication settings were unchanged. Current main is `53d3d0c1a740fcca0665c216c3ebdabaaac95e49` after supporting PR40 merged. Its full post-merge gate is still running; no completed main-green claim.

## Progress

Nine assigned issues: one merged (11.1%), seven ready/reviewed implementations (77.8%), one dependency-blocked (11.1%). Eight remain unmerged (88.9%). Eight have ready implementation including the merged issue (88.9%). #20 implementation is complete and approved; delivery still requires CI/merge/main Nightly/closure. Supporting PR40 is outside the nine-issue total.

| Issue | Status | PR | Merge SHA |
|---|---|---|---|
| #9 Kafka/Event Hubs metadata | Merged | [32](https://github.com/sqllocks/shape/pull/32) | `35addfc4c310b85a62cbe7f315e1d50fb8e426f8` |
| #10 Kafka/Event Hubs formats | Implemented, ready, independently approved; CI hold pending shared repair | [34](https://github.com/sqllocks/shape/pull/34) | None |
| #11 dbt | Implemented, ready, independently approved; CI hold pending shared repair | [29](https://github.com/sqllocks/shape/pull/29) | None |
| #12 Eventhouse | Implemented, ready, independently approved; CI hold pending shared repair | [30](https://github.com/sqllocks/shape/pull/30) | None |
| #13 Semantic measures/roles | Implemented, ready, independently approved; CI hold pending shared repair | [33](https://github.com/sqllocks/shape/pull/33) | None |
| #17 Iceberg | Implemented, ready, independently approved; both current-head Windows core jobs passed; shared repair still required | [31](https://github.com/sqllocks/shape/pull/31) | None |
| #6 Nested profiling | Ready and independently approved; depends on PR42 and current-main CI | [43](https://github.com/sqllocks/shape/pull/43) | None |
| #14 BigQuery | Parked: source interface dependency absent from main | None | None |
| #20 Frozen comparison | Complete three-part fix ready and independently approved; all four value workloads pass; CI/merge/Nightly pending | [42](https://github.com/sqllocks/shape/pull/42) | None |

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

All seven open lanes were refreshed, signed/pushed and independently approved. Current exact heads are in state.md. Root deliberately canceled lower-priority31 coreCI/Wheels and34 coreCI to release hosted runner slots for first group29/30/33; these are NOT passes and must rerun after next main refresh. Other fresh gates are queued/running, with no observed failure. Merge whichever first-group member is fully green/current/reviewed first; then refresh other lanes, then34/31, then42/43, then14 after35/main interface. No local fulls. Product-code conflict refreshes require only targeted tests.

PR42 at `0b95d31a7f70fcaa2c399b1ff121420b66eb0113` implements recorded-error classification, authenticated name aliases and scoped pre-computation capture-date replay. No original recording/manifest/fixture/numeric value or threshold changed. Reviewer frz_review approved complete exact source. All four previous value failures pass targeted acceptance; final138tests pass eachkernel and static checks pass. It uses Refs20; no closure or main Nightly pass is claimed. PR43 still depends on42 landing and combined exact-head/main checks.

## Pending Owner/service actions

- #9: Kafka/Event Hubs account round trips; nightly broker emulator coverage.
- #10: broker/account round trips and Azure Schema Registry Avro round trip; nightly emulator coverage. Existing Azure scope approval is already resolved.
- #11: no account-dependent live requirement; actual file-based DuckDB/dbt acceptance passed.
- #12: Kusto/Eventhouse emulator and account read-back.
- #13: Owner supplies a model with a measure and row-level-security role, expected metadata and role-visible rows. Existing live test covers relationships/table reads; add measure/role read-back assertions after fixture approval. No role deployment or live query occurred.
- #17: REST/MinIO emulator validation when images are available; no account-live pass is claimed; both Windows3.11 and3.14 core CI jobs passed on8c9fa927.

## Remaining dependency and required post-merge acceptance

Only #14 remains dependency-blocked: PR35 must merge before reusing W9-01's source interface. Its decimal rule is already approved:0<=scale<=precision before connecting, naming the column. Start fresh from then-main, not its historical journal ancestry. [Issue clarification](https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080).

#20 has no pending Owner comparison question after the provenance reconstruction. The two explicit recorded-error workloads never count as passes. Four value workloads now pass unchanged checks. After implementation merges, dispatch Nightly on main and require all five parity shards, parity-publish and sqlserver-e2e green; link the run and close20 only through a subsequent PR. No Nightly was dispatched or claimed yet. [Current issue status](https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664). Exact safeguards and provenance are in questions.md and the reviewed FRZ lane record.

All owned local implementation commits are pushed. Current branch heads/reviews/next actions are in state.md. Lead A branches/issues and Premium were not modified. This report remains INTERIM while active merge gates run.

# Public B interim checkpoint — 2026-10-20 21:40 UTC

Work is continuing. This is not the final completion report. The earlier transient credential interruption resolved with fresh command environments; authentication settings were unchanged. Current main is `35addfc4c310b85a62cbe7f315e1d50fb8e426f8`.

## Progress

Nine assigned issues: one merged (11.1%), six ready/reviewed implementations (66.7%), two blocked or partially parked (22.2%). Eight remain unmerged (88.9%). Seven have ready implementation including the merged issue (77.8%). Partial #20 category work is not full delivery. Supporting PR40 is outside the nine-issue total.

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
| #20 Frozen comparison | Reviewed error category ready; four value-recording workloads remain parked | [42](https://github.com/sqllocks/shape/pull/42) | None |

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
| #20 partial category | 11 initial red; stronger base-behavior 3 red; identity 2 red and forged-pass 1 red | 107 focused passed; 21 Python category controls passed; four value workloads remain parked |

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

[PR40](https://github.com/sqllocks/shape/pull/40), head `3f6254768085d6f96683550c8939998170be989f`, is ready and independently APPROVED. It updates certifi to2026.10.19 plus the generated docs pin, uses pinned Playwright Chromium, and installs that browser before Documentation verification. No existing job, gate, assertion, dependency range, project version or tolerance was removed or loosened.

Targeted advisory audit: old version exit1 with GHSA-6q48-6c7w-9gw2 → fixed version exit0, no known vulnerabilities. Branding acceptance:1 passed in30.95s. Static/lock/export checks passed. Exact-head complete CI is required before merge; no unfinished run is represented as passing.

Merge priority:40, then29/30/33 (32 already merged), then34/31, then42/43, then14 after Lead A's35. Refresh own open lanes after main moves, preserving additive entries. Run local targeted tests only for code conflicts, rebind independent reviews and require all exact-head triggered CI. Do not restart historical local full-suite workers. #20 stays open until its value acceptance is resolved and a qualifying main Nightly is green on all five parity shards, parity-publish and sqlserver-e2e.

## Pending Owner/service actions

- #9: Kafka/Event Hubs account round trips; nightly broker emulator coverage.
- #10: broker/account round trips and Azure Schema Registry Avro round trip; nightly emulator coverage. Existing Azure scope approval is already resolved.
- #11: no account-dependent live requirement; actual file-based DuckDB/dbt acceptance passed.
- #12: Kusto/Eventhouse emulator and account read-back.
- #13: Owner supplies a model with a measure and row-level-security role, expected metadata and role-visible rows. Existing live test covers relationships/table reads; add measure/role read-back assertions after fixture approval. No role deployment or live query occurred.
- #17: REST/MinIO emulator validation when images are available; no account-live pass is claimed; both Windows3.11 and3.14 core CI jobs passed on8c9fa927.

## Parked questions

Full options/evidence are in [questions.md](questions.md).

- #20: Owner approved error/unsupported-recording classification, implemented in PR42. Reviewed registry names `profile:edge/x_pq_list.parquet` and `profile:edge/x_pq_nested.parquet`, both recorded TypeError with no values. They are reported separately and never count as passes. Four value workloads remain parked: `profile:edge/x_time_only.csv`, `profile:edge/x_time_ampm.csv`, `generate:capital_markets:medium`, `generate:healthcare:medium`. Recommendation: preserve strict recorded-value comparisons until a precise further comparison-context decision exists. Runtime timestamp exclusions and reference-name substitutions do not authorize changing calendar histograms or code values. [Updated issue comment](https://github.com/sqllocks/shape/issues/20#issuecomment-6301325664).
- #6: former Wanted5 decision is resolved by the same Owner category approval. PR43 is ready and independently approved at4704c711; it depends on PR42 landing and combined current-main checks.
- #14: wait for W9-01's source interface on main; PR35 remains open at the latest read. Recommendation: wait, start a fresh lane branch from then-main, and reuse the merged interface. Owner-approved validation is `0 <= scale <= precision` before connecting, with the column named in the error. [Issue comment](https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080).

Full options and evidence remain in questions.md. Lead A branches/issues and Premium were not modified. All owned local implementation commits are pushed; exact branch SHAs are in state.md.

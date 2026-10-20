# Public B interim checkpoint — 2026-10-20 20:33 UTC

**Interruption resolved at20:35UTC:** fresh commands successfully authenticated to both REST and GraphQL without changing authentication settings. The401 was confined to the long-running watcher, which was stopped. Work resumed using fresh commands. This is a historical checkpoint, not the final completion report; statuses below are the20:33 observations.

GitHub GraphQL returned HTTP 401 “Bad credentials” while reading PR40. The attached prompt explicitly makes credential failure a hard stop. No authentication settings were changed and no subsequent merge was attempted. Latest remote main verified before the failure: `35addfc4c310b85a62cbe7f315e1d50fb8e426f8`.

## Progress

Nine assigned issues: one merged (11.1%), five implemented/reviewed but unmerged (55.6%), three parked (33.3%). Eight remain unmerged (88.9%). Six have implementation (66.7%), including the merged issue. Under the run's merged-or-parked stopping criterion, four are handled (44.4%), five remain active (55.6%); parked does not mean delivered. Supporting PR40 is outside the nine-issue total.

| Issue | Status | PR | Merge SHA |
|---|---|---|---|
| #9 Kafka/Event Hubs metadata | Merged | [32](https://github.com/sqllocks/shape/pull/32) | `35addfc4c310b85a62cbe7f315e1d50fb8e426f8` |
| #10 Kafka/Event Hubs formats | Implemented, ready, independently approved; CI hold pending shared repair | [34](https://github.com/sqllocks/shape/pull/34) | None |
| #11 dbt | Implemented, ready, independently approved; CI hold pending shared repair | [29](https://github.com/sqllocks/shape/pull/29) | None |
| #12 Eventhouse | Implemented, ready, independently approved; CI hold pending shared repair | [30](https://github.com/sqllocks/shape/pull/30) | None |
| #13 Semantic measures/roles | Implemented, ready, independently approved; CI hold pending shared repair | [33](https://github.com/sqllocks/shape/pull/33) | None |
| #17 Iceberg | Implemented, ready, independently approved; Windows CI still running at last read | [31](https://github.com/sqllocks/shape/pull/31) | None |
| #6 Nested profiling | Parked: comparison-contract decision | None | None |
| #14 BigQuery | Parked: source interface dependency absent from main | None | None |
| #20 Frozen comparison | Parked: recording/comparison-contract decision | None | None |

All five feature heads and branches are recorded in [state.md](state.md). Their existing reviews remain bound to those heads. Canceled checks are not passes. No open feature PR has a complete green exact-head gate established.

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

The native joint-heavy functional selection missing from CI passed in every implemented lane. No live account success is claimed.

PR32 merged source head `91ef6200266bf962246d1553356b26a442be2303` had every triggered workflow green:

- [CI](https://github.com/sqllocks/shape/actions/runs/39147911536)
- [Documentation](https://github.com/sqllocks/shape/actions/runs/39147911936)
- [DCO](https://github.com/sqllocks/shape/actions/runs/39147911789)
- [Security](https://github.com/sqllocks/shape/actions/runs/39147911639)
- [Container](https://github.com/sqllocks/shape/actions/runs/39147911784)
- [Action self-test](https://github.com/sqllocks/shape/actions/runs/39147911556)

The separate post-merge main CI run was unfinished at the last successful read:20 successful jobs,7 running, no observed failures. That is not a completed main-green claim.

## Shared repair and resumption

[PR40](https://github.com/sqllocks/shape/pull/40), head `3f6254768085d6f96683550c8939998170be989f`, is ready and independently APPROVED. It updates certifi to2026.10.19 plus the generated docs pin, uses pinned Playwright Chromium, and installs that browser before Documentation verification. No existing job, gate, assertion, dependency range, project version or tolerance was removed or loosened.

Targeted advisory audit: old version exit1 with GHSA-6q48-6c7w-9gw2 → fixed version exit0, no known vulnerabilities. Branding acceptance:1 passed in30.95s. Lock/export checks, Ruff/format/mypy/wording and workflow YAML parsing passed. CI caught and prompted correction of the generated docs pin, then the missing browser prerequisite; final revision was independently re-reviewed. Last successful final-head snapshot:14 passing checks,16 running, no reported failures. Further status is unavailable because credentials failed.

After credentials are restored, re-read prompt/state, verify current main and exact-head CI, merge PR40 only when all gates are green, then refresh the five feature lanes in parallel. Preserve additive changelog/requirements entries. Run targeted local tests only for actual code conflicts, rebind independent reviews, and merge promptly on complete fresh CI. Recheck the BigQuery dependency on main. Do not restart historical local full-suite workers.

## Pending Owner/service actions

- #9: Kafka/Event Hubs account round trips; nightly broker emulator coverage.
- #10: broker/account round trips and Azure Schema Registry Avro round trip; nightly emulator coverage. Existing Azure scope approval is already resolved.
- #11: no account-dependent live requirement; actual file-based DuckDB/dbt acceptance passed.
- #12: Kusto/Eventhouse emulator and account read-back.
- #13: Owner supplies a model with a measure and row-level-security role, expected metadata and role-visible rows. Existing live test covers relationships/table reads; add measure/role read-back assertions after fixture approval. No role deployment or live query occurred.
- #17: REST/MinIO emulator validation when images are available; no account-live scope or Windows pass is claimed.

## Parked questions

Full options/evidence are in [questions.md](questions.md).

- #20: authorize an explicit noncomparability contract for newly supported nested inputs, time-only calendar context and intentionally replaced synthetic labels, or retain the current strict red result. Recommendation: explicit tested classification while preserving recordings and numeric thresholds. Pass-rule changes remain prohibited without Owner authorization. [Issue comment](https://github.com/sqllocks/shape/issues/20#issuecomment-6252771902).
- #6: defer Wanted5 or authorize its unsupported-baseline classification together with #20. Recommendation: explicit authorized classification; preserve the intentionally merged nested support. [Issue comment](https://github.com/sqllocks/shape/issues/6#issuecomment-6299574643).
- #14: wait for W9-01's source interface on main or authorize a scope/interface change. Recommendation: wait and reuse the merged interface. Decimal-boundary policy is already resolved and is not an outstanding question. [Issue comment](https://github.com/sqllocks/shape/issues/14#issuecomment-6299575080).

The three parked issues were rechecked before the credential failure; no new decision had arrived. Lead A branches/issues and Premium were not modified.

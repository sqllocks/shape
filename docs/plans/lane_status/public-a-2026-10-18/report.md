# Public A report — 2026-10-26, final integration CI in progress

**3/7 merged (42.9% done), 4/7 remaining (57.1%). All four remaining issues are in one ready PR; none is parked.** This replaces the superseded October 20 credential-handoff report. Credentials work, all local implementation handoffs are pushed, and the Owner has resolved the policy decisions.

The Owner requested one final integration PR and full CI at the end. [PR #49](https://github.com/sqllocks/shape/pull/49), exact head `23c90c0f6fff5f6ed3d418b9a2986677ecde83f3`, contains #4/#5/#7/#8, the reviewed CI acceleration and a minimal cancellation-race repair exposed by fresh main CI. It is ready, independently reviewed and based on current main `314749d37aba9141a2ff68a33cfdb91e8a87cca5`. Full CI is pending; no merge or green result is claimed for #49.

| Issue | Status / PR | Actual merge SHA | Targeted acceptance |
|---|---|---|---|
| #2 W9-01 PostgreSQL/MySQL profiling | MERGED [#45](https://github.com/sqllocks/shape/pull/45) | `43d3f9aebeb8860489bc46f475203ba2faa8212e` | Tests-first acceptance; 727 affected cases per kernel. |
| #3 W9-02 Snowflake/Databricks profiling | MERGED [#37](https://github.com/sqllocks/shape/pull/37) | `314749d37aba9141a2ff68a33cfdb91e8a87cca5` | Tests-first sensitive-artifact acceptance; 195 integration cases per kernel and two documentation pages. |
| #16 W9-15 Oracle | MERGED [#39](https://github.com/sqllocks/shape/pull/39) | `3b19d05b940816a74a0ce1ec177365138ce0ed32` | 117 targeted cases, 96 Python cases, 46 sink cases and three actual Oracle Free23 emulator cases. |
| #4 W9-03 type fidelity | READY in [#49](https://github.com/sqllocks/shape/pull/49) | Pending | 213 focused cases per kernel; final type/writer/DDL selection 516 per kernel. Bridge five-column acceptance 2 red → green; expanded Bridge selection 695 green. Dictionary preview 5 red/1 control → 100 affected cases per kernel. UNIQUE validation 8 red/6 controls → 223 per kernel. |
| #5 W9-04 constraints | READY in [#49](https://github.com/sqllocks/shape/pull/49) | Pending | Tests-first compiler/writer acceptance, real local PG/MySQL enforcement recorded in lane; final shared integration 1040 affected cases per kernel before type composition, then focused affected selections. DuckDB inline parent-first exception implemented under Owner ruling. |
| #7 W9-06 native nested types | READY in [#49](https://github.com/sqllocks/shape/pull/49) | Pending | Tests-first sink/module and native/JSON roundtrips; native preview 3 red/JSON control → 326 final focused cases per kernel in combined runtime. |
| #8 W9-07 Delta features | READY in [#49](https://github.com/sqllocks/shape/pull/49) | Pending | Amended Wanted coverage; integration 11 red/5 controls plus six extra red/one control → 40 green; both-kernel affected and byte-compatibility checks recorded in lane. |

Final post-conflict composition passes **280 tests in each kernel**, covering UNIQUE validation, Bridge 1.3 and frozen 1.0/1.1/1.2 replay. The authorized combined nonfrozen corpus matches **390 files and 28 named refusals**. No inserted values or non-SQL artifacts changed beyond the approved type/constraint corpus deltas. Existing frozen Bridge blobs are unchanged; all **207** newly archived 1.2 schema/vector blobs match the published pre-type baseline byte for byte. Stable/plugin API checks report zero breaking changes. Ruff, formatting, mypy, wording and conformance checks pass. No local full pytest or make check was run as merge evidence.

Independent reviewers approved the final composition, Delta product changes, CI acceleration and cancellation repair. All 118 new commits were checked for DCO sign-off. Final merge still requires every triggered exact-head CI job green and current-main ancestry.

## CI evidence

Merged PR #45 exact-head green workflows: [CI](https://github.com/sqllocks/shape/actions/runs/39528689508), [Documentation](https://github.com/sqllocks/shape/actions/runs/39528690005), [Security](https://github.com/sqllocks/shape/actions/runs/39528689826), [Container](https://github.com/sqllocks/shape/actions/runs/39528689918), [DCO](https://github.com/sqllocks/shape/actions/runs/39528689413), [action-selftest](https://github.com/sqllocks/shape/actions/runs/39528689400).

Merged PR #39 exact-head green workflows: [CI](https://github.com/sqllocks/shape/actions/runs/40226721627), [Documentation](https://github.com/sqllocks/shape/actions/runs/40226721586), [Security](https://github.com/sqllocks/shape/actions/runs/40226721653), [Container](https://github.com/sqllocks/shape/actions/runs/40226721599), [DCO](https://github.com/sqllocks/shape/actions/runs/40226721600), [action-selftest](https://github.com/sqllocks/shape/actions/runs/40226721595). Fresh merged-head [CI](https://github.com/sqllocks/shape/actions/runs/40235435384), [Documentation](https://github.com/sqllocks/shape/actions/runs/40235435374) and [Security](https://github.com/sqllocks/shape/actions/runs/40235435418) also passed.

Merged PR #37 exact-head green workflows: [CI](https://github.com/sqllocks/shape/actions/runs/40243748742), [Documentation](https://github.com/sqllocks/shape/actions/runs/40243748741), [Security](https://github.com/sqllocks/shape/actions/runs/40243748751), [Container](https://github.com/sqllocks/shape/actions/runs/40243748808), [DCO](https://github.com/sqllocks/shape/actions/runs/40243748750), [action-selftest](https://github.com/sqllocks/shape/actions/runs/40243748743). Fresh main macOS 3.11 subsequently exposed a pre-existing cross-store cancellation lost-update race; PR49 includes its deterministic repair rather than retrying to hide the failure.

PR #49 **pending, not green evidence**: [CI](https://github.com/sqllocks/shape/actions/runs/40259512617), [Wheels](https://github.com/sqllocks/shape/actions/runs/40259512606), [Security](https://github.com/sqllocks/shape/actions/runs/40259512623), [Container](https://github.com/sqllocks/shape/actions/runs/40259512521), [Documentation](https://github.com/sqllocks/shape/actions/runs/40259512533), [DCO](https://github.com/sqllocks/shape/actions/runs/40259512500), [action-selftest](https://github.com/sqllocks/shape/actions/runs/40259512531).

## Owner decisions and remaining live actions

All policy questions are answered: indexed T-SQL strings require explicit widths; DuckDB uses its supported parent-first inline-FK exception; #8 integrates the final #5 helper; frozen Bridge 1.1/1.2 remain historical while CLI/API/new 1.3 use corrected types. No frozen baseline was amended. No unresolved contract question or parked lane remains.

No account-dependent live test is claimed passing. Owner accounts/settings are still needed for PostgreSQL/MySQL source checks (#2), Snowflake/Databricks profiling (#3), cloud type/timezone readback (#4), cloud constraint enforcement (#5), cloud nested readback (#7), six Delta feature tables using SHAPE_LIVE_DELTA_URI and normal storage credentials (#8), and Oracle catalog/MAX_STRING_SIZE/account rights (#16). Actual local emulators are distinct from account-dependent live evidence. SQL Server emulator follow-up remains unexecuted locally; no SQL Server pass is claimed.

The historical maintenance PR46 Windows corrupted-Delta diagnostic failure remains unexplained: [job](https://github.com/sqllocks/shape/actions/runs/40213846736/job/121231082157). Its one diagnostic rerun and subsequent Windows heads passed, which does not establish the original cause. No test or assertion was weakened.

No tags, releases, publishing, protected-branch pushes, repository settings or other-session branches were changed. The Premium Contract Lead questions path is absent in this public repository; recheck after the final merge batch.

# Public A handoff report — 2026-10-20

Stopped at the prompt's credential hard stop at 20:33 UTC. GitHub returned HTTP 401 “Bad credentials”; repeated git pushes failed to read Username. No authentication or configuration changes were attempted. All agents stopped and local work is preserved.

**0 of 7 assigned issues merged: 0% done, 100% still unmerged.** Four implementation PRs are ready and independently reviewed (57.1%); three issues are parked (42.9%). No merge SHA or green merged-head CI evidence exists for any assigned issue.

## Issue status

| Issue | Status and preserved head | PR | Targeted evidence |
|---|---|---|---|
| #2 W9-01 PostgreSQL/MySQL profiling | Ready, reviewed; `f16c2c68226402a5ee0f9f3b65dbf953dfff2f92` | [#35](https://github.com/sqllocks/shape/pull/35) | Acceptance red against base, then green; 727 database/source/privacy/unchanged SQL Server cases in each kernel. Changed documentation examples pass. |
| #3 W9-02 Snowflake/Databricks profiling | Ready, reviewed; `e8f9e5b0d2b4cbbe106a59eea60bfe2fe43f8651` | [#37](https://github.com/sqllocks/shape/pull/37) | Eight sensitive-artifact failures red→green; 1,278 cloud/bridge cases default kernel, 76 cloud cases Python kernel. Actual plugin listings updated. |
| #4 W9-03 type fidelity | Parked; `9869529e9a6bc4c03fd34feb6c72b718646d4fc7` | None | 233 focused cases pass. Corpus audit preserves zero INSERT/value differences; four additional string-key refusals need Owner authorization. |
| #5 W9-04 constraints | Parked and incomplete; last pushed `e01c079f9d1a0a92340784e70a97b7c135878cfc`; six dirty files preserved | None | Compiler and writer tests-first checkpoints pass; actual PG16/MySQL8.4 enforcement, string-mode, retained-row and reference safeguards pass. Latest affected-module run: **439 passed, 8 failed**; not merge-ready. |
| #7 W9-06 nested types | Ready, reviewed; `440540c591499eca0827dccef9d31080dd6e6cef` | [#38](https://github.com/sqllocks/shape/pull/38) | 257 acceptance/module cases plus 380 affected cases pass; 176 targeted Python cases pass. Real local DuckDB/Delta roundtrips and exact Delta timestamp boundaries pass. Final strict mypy covers all 15 changed plugin source modules. |
| #8 W9-07 Delta features | Parked solely on #5; reviewed feature checkpoint `9bd824e8befd343c6bfd0a1a7342f8a26c945c57` | None | Actual #5/#7 branches stacked. Both unfiltered kernel selections: **122 passed**. Independent integration: 17 passed. Supported native/JSON roundtrips remain; unsupported combinations receive specific pre-storage refusals under existing Wanted rules. |
| #16 W9-15 Oracle | Ready, reviewed; `ad3d2647d4e1aaf790d007e9dc0abb9fdfc84a32` | [#39](https://github.com/sqllocks/shape/pull/39) | 117 targeted integration cases; 46 sink cases after final fixes; 96 Python acceptance cases. All 3 actual Oracle Free23 emulator cases pass, including decimal(38,10), BLOB/timestamp9 and dataset identity. |

Ruff, formatting, typing and public-wording checks passed at the reviewed ready heads. Local full pytest/make check runs were retired under the Owner throughput directive. No skip, xfail, test deletion, gate or tolerance change was introduced to resolve blockers.

## CI and shared merge blocker

The baseline locks certifi2026.7.22, affected by GHSA-6q48-6c7w-9gw2; the reported fixed version is2026.10.19. The security audit failed on both an implementation PR and the docs-only lead PR. Lead B already owns [fix PR #40](https://github.com/sqllocks/shape/pull/40); its branch was not edited, refreshed or merged by Public A. At the last successful read it remained open with 13 checks successful, 17 running and no failures.

Unfinished CI on the known-vulnerable owned heads was cancelled to release capacity for the shared fix. These runs are **not merge evidence**. After the fix reaches main, merge main once into the owned ready branches, push and require every triggered job green on each exact new head.

Last current-head CI links, unsuccessful or cancelled on the vulnerable baseline:

- #35: [CI](https://github.com/sqllocks/shape/actions/runs/39483775008), [security failure](https://github.com/sqllocks/shape/actions/runs/39483775203).
- #37: [CI](https://github.com/sqllocks/shape/actions/runs/39485790956).
- #38: [CI](https://github.com/sqllocks/shape/actions/runs/39491557920).
- #39: [CI](https://github.com/sqllocks/shape/actions/runs/39487056238).
- Owner directive row: ready, reviewed [PR #36](https://github.com/sqllocks/shape/pull/36), head `5ae97349db0aa9531619d3a30ffe693bb0330258`; [CI](https://github.com/sqllocks/shape/actions/runs/39483639359) also awaits the shared fix.

## Owner decisions still outstanding

- **#4:** authorize named preconnection refusal for undeclared indexed-string widths and the four exact additional corpus refusals. Recommended over silently guessing450. Alternative: explicitly permit a documented bounded-key exception. [Recorded question](https://github.com/sqllocks/shape/issues/4#issuecomment-6298211776).
- **#5:** permit DuckDB's supported parent-first inline foreign keys as a documented exception to the required post-creation FK phase. Recommended; installed DuckDB1.5.6 cannot perform the literal ALTER. Alternatives: remove DuckDB from scope or await backend support. [Recorded question](https://github.com/sqllocks/shape/issues/5#issuecomment-6298361966).
- **#8:** #5 must resolve and merge first. Its actual safe AST helper is already integrated; there is no additional feature-composition Owner question. [Dependency parking record](https://github.com/sqllocks/shape/issues/8#issuecomment-6298362470).

## Pending live validation

No account-dependent live pass is claimed.

- #2: real PostgreSQL/MySQL account roundtrips.
- #3: Snowflake and Databricks account roundtrips with Owner settings.
- #4: account-dependent type/zone/length readback after the Owner decision.
- #5: remaining SQL Server/dialect acceptance and Snowflake/Databricks/Fabric account validation. Passed local PG/MySQL tests are emulator tests, not live-account evidence.
- #7: PostgreSQL/MySQL nested emulator coverage remains distinct from its fake acceptance; live Snowflake/Databricks awaits Owner accounts. Local DuckDB/Delta passed.
- #8: Fabric feature tables await Owner environment; local Delta feature tests passed.
- #16: real Oracle19+/Autonomous TLS/wallet/account validation remains pending. Oracle Free23 local emulator passed.

## Preserved local work and resumption

The final scratch state/report cannot be pushed while authentication fails. Last confirmed remotely pushed scratch commit: `30974d5`; local state commit `7dd2c2a` and this report are newer.

#5 preserves dirty `_base.py`, `mysql.py`, `postgres.py`, `testing.py`, `tests/test_constraint_emulator.py` and `tests/test_write_constraints.py` under `/workspace/public-a-W9-04/plugins/shape-databases`. New reference safeguards have four fake and four actual-emulator green cases. Latest broader selection has two seed rerun regressions from default constraint derivation and six source CLI/bridge failures needing diagnosis. Remaining work includes existing-table verification, SQL Server/Fabric/DuckDB hooks, retail/chaos coverage and final docs/traceability. No further edits occurred after the hard stop.

Oracle's temporary container/image was retired after its final successful tests to free disk. PG/MySQL services belong to compose project `shape-w9-04`; preserve or retire only that owned project as appropriate.

After authentication is restored: read the prompt/state, publish the local handoff, preserve and resume #5's dirty work, absorb the other-owned shared fix from main, then run exact-head CI and merge promptly. Required implementation order is #35 before #37/#39, and #37 before #38. #8 still requires #5 first. User requests quantified updates about every five minutes and immediately on merge/blocker changes.

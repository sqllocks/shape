# Public B interim report — 2026-10-25

Work continues. **4 of9 assigned issues merged (44.4%);5 remain unmerged (55.6%).** All9 implementations have independent approval. The five remaining implementations are consolidated in ready PR48 under the latest Owner speed directive. Final independent combined review is approved; required CI is running. Earlier per-lane CI stages are superseded. None is parked or awaiting an Owner decision. Supporting certificate PR40 is outside the9-issue denominator. No live-account success is claimed.

Current main is `314749d37aba9141a2ff68a33cfdb91e8a87cca5` after another session merged37. PR48 contains it. Our latest merge, dbt main `90fbff999bc95e255b3dbd90b76278441a735b41`, passed all triggered [CI](https://github.com/sqllocks/shape/actions/runs/40243508314), [Docs](https://github.com/sqllocks/shape/actions/runs/40243508311) and [Security](https://github.com/sqllocks/shape/actions/runs/40243508316), including all27corejobs.

## Active combined delivery PR

[PR48](https://github.com/sqllocks/shape/pull/48) is ready and contains all five remaining implementations, exact signed/pushed head `b86a707734ab7c286880eb5c37b27d2cebb44383`, based on current main314749d. Full independent Wanted/security/scope review APPROVED this exact head; a second reviewer approved broker/FRZ preservation. All required hosted workflows are running on this final head. Original per-lane merge stages below are historical; the combined PR replaces repeated full CI after each separate merge.

Targeted integration evidence: broker223+6cases eachkernel, cloud12red→90green eachkernel, BigQuery92green eachkernel, Iceberg4red→136green eachkernel, asyncBigQuery9red plus linear-time timeout red→70combinedsecuritygreen eachkernel. Ruff/format, mypy711 and publicwording pass. Actual191plugin transcripts match exactly. Original acceptance proofs and pendinglive actions remain recorded. #20 stays open until required dispatched-main Nightly and a subsequent acceptance PR.

## Issue status

| Issue | Status / PR | Original included source head or merge SHA |
|---|---|---|
| #9 W9-08 message metadata | Merged [32](https://github.com/sqllocks/shape/pull/32) | merge `35addfc4c310b85a62cbe7f315e1d50fb8e426f8` |
| #12 W9-11 Eventhouse | Merged [30](https://github.com/sqllocks/shape/pull/30) | merge `5c6d46ff1703db9fd2307bc4ebd4fb087e9af733` |
| #13 W9-12 semantic measures/roles | Merged [33](https://github.com/sqllocks/shape/pull/33) | merge `404fed823b271525b8802254312ded28f1c5aaa8` |
| #11 W9-10 dbt | Merged [29](https://github.com/sqllocks/shape/pull/29); all4sourceworkflowsgreen | merge`90fbff999bc95e255b3dbd90b76278441a735b41` |
| #10 W9-09 binary formats | Included in [48](https://github.com/sqllocks/shape/pull/48); original reviewed PR [34](https://github.com/sqllocks/shape/pull/34) | `9b3e7375694514faeefa779c11f05bb13e1a8e2b` |
| #17 W9-16 Iceberg | Included in [48](https://github.com/sqllocks/shape/pull/48); original reviewed PR [31](https://github.com/sqllocks/shape/pull/31) | `24a145795a0f33f95d125fa0a7123f76b959d780` |
| #20 FRZ-01 | Implementation in [48](https://github.com/sqllocks/shape/pull/48), original [42](https://github.com/sqllocks/shape/pull/42); required main Nightly outstanding | `53bc24958788830669a00d47e9048c76418a475d` |
| #6 W9-05 nested profiling | Included in [48](https://github.com/sqllocks/shape/pull/48); original reviewed PR [43](https://github.com/sqllocks/shape/pull/43) | `e5d6dd3d8351854b7967f145f5c7aeb6c1c32f5e` |
| #14 W9-13 BigQuery | Included in [48](https://github.com/sqllocks/shape/pull/48); original reviewed PR [47](https://github.com/sqllocks/shape/pull/47) | `8228f87e699bb3524bf26f4fa4fefc6d0449856e` |

The final combined head contains current main and is DCO-signed/pushed. metadata_oct25 approved its complete Wanted/security/scope; formats_oct25 approved broker/FRZ preservation. All original source descriptions point to48. Clean base refreshes did not rerun local tests; actual interface conflicts received targeted red/green coverage in both kernels.

## Targeted acceptance evidence

These are meaningful acceptance/changed-module selections from the lane records, not distinct test totals to add together. CI is the full-suite gate. No local full `make check` or full pytest was used after the Owner's speed directive.

| Issue | Red evidence | Green evidence |
|---|---|---|
| #9 | `15 failed,6 passed`; metadata validation `4 failed,1 passed` |271 targeted in each kernel; validation5passed |
| #10 |28initial failures; later dead-letter regression2failures |345passed,38deselected in each kernel after actual code-conflict integration; dead-letter2passed |
| #11 |All10 initial acceptance cases failed; ratio/Jinja regressions red |142offline eachkernel;8 actual DuckDB/dbt acceptance;34focused Wanted/conformance eachkernel |
| #12 |Missing source module/CLI hook failed acceptance |46passed eachkernel |
| #13 |40failed,7passed against base |171focused eachkernel; additive metadata-kind schema/editor compatibility selection passed |
| #17 |18initial missing-module failures; new option-boundary2 genuine failures without guard (initial test-import typo excluded from proof) |536focused eachkernel;16 Windows FileIO adapter cases with minimum SDK0.9 and current0.12; static/mypy/trace green |
| #6 |14initial failures eachkernel; compatibility16failed/92passed; DB depth4red eachkernel; Oracle depth5red eachkernel |221focused including1GB memory cases with unchanged256MiB growth budget; compatibility170eachkernel; DB/depth/privacy258eachkernel; latest Oracle133eachkernel; static green |
| #20 |11category initial red; old generation verifier14flags on identical outputs; old profile/timing5red; Windows path1failed/1passed |138complete FRZ targeted eachkernel; later35 verifier/date/timing eachkernel; actual four value workloads pass unchanged comparisons |
| #14 |38initial/56expanded acceptance red on untouchedc1ff; fragment4red, display3red, first-load cleanup1red; decimal load request and13 SDK parameter cases red |Latest88BQ eachkernel; initial276changed-module eachkernel; actual SDK configuration/field metadata/Parquet wire proofs; strict adapter/core mypy, Ruff, public checks; targeted authoring+DBT documentation pages2passed |

No skip, xfail, deleted or weakened assertion/gate/tolerance was introduced. Existing explicit offline marker selections are stated rather than counted as passing tests. No recordings were changed. Actual remote/live-account validation remains pending below.

BigQuery review repairs preserve strict reference-only credentials and preconnection fragment refusal, safe remote-confirmation output, private temporary staging and cleanup, confirmed stream progress, and explicit `decimal_target_types=[NUMERIC,BIGNUMERIC]` without STRING fallback. Explicit SDK precision/scale/maxLength is retained while `INFORMATION_SCHEMA` remains authoritative for column order/nullability; this makes no undocumented claim that `DATA_TYPE` always omits parameters.

## Green CI evidence for merged work

PR32 exact reviewed source91ef620 had all triggered workflows green: [CI39147911536](https://github.com/sqllocks/shape/actions/runs/39147911536), [Docs39147911936](https://github.com/sqllocks/shape/actions/runs/39147911936), [DCO39147911789](https://github.com/sqllocks/shape/actions/runs/39147911789), [Security39147911639](https://github.com/sqllocks/shape/actions/runs/39147911639), [Container39147911784](https://github.com/sqllocks/shape/actions/runs/39147911784), [selftest39147911556](https://github.com/sqllocks/shape/actions/runs/39147911556). Merged main35addfc4 also passed [CI39483030770](https://github.com/sqllocks/shape/actions/runs/39483030770), [Docs39483030734](https://github.com/sqllocks/shape/actions/runs/39483030734), [Security39483030728](https://github.com/sqllocks/shape/actions/runs/39483030728).

PR30 exact reviewed sourcecad8cfd had all six workflows green: [CI39504103202](https://github.com/sqllocks/shape/actions/runs/39504103202), [Docs39504103156](https://github.com/sqllocks/shape/actions/runs/39504103156), [DCO39504103258](https://github.com/sqllocks/shape/actions/runs/39504103258), [Security39504103173](https://github.com/sqllocks/shape/actions/runs/39504103173), [Container39504103180](https://github.com/sqllocks/shape/actions/runs/39504103180), [selftest39504103224](https://github.com/sqllocks/shape/actions/runs/39504103224). Its merged-head main5c CI was automatically canceled by the subsequent main move, never a completed pass.

PR33 exact reviewed source190c763 had32successful checks, only two conditional publish skips, and all six workflows green: [CI39517317496](https://github.com/sqllocks/shape/actions/runs/39517317496), [Docs39517317492](https://github.com/sqllocks/shape/actions/runs/39517317492), [DCO39517317485](https://github.com/sqllocks/shape/actions/runs/39517317485), [Security39517317518](https://github.com/sqllocks/shape/actions/runs/39517317518), [Container39517317531](https://github.com/sqllocks/shape/actions/runs/39517317531), [selftest39517317589](https://github.com/sqllocks/shape/actions/runs/39517317589). Merged main404fed8 also passed [CI39528275403](https://github.com/sqllocks/shape/actions/runs/39528275403), [Docs39528275422](https://github.com/sqllocks/shape/actions/runs/39528275422), [Security39528275442](https://github.com/sqllocks/shape/actions/runs/39528275442).

Prior mainc1ff completed [CI40226657799](https://github.com/sqllocks/shape/actions/runs/40226657799), [Docs40226657904](https://github.com/sqllocks/shape/actions/runs/40226657904), [Security40226657845](https://github.com/sqllocks/shape/actions/runs/40226657845), [Wheels40226657792](https://github.com/sqllocks/shape/actions/runs/40226657792). Earlier3b19 main also completed all three triggered workflows successfully as noted above.

Supporting [PR40](https://github.com/sqllocks/shape/pull/40) merged as53d3d0c after exact approved3f625476 had all4workflows green: [CI39491203122](https://github.com/sqllocks/shape/actions/runs/39491203122), [Docs39491203111](https://github.com/sqllocks/shape/actions/runs/39491203111), [Security39491203106](https://github.com/sqllocks/shape/actions/runs/39491203106), [DCO39491202974](https://github.com/sqllocks/shape/actions/runs/39491202974). Targeted audit oldexit1 GHSA-6q48-6c7w-9gw2→newexit0/no vulnerabilities; browser branding1passed. Locked certifi artifacts/docs export and prepared Playwright/browser installation were repaired without gate/range changes. Later canceled main53 CI is not called green.

## Frozen comparisons: passes and errors reported separately

These are actual targeted results from the reviewed fix; **they are not the required post-merge main Nightly acceptance**.

| Workload with recorded values | Targeted result |
|---|---|
|`generate:healthcare:medium`|PASS80/80columns, all original clausesa–f|
|`generate:capital_markets:medium`|PASS64/64columns, all original clausesa–f|
|`profile:edge/x_time_only.csv`|PASS0mismatches/exit0|
|`profile:edge/x_time_ampm.csv`|PASS0mismatches/exit0|

These two workloads are **NOT COMPARABLE, never passes and never timing credit**:

| Workload | Recorded0.9.1 reason |
|---|---|
|`profile:edge/x_pq_list.parquet`|TypeError on list-valued Parquet; no baseline profile values|
|`profile:edge/x_pq_nested.parquet`|TypeError on struct-valued Parquet; no baseline profile values|

The reviewed committed registry is `benchmarks/vs_refengine/comparison_contract.py` in PR42. Inputs/recordings are authenticated. Name aliases use original/current reference lineage, never numeric values, frequencies or output ranks. Scoped implicit parser date replay uses independently authenticated capture context before computation, preserves explicit dates and restores state; no output masking or threshold changes. No re-recording.

#20 remains open. After42 merges, dispatch Nightly on main and require all five parity shards, `parity-publish` and `sqlserver-e2e` green. Link the qualifying dispatched run and close20 only through a subsequent acceptance PR. No qualifying run has been dispatched yet. No additional Owner comparison decision is pending.

## Remaining work and Owner actions

Merge48 when all seven triggered workflows succeed on its exact reviewed head and it contains freshly verified current main: [CI40253591021](https://github.com/sqllocks/shape/actions/runs/40253591021), [Wheels40253591070](https://github.com/sqllocks/shape/actions/runs/40253591070), [Docs40253591136](https://github.com/sqllocks/shape/actions/runs/40253591136), [DCO40253591052](https://github.com/sqllocks/shape/actions/runs/40253591052), [Security40253591186](https://github.com/sqllocks/shape/actions/runs/40253591186), [Container40253591239](https://github.com/sqllocks/shape/actions/runs/40253591239), [selftest40253591119](https://github.com/sqllocks/shape/actions/runs/40253591119). DCO/security/selftest have passed; remaining gates are active. No obsolete canceled run is passing evidence. Refresh only this combined branch if main moves. After merge, dispatch required main Nightly; close20 only through its subsequent acceptance PR after the qualifying green run.

BigQuery's former source dependency is resolved: PR35 closed unmerged and was superseded by [PR45](https://github.com/sqllocks/shape/pull/45), mergedOctober25. Its fresh lane has none of the old journal branch's unmerged ancestry. The Owner decimal rule remains0<=scale<=precision before connecting, with the column named in the error. PR48 already includes and tests the shared depth interface across the combined sources.

Pending external validation, none claimed passing:

- #9/#10: Kafka/Event Hubs account round trips; broker emulators; Azure Schema Registry Avro round trip (scope approval already resolved).
- #11: no account-dependent live requirement; actual offline DuckDB/dbt acceptance passed.
- #12: Kusto/Eventhouse emulator and account read-back.
- #13: Owner measure/role fixture with expected metadata and role-visible rows. Existing live tests cover relationships/table reads; no role deployment or live query occurred.
- #17: REST/MinIO emulator images and validation; no account-live pass claimed.
- #14: O14 settings for live generate→write→profile and dataset identity agreement; no real BigQuery account used.

No implementation is parked, and no new Owner question is needed. Lead A branches/issues and Premium were not edited. All owned commits are pushed. Current exact heads, merge guards, agent ownership and holds are in [state.md](state.md). This remains an interim report until all assigned issues are merged or genuinely parked.

## Fourth assigned issue delivered

PR29 exactreviewede8bcc73 completed30successful checks+onlyconditionalDocspublishskip; all4workflowsSUCCESS: [CI40235648525](https://github.com/sqllocks/shape/actions/runs/40235648525), [Docs40235648520](https://github.com/sqllocks/shape/actions/runs/40235648520), [DCO40235648542](https://github.com/sqllocks/shape/actions/runs/40235648542), [Security40235648550](https://github.com/sqllocks/shape/actions/runs/40235648550). Allgreenobserved23:09:48→exactAPImerge23:09:58 as90fbff9; issue11closed23:09:59. Sourceacceptance10red→142offline+8actualDuckDB/dbtgreen asabove. Fiveothers refreshedsignedpushedandre-reviewed; only31additivechangelogconflict,no productconflict/localrerun. Stage34/31fullCIadvanced,othersheld.4/9delivered44.4%,5/9left55.6%.

## Latest own merged-main verification

Merged dbt main `90fbff999bc95e255b3dbd90b76278441a735b41` passed every triggered workflow, verified October26 at00:47:31UTC: [CI40243508314](https://github.com/sqllocks/shape/actions/runs/40243508314), [Docs40243508311](https://github.com/sqllocks/shape/actions/runs/40243508311), [Security40243508316](https://github.com/sqllocks/shape/actions/runs/40243508316). All27corejobs passed. All remaining implementations are now consolidated in ready, reviewed PR48; its final exact-head CI remains required.


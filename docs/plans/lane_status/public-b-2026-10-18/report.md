# Public B interim report — 2026-10-25

Work continues. **3 of9 assigned issues merged (33.3%);6 remain unmerged (66.7%).** All9 implementations have independent approval. Six ready implementation PRs remain; none is parked or awaiting an Owner implementation decision. Supporting certificate PR40 is outside the9-issue denominator. No live-account success is claimed.

Current main is `3b19d05b940816a74a0ce1ec177365138ce0ed32` after Lead A's Oracle PR39. Its [Documentation](https://github.com/sqllocks/shape/actions/runs/40235435374) and [Security](https://github.com/sqllocks/shape/actions/runs/40235435418) passed; [CI](https://github.com/sqllocks/shape/actions/runs/40235435384) is still running, with no reported failure. Prior mainc1ff1c8 completed all triggered workflows successfully. PR29 was refreshed when main advanced; its previous incomplete CI does not qualify for the new head.

## Issue status

| Issue | Status / PR | Exact pushed source head or merge SHA |
|---|---|---|
| #9 W9-08 message metadata | Merged [32](https://github.com/sqllocks/shape/pull/32) | merge `35addfc4c310b85a62cbe7f315e1d50fb8e426f8` |
| #12 W9-11 Eventhouse | Merged [30](https://github.com/sqllocks/shape/pull/30) | merge `5c6d46ff1703db9fd2307bc4ebd4fb087e9af733` |
| #13 W9-12 semantic measures/roles | Merged [33](https://github.com/sqllocks/shape/pull/33) | merge `404fed823b271525b8802254312ded28f1c5aaa8` |
| #11 W9-10 dbt | Ready/reviewed [29](https://github.com/sqllocks/shape/pull/29); current full CI running | `e8bcc73f238588d82c122bed9c5ad0e38b29c4b5` |
| #10 W9-09 binary formats | Ready/reviewed [34](https://github.com/sqllocks/shape/pull/34); full CI held until29 | `2e6caa4afdab03ba35e29a1cb965d6f78eecb27e` |
| #17 W9-16 Iceberg | Ready/reviewed [31](https://github.com/sqllocks/shape/pull/31); full CI/Wheels held until29 | `fa1ee63175ee3d480fe0de1f1472a75ef49f8de3` |
| #20 FRZ-01 | Ready/reviewed [42](https://github.com/sqllocks/shape/pull/42); full CI held until34+31, required main Nightly still outstanding | `16b061c3e976fa4f5823d70ce40d9fd3eaf982e9` |
| #6 W9-05 nested profiling | Ready/reviewed [43](https://github.com/sqllocks/shape/pull/43); full CI held, depends42 | `48738c68487b988c83ab56dc9275627bbd8b5819` |
| #14 W9-13 BigQuery | Ready/reviewed [47](https://github.com/sqllocks/shape/pull/47); full CI held until prior stages | `716e791ba34fc81b9ec5587af32f331451224980` |

All six open heads contain currentmain and are DCO-signed/pushed. Independent exact approvals: eventhouse_oct25 for29/42; metadata_oct25 for31/34/43/47. The latest refresh had only additive conflicts except43's necessary Oracle depth integration. Clean refreshes did not rerun local tests. Every open PR is ready, not draft.

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

Prior mainc1ff completed [CI40226657799](https://github.com/sqllocks/shape/actions/runs/40226657799), [Docs40226657904](https://github.com/sqllocks/shape/actions/runs/40226657904), [Security40226657845](https://github.com/sqllocks/shape/actions/runs/40226657845), [Wheels40226657792](https://github.com/sqllocks/shape/actions/runs/40226657792). Current3b19 main is separately pending as noted above.

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

Merge29 when its [current CI40235648525](https://github.com/sqllocks/shape/actions/runs/40235648525) and all exact-head workflows pass; then advance34/31, then42, then43, then47. Every main move requires fresh signed/pushed heads and independent reviews. Current later full runs are intentionally canceled/held; **none is passing evidence**. Their retained documentation/security/DCO/container/action checks are green on current heads, verified21:46UTC. Their full gates must still run successfully before merge.

BigQuery's former source dependency is resolved: PR35 closed unmerged and was superseded by [PR45](https://github.com/sqllocks/shape/pull/45), mergedOctober25. Its fresh lane has none of the old journal branch's unmerged ancestry. The Owner decimal rule remains0<=scale<=precision before connecting, with the column named in the error. After43/main changes, refresh47's shared depth interface and rebind exact review.

Pending external validation, none claimed passing:

- #9/#10: Kafka/Event Hubs account round trips; broker emulators; Azure Schema Registry Avro round trip (scope approval already resolved).
- #11: no account-dependent live requirement; actual offline DuckDB/dbt acceptance passed.
- #12: Kusto/Eventhouse emulator and account read-back.
- #13: Owner measure/role fixture with expected metadata and role-visible rows. Existing live tests cover relationships/table reads; no role deployment or live query occurred.
- #17: REST/MinIO emulator images and validation; no account-live pass claimed.
- #14: O14 settings for live generate→write→profile and dataset identity agreement; no real BigQuery account used.

No implementation is parked, and no new Owner question is needed. Lead A branches/issues and Premium were not edited. All owned commits are pushed. Current exact heads, merge guards, agent ownership and holds are in [state.md](state.md). This remains an interim report until all assigned issues are merged or genuinely parked.

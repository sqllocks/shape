# ISS2-dbt: issue #44, dbt integration (lane/ISS2-dbt)

Branch `lane/ISS2-dbt`, cut from the lead's integration tree (abd78cb); `origin/build/main-plan` (521310f,
P6-11) merged by a merge commit. Commit prefix `ISS2-dbt:`. No §11 or §2.3 edit (the plan file differs from
`origin/build/main-plan` only by what the integration tree already had), no PR, no issue comment, label or close.
`$REFENGINE_ROOT` was not touched. No D-xx, T-xx, gate or tolerance changed (see "For the lead" for two
points that touch T-08 and T-09). Live Fabric runs are the owner's (O-07) and were **not** run; what needs one is
marked `[VERIFY]`.

Environment: Python 3.11, `~/.venvs/shape` (`pip install -e plugins/shape-domains -e ".[dev,advanced]"
-e plugins/shape-dbt dbt-duckdb`; dbt-core 1.12.5, dbt-duckdb 1.11.0, duckdb 1.5.6), `~/.venvs/shape-fabric`
(`-e '.[dev]' -e plugins/shape-dbt -r tests/demo/fabric/requirements.txt`, unixODBC installed). The sandbox
cannot download GitHub tarballs (403), so `dbt deps` from the package hub did not work here; the dbt tests ran
with `SHAPE_DBT_PACKAGES_FILE` pointing at a `packages.yml` of `local:` entries (git clones of dbt-utils 1.4.1,
dbt-expectations 0.10.9, dbt-date 0.9.2). CI installs from the hub (`packages.yml` of the sample); **that
path was not run here** (the hub index lists `metaplane/dbt_expectations` and `dbt-labs/dbt_utils`).

## Result, requirement by requirement

| # | Requirement | Result |
|---|---|---|
| 1 | `shape from-dbt`: `manifest.json` and `schema.yml` / `sources.yml` into a schema, reusing `from-ddl`'s builder | **Done.** New plugin `plugins/shape-dbt` (`sqllocks-shape-dbt`); `shape.generation.ddl.schema_from_parsed` (and `ParsedTable`, `ParsedColumn`, `ParsedForeignKey`) is the public entry to the same builder `from-ddl` uses (the only core change for this part; `from_ddl` is untouched). |
| 1a | `unique` + `not_null` give a key candidate and non-nullable | Done (single column, or `dbt_utils.unique_combination_of_columns` for a composite key); `tests:` and `data_tests:`, inline and `arguments:` forms read. `test_unique_and_not_null_make_a_primary_key`, `test_a_composite_...`. |
| 1b | `relationships` give a foreign key | Done (`ref()` and `source()` targets; an unselected parent is noted, no key). A key with no `data_type` takes its parent's type. |
| 1c | `accepted_values` give `weighted_enum` | Done; equal weights, or a profile's frequencies (`--profile`). Re-applied after the smart inference so a declared set is never widened. |
| 1d | contract `data_type` gives column type incl. decimal precision and scale | Done: `numeric(p,s)` gives `type: decimal`, `precision`, `scale`; `varchar(n)` gives `max_length`; `NUMBER(38,0)` is an integer. Decimals stay `dtype: float` in profiles (owner 2026-10-02) and the engine writes a double; the seeds sink declares `numeric(p,s)` again. **Generation does not emit Arrow decimal types** (`engine._ARROW_TYPES` maps `decimal` to float64; outside this lane): noted, not changed. |
| 1e | descriptions give metadata | Table description in the schema; column descriptions have no place in `generation-schema-v1.json` (additionalProperties false), so a `<out>.dbt-meta.json` is written beside it and `dbt-seeds --metadata` carries them into the seeds block. |
| 2 | `shape to-dbt-tests`: a contract (v1) or a profile as `schema.yml` tests, packages declared, round-trip test, non-expressible rules documented | **Done.** `not_null`, `unique`, `accepted_values`; `dbt_utils.accepted_range` (min/max); `dbt_utils.not_null_proportion` (max_null_rate; `dbt_expectations` has no `mostly`, checked in 0.10.9's macros); `dbt_expectations` row count, column exists, columns match set; from a profile: mean, stdev and quartile bounds (defaults are the drift defaults). Packages: header comment and `--packages-out`. `--merge` is needed because dbt allows one entry per model (found when the first compiled file failed to parse). The contract format v1 is unchanged. |
| 2a | round trip contract, dbt tests, contract | `test_any_contract_round_trips` (hypothesis, 150 contracts): equal to `expressible(contract)` from the tests alone, and equal to `normalize_contract(contract)` with `meta.shape`; also every layout (models, seeds, sources; `tests`/`data_tests`; `arguments`/inline). `dtype`, `pattern`, `distribution`, `min_true_rate`/`max_true_rate` and non-numeric `min`/`max` are **not expressible** (kept as `meta.shape`, not tested), documented in `docs/DBT.md` and in `totests.NOT_EXPRESSIBLE`. Distribution bounds cannot be in contract v1 and are returned separately. |
| 3 | dbt seeds sink with a `seeds:` block of column types; leading-zero ids as text (#46); size guidance | **Done.** `shape.sinks` `dbt-seeds` (`dbt://PROJECT`) and `shape dbt-seeds`. Text columns are always `varchar` in `config.column_types`; decimals `numeric(p,s)`; four dialects (`ansi`, `duckdb`, `tsql`, `spark`). A seed over 1 MiB is refused unless `--allow-large` (dbt-core hashes a seed only up to `--maximum-seed-size-mib`, default 1, read in 1.12.5's source). Size guidance and a one-run DuckDB timing table are in `docs/DBT.md` (3,750 rows / 0.10 MB: 3.3 s; 37,500 rows / 1.1 MB: 3.4 s; 375,000 rows / 11.8 MB: 5.5 s, `dbt seed` wall time including about 3 s of start-up). Not a benchmark; not Fabric (`[VERIFY]`). Not done: reading the seeds back through Shape's CSV profiler (that is #46, in `lane/ISS2-bugs`); the acceptance test profiles the built tables instead. |
| 4 | Fabric pipeline pattern: documented pattern, sample pipeline, helper reading `run_results.json` and `manifest.json` | **Done locally, `[VERIFY]` live.** `shape dbt-report` and `shape_dbt.report.build_report`: a failed dbt test (resolved to model, column, test through the manifest) and a Shape contract violation and drift change on the same column in one report (`by_column`, Markdown and JSON). Pipeline `integrations/fabric/pipelines/shape_dbt_gate.DataPipeline` (dbt job, then notebook `shape_profile_dbt`, then the gate) from `build_pipelines.py`; the notebook runs after the dbt job *completes* so a failed test still gives the report. RUNBOOK section 13. **`[VERIFY]`: the dbt job activity.** Its type `DbtJob` and property names are my reading of the preview; I could not see a real export. Also: where the dbt job writes `run_results.json`/`manifest.json`, reading Warehouse model tables from a notebook, the exit-value expressions, the dbt 1.11 runtime reading `arguments:`, seed load time on `dbt-fabric`/`dbt-fabricspark` (RUNBOOK 13.1). |
| 5 | dbt-specific code in a plugin; `dbt-core` not a core dependency (T-07) | Done: `plugins/shape-dbt`, dependencies `sqllocks-shape==0.9.0` and `pyyaml>=6` only; dbt is never imported or run by the package; core `pyproject.toml` dependencies untouched. `python -m shape.plugins.kit sqllocks-shape-dbt` conforms (4 commands, 1 sink). |
| 6 | Acceptance: a sample jaffle-shop project, a test that imports it with `from-dbt`, generates, loads seeds, runs `dbt build` against DuckDB in CI, profiles the outputs, checks a compiled test set round-trips | **Done.** `examples/dbt_jaffle_shop` and `plugins/shape-dbt/tests/test_dbt_build.py` (marker `dbt`): `from-dbt`, `dbt-seeds`, `dbt deps`, `dbt build` (31 results: 3 seeds, 3 views, 2 tables, 23 tests, all pass), profile of the two marts from DuckDB, compiled test set round-trips and runs in dbt (`dbt test --select tag:shape`, 40+ tests, all pass), then a drifted model fails several compiled tests (the range, mean, stdev and quartile ones) and one report shows the failed dbt tests, the contract violation and the drift on `orders.amount`. CI: new job `dbt` in `.github/workflows/ci.yml` (installs dbt-duckdb there only; core never does); `fabric-demo` also installs the plugin. |
| 7 | Manual Fabric test | Not run (owner, `[VERIFY]`); checklist in RUNBOOK 13.2. |

## Found while building

- **Bug fixed in core: `shape.check` reported every `min` and `max` of a decimal column as violated.** The
  profile stores a decimal's bounds as `["Decimal", "1.50"]`; the contract compared the text with a number.
  Every dbt model with a money column has one, so the acceptance test hit it. `contracts/v1.py::_plain` converts
  it; regression test `tests/demo/core/test_contract.py::test_min_and_max_of_a_decimal_column_compare_as_numbers`
  (fails before: `1 failed, 27 passed`; passes after). It is in L1's file; the change is eight lines and the
  merge should be clean unless another lane touches `_plain`.
- dbt allows one `schema.yml` entry per model, so compiled tests cannot go in a second file for a model that has
  an entry: `to-dbt-tests --merge` adds them to the existing entry (de-duplicated by test and arguments).
- `dbt_expectations` 0.10.x has no `mostly`; the null-share test is `dbt_utils.not_null_proportion`.
- The generation engine writes the `temporal` strategy's output as a timestamp even for a column typed `date`
  (the sample's `order_date`); the seeds sink cuts it to a date where the column is a date. Not changed in core.
- A column named `zip` is generated with the `faker` provider, which is not installed by `[dev]`
  (`ImportError` at generation). Same in `from-ddl`; not changed. The unit tests use other names.
- Profiling decimals from DuckDB parquet/Arrow: dtype `float`, min/max `["Decimal", ...]`.

## For the lead (not decided here)

- **T-08** lists the extras and has no `[dbt]`; I did **not** add one (docs say `pip install
  sqllocks-shape-dbt`). Say if you want `dbt=["sqllocks-shape-dbt==0.9.0"]`.
- **T-09 / §5** list the first-party plugins; `plugins/shape-dbt` is a seventh. I added `dbt` to
  `scripts/check_plugin_skeletons.py` (`EXPECTED`) and did not edit the plan. The merge with P6-11 (which removed
  `mcp` from the same line) is resolved as `("kafka", "eventhubs", "fabric", "sqlserver", "domains",
  "simulation", "dbt")`.
- The plugin's tests: `pytest -m "not dbt" plugins/shape-dbt/tests` needs no dbt. `pytest -m dbt` needs
  `dbt-duckdb` and the package hub (or `SHAPE_DBT_PACKAGES_FILE`). The `dbt` marker is declared in the root
  `pyproject.toml`; the CI `dbt` job runs the whole directory.
- `tests/demo/fabric/test_pipelines.py::test_bind_replaces_every_placeholder` got the two new bind arguments
  (`--notebook shape_profile_dbt=...`, `--dbt-job ...`) because the new pipeline has two more placeholders.

## Checks run in this session

All run on the final tree (after the merge of `origin/build/main-plan` 521310f), in `~/.venvs/shape` unless noted.

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 882 files already formatted |
| `mypy` (strict, `packages=["shape"]`) | no issues in 347 source files |
| `mypy --strict --explicit-package-bases plugins/shape-dbt/src` | no issues in 7 source files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | no output (the new public `ParsedTable` etc. are used by the plugin; no whitelist entry needed) |
| `lint-imports` | 1 contract kept, 0 broken |
| `python scripts/check_user_facing.py` | clean; also `--wheel` on all 7 plugin wheels built by `check_plugin_skeletons.py --build ... --no-isolation` (including `sqllocks_shape_dbt-0.9.0`): clean |
| `python scripts/check_plugin_skeletons.py` (and `--build`) | OK, 7 distributions, 7 wheels, pure `py3-none-any`, no tests inside |
| `bandit -q -r src -ll` | no findings (only the existing `nosec` notices) |
| START: median of 10 runs of `shape --version` | 52 ms (min 48, max 67; limit 300 ms); the plugin's commands are listed without importing it |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` | 5211 passed, 2 failed, 46 deselected: the two were `tests/plugins/test_plugin_kit_install.py` tests that hard-coded six plugin distributions; updated to seven (3 lines) and re-run: 7 passed under `SHAPE_KERNEL=rust` and under `python` |
| the same with `SHAPE_KERNEL=python` | 5211 passed, the same 2 failed (same fix), 46 deselected |
| `pytest tests/demo/fabric` (`~/.venvs/shape-fabric`, unixODBC installed) | 233 passed (includes the 16 new tests of `test_dbt.py`; `test_pipelines.py` bind test updated) |
| `pytest -m "not dbt" plugins/shape-dbt/tests` | 108 passed, 7 deselected |
| `pytest plugins/shape-dbt/tests` with `dbt-duckdb` (dbt 1.12.5) and `SHAPE_DBT_PACKAGES_FILE` (local package copies) | 115 passed, including the 7 `dbt` tests |
| `python -m shape.plugins.kit sqllocks-shape-dbt` | OK: 5 plugins conform to plugin API 1.0 |
| `python scripts/check_requirements.py`, `check_conformance_coverage.py` | OK (89 requirements; 32 normative statements, 32 tests) |
| `python scripts/check_secrets.py` | **exits 1 on `plugins/shape-fabric/tests/test_recorded.py`, unchanged by this lane** (the same on the integration tree; not mine, not fixed) |
| Not run | `cargo` checks (no Rust changed); `pytest -m heavy` (nothing in this lane is heavy); `tests/demo/content` (needs `$REFENGINE_ROOT`); the CI `dbt` job itself and `dbt deps` from the hub (see Environment); everything live in Fabric |

Reproduce the acceptance test: `pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-dbt dbt-duckdb
&& pytest -m dbt plugins/shape-dbt/tests`.

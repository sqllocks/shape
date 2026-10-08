# G6-eval: Gate G6 evidence, §10 e2e and coverage parts (lane/G6-eval)

Status: **G6 NOT MET.** Item 1 (§10 e2e) is met once this lane's five tests land, under the reading of
§10 set out below; the lead must confirm that reading. Item 3 (coverage) is blocked: `check_coverage.py`
exits 0, but P6-01a to P6-01e, which `refengine_coverage.tsv` names, are not `done`. Item 2 (domains,
T-21 and GEN-IN) belongs to lane P6-01-fin and was not evaluated here.

Base: `int/INT-18` at `f94de92`. §11 and §2.3 were not edited, and no decision, gate or tolerance was
changed. No product code changed: this lane adds tests and this file only, so `CHANGELOG.md` has no
entry. The pinned RefEngine checkout (`422e78d`) was cloned per §1.2 and was only read.

## 1. How the §10 item reads under §2.3 (and what conflicts, for the lead)

What the plan on INT-18 says:
- **§2.3, 2026-09-30 (D-12, D-13, §10):** "Shape reads only its own formats (no RefEngine schema or
  profile input) and has no RefEngine command aliases; §10 is retired as a parity contract." The
  `profile capture|diff` ports are removed.
- **§10 header (amended to match):** the Shape commands in the right-hand column "are still delivered
  by the work packages named, under their Shape names only: the `[aliases]` in brackets are dropped,
  and the `profile capture|diff` row is removed."
- **§2.3, 2026-10-02:** P4-08, P4-10 and P4-11 plan text is superseded by D-13. That row does not
  mention P8-01.
- **G6 (§7) is unchanged:** "Every row of §10 has a passing e2e test."

**Reading applied:** each of the 21 remaining §10 rows (22 minus `profile capture|diff`) needs a
passing end-to-end test of its Shape command(s) and the options the row lists. Each test runs
`shape.cli.main.main([...])` or a `shape` / `python -m shape` subprocess and checks the output or exit
code. Tests use Shape names; aliases are not deliverables and are not counted.

**Conflicts recorded for the lead (nothing was changed):**
1. **"P8-01 rejected" is not recorded.** The lane brief says P8-01 is rejected (superseded by D-13,
   §10 aliases retired). On INT-18 (and on `build/main-plan` `5c91ea5`), no §2.3 row says this, and
   §11 row 82 still shows P8-01 as `todo`. P8-01's deliverables (every §10 alias; reading RefEngine profile JSON
   and `.refengine.json`; `docs/migration/from-refengine.md`) and its acceptance criterion (RefEngine's
   README commands with `refengine` replaced by `shape`) contradict D-13 as written. P8-04 still depends
   on P8-01. The lead needs a §2.3 row and a §11 status for P8-01.
2. **`compare` is still a user-visible alias of `shape fidelity`**, at `src/shape/cli/main.py:1619`. It
   comes from P4-09's deliverable "alias `compare`", which predates D-13. §10 lists `compare` as a
   bracketed (dropped) alias, and D-13 says "no RefEngine command aliases". The alias is in the v1 surface
   baseline (`tests/cli/cli_surface_v1.json`) and is tested at `tests/cli/test_fidelity_cli.py:47,157`,
   so removing it is a breaking surface change. That call belongs to the lead or owner.
3. **The shape-fabric plugin registers top-level `publish`, `notebook`, `deploy-notebook`,
   `setup-fabric` and `export-model`** (`plugins/shape-fabric/pyproject.toml:36-40`). With the plugin
   installed, these appear in `shape --help`. §10 marks them as dropped aliases (`[originals]`,
   `[export-model]`), but the §2.3 INT-14 row records P6-07c as delivered "with top-level aliases".
   This conflicts with D-13. `check_user_facing.py` does not catch it, because the names do not contain
   the baseline's name.
4. **The §10 `generate` row describes `sql-database` as a `generate --format` with `--auth`,
   `--connection-string`, `--write-mode`, `--batch-size`, `--staging-path` "via shape-fabric".** Shape
   delivers this as `shape fabric publish -t sql-database` (and `generate --to mssql://…`).
   `--staging-path` belongs to the `warehouse` target, not to `sql-database`. This lane tested both as
   delivered (rows 1 and 15 below). If the lead reads the row literally (`generate --format
   sql-database`), the row has no implementation, and that would be an escalation.

## 2. §10 rows and their e2e tests

Paths are repo-relative. Every test listed passed in this session: core tests in the full runs (§5),
plugin tests in the shape-fabric runs. **New** marks a test this lane added; each new test was written
because no CLI-level test covered that part of the row.

| # | §10 row (Shape side) | e2e test(s) |
|---|---|---|
| 1 | `generate`, every `--format` | summary: `tests/cli/test_generation_cli.py::test_generate_summary` and **new** `::test_generate_explicit_summary_format` (`-f` and `--format summary`); csv, tsv, jsonl, parquet, sql: `::test_generate_formats`; excel and delta: `test_generation_cli.py` (`-f excel`, `-f delta`). sql-database with `--auth`, `--connection-string`, `--write-mode`, `--batch-size`: **new** `plugins/shape-fabric/tests/test_publish.py::test_fabric_publish_sql_database_with_every_option` (the `fabric publish` name; also asserts 4 round trips for 200 rows at `--batch-size 50`). `--staging-path`: **new** `::test_fabric_publish_warehouse_with_a_staging_path` (see conflict 4) |
| 2 | `describe`, `list`, `validate` | `tests/cli/test_generation_cli.py` (`describe retail`, `list`, `list --json`, `::test_validate_dispatches_on_content`, which covers schema, contract and neither) |
| 3 | `presets`, `composite` | `tests/cli/test_generation_cli.py` (`presets`); `tests/generation/test_composite_p601e.py` (`composite <spec>`, `composite enterprise`, `--dry-run`, `presets --composites`) |
| 4 | `stream` and its flags | `tests/streaming/emit/test_stream.py`: `--table --scale --seed --sink --output --no-realtime` (`::test_stream_file_is_the_emit_table_in_time_order`), `--max-events`, `--mode`, `-s/-t/-m`, `--out-of-order`, `--anomaly-fraction`. **New** `::test_realtime_rate_duration_and_burst` (marked `realtime`): `--realtime --rate 1000 --duration 1`, with and without `--burst 0:0.5:3`; events within ±10% of the schedule (1000 / 2000), stopped by `duration`, and the head of the unpaced stream byte for byte |
| 5 | `transform star\|cdm` | `tests/cli/test_transform_cli.py` (`transform star`, `transform cdm`; `to-star`/`to-cdm` exit 2) |
| 6 | `learn` | `tests/cli/test_profile_generation_cli.py` (`learn <file> -o`, `--json --domain`) |
| 7 | `fabric export-model` | `plugins/shape-fabric/tests/test_export_model.py::test_the_alias_and_the_fabric_subcommand_write_the_same_file` |
| 8 | `from-ddl` (`--smart`, `--explain`, `-s`, `--domain`, `-o`) | `tests/generation/test_ddl.py::TestCommand` (`--explain`; `-o --domain -s --no-smart`); **new** `::test_smart_named_is_the_default` (explicit `--smart` equals the default and differs from `--no-smart`) |
| 9 | `continue`, `time-travel` | `tests/cli/test_incremental_cli.py` |
| 10 | `fidelity` | `tests/cli/test_fidelity_cli.py` |
| 11 | `verify` (gate runner) | `tests/quality/test_verify.py`, `tests/quality/test_verify_config.py` |
| 12 | `mask` | `tests/builtins/test_mask.py`, `tests/masking/test_mask_keyed_cli.py` |
| 13 | `profile export\|import\|list\|validate` | `tests/cli/test_profile_registry_cli.py` (subprocess) |
| 14 | `profile registry list\|save\|delete\|tag\|diff\|reindex\|validate` | `tests/cli/test_profile_registry_cli.py` (subprocess) |
| 15 | `fabric publish\|notebook\|deploy-notebook\|setup` | `plugins/shape-fabric/tests/test_publish.py::test_the_alias_and_the_subcommand_publish_the_same_files` plus the two **new** tests in row 1; `test_fabric_commands.py::test_the_alias_and_the_subcommand_print_the_same_notebook`, `::test_the_alias_and_the_subcommand_make_the_same_request`, `::test_setup_through_the_fabric_subcommand` |
| 16 | `demo init\|list\|run\|preflight\|cleanup\|status\|notebook\|report` | `tests/demo_cmd/` (`test_run_local.py`, `test_connections.py`, `test_catalog_and_input.py`, `test_remote_targets.py`, `test_notebook_and_outputs.py`) |
| 17 | `bridge` | `tests/bridge/test_cli.py` (in process and in a subprocess) |
| 18 | `generate --scale-mode` and bridge `scale_*` | `tests/cli/test_scale_cli.py` (`local_single`, `local_mp`, `fabric_spark`); **new** `tests/bridge/test_cli.py::test_scale_commands_through_a_real_process` (`scale_generate`, `scale_status` and `scale_cancel` sent to a `shape bridge` process, then read back from a second process). Before this test, `scale_status` and `scale_cancel` were tested only in process |
| 19 | `pack run\|validate\|list` | `tests/cli/test_pack_cli.py`, `tests/cli/test_suite_cli.py` |
| 20 | `fidelity --tier 1\|2\|3`, `drift --psi`, `ctgan` | `tests/fidelity/test_cli_tiers.py`, `tests/fidelity/test_ctgan.py` (with a fake sdv: real SDV is an optional extra) |
| 21 | `stream-profile`, `plugins`, `check`, `inspect`, `conformance`, `plan` | `tests/streaming/test_cli.py`, `tests/regressions/test_aud_cli.py`, `tests/cli/test_five_cli.py`, `tests/cli/test_profile_cli.py`, `tests/cli/test_cli_e2e.py`, `tests/cli/test_profile_generation_cli.py` |
| — | `profile capture\|diff` | Removed by D-13. Neither subcommand exists (`src/shape/cli/profiles.py:22`). No test asserts the absence; `tests/cli/test_profile_cli.py:61` has a name that suggests it does, but it checks other things |

Notes:
- While writing the bridge test: an async `scale_generate` job is cancelled when the bridge's input
  ends (`Bridge.close`: "streams and scale runs are cancelled"), but async `generate` jobs are allowed to
  finish. This is documented behaviour, not a defect, so the new test keeps the bridge running until
  the job is final.
- `stream` resumes from `<output>.checkpoint` by default, so a second run to the same output
  continues where the first stopped. The new stream test writes to fresh files.

## 3. Coverage item (`check_coverage.py`)

```
source scripts/env.sh && python benchmarks/vs_refengine/check_coverage.py
OK: all 277 RefEngine files are mapped; every work package exists in section 11      (exit 0)
```

Of the 39 work packages that `docs/plans/refengine_coverage.tsv` names, 34 are `done` in §11. **The
five that block G6 item 3:**

| WP | §11 status | TSV globs |
|---|---|---|
| P6-01a | wip (GEN-IN round 3) | `domains/capital_markets`, `education`, `financial` |
| P6-01b | wip (GEN-IN round 3) | `domains/healthcare`, `hr`, `insurance` |
| P6-01c | wip (GEN-IN round 3) | `domains/iot`, `manufacturing`, `marketing` |
| P6-01d | wip (GEN-IN round 3) | `domains/pulse`, `real_estate`, `supply_chain`, `telecom` |
| P6-01e | wip (lane/P6-01e) | `domains/composite.py`, `domains/shared_registry.py`, `presets/*` |

All five belong to lane P6-01-fin. When they are `done`, item 3 holds; `check_coverage.py` itself
already passes.

## 4. Failures that predate this lane (seen on INT-18; not fixed here)

`plugins/shape-fabric/tests`, `-m "not emulator and not live"`, in an environment like CI's
`stream-plugins` job: 4 failed, 916 passed with this lane's changes stashed. With them applied: 4 failed,
918 passed (the 2 new tests pass). None of the 4 affect a §10 row's coverage. There is no CI run on
`int/INT-18` to compare with.
- `test_publish.py::test_lakehouse_writes_the_landing_zone_and_the_manifest`: the manifest now also
  has `shape_version` and `min_shape_version`, and the test's expected key set was not updated.
- `test_sql_write_path_cli.py::test_upsert_is_a_write_mode_choice_and_other_databases_refuse_it`: it
  expects an `upsert` refusal for `postgresql://`, but no sink for `postgresql://` is installed here.
  The shape-databases plugin is installed by CI's `database-plugins` job, not by `stream-plugins`.
- `test_synapse.py::test_generate_to_synapse_with_a_sql_login` and
  `::test_emit_to_synapse_commits_while_the_stream_runs`: these are refused with "refusing to write to
  non-local target … pass --yes or set SHAPE_CONFIRM_REMOTE=1". The tests were not updated for the
  remote-confirmation rule.

Core suite (`pytest -m "not emulator and not live"`, both kernels): the failures below also occur at
base `f94de92`. None is in a file this lane changed, and the five new core tests pass in both kernels.
- `tests/demo/fabric/test_dbt.py` (7 tests): `ModuleNotFoundError: No module named 'shape_dbt'`. This is
  the environment: `tests/demo/fabric/requirements.txt` does not install `plugins/shape-dbt`.
- `tests/demo_cmd/test_notebook_and_outputs.py::test_the_semantic_model_is_a_bim_of_the_learned_schema`
  and `::test_all_writes_the_page_and_the_model`: "the semantic model needs the shape-fabric plugin".
  This is the environment: CI's `test` job does not install shape-fabric, so these fail there too,
  unless CI installs it somewhere I did not find.
- `tests/bridge/test_vectors.py::test_a_live_bridge_gives_the_responses_of_the_vectors[report_card]` and
  `[report_card_read]`: `overall_reasons` text differs from the stored vectors.
- `tests/generation/test_w1_06_spec_schema.py::test_the_shipped_file_is_what_the_builder_makes`: the
  shipped spec schema is out of date with its builder.
- `tests/plugins/test_trust_reach.py::test_no_plugin_starts_a_subprocess_or_opens_a_socket`:
  `kerberos.py: subprocess` (×3).
- `tests/test_removed_modules.py::test_removed_module_is_not_importable[history]`: `shape.history`
  imports again.
- Rust kernel only: `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`
  (`heavy`; +11.6% RSS from 24M to 48M rows, limit 10%). CI runs `heavy` tests in a separate step.

No workflow change is needed. CI's `stream-plugins` job already runs `plugins/shape-fabric/tests`,
which picks up the two new plugin tests.

## 5. Checks run in this session

Environment: Python 3.11.15, Rust 1.97. `pip install -e '.[dev,streaming,advanced]' -e
plugins/shape-domains` (CI's `test` job), plus `tests/demo/fabric/requirements.txt` and unixODBC.
Plugin runs used a second venv with CI's `stream-plugins` installs (shape-domains, -kafka, -eventhubs,
-sqlserver, -fabric). Each pytest run had its own private `TMPDIR`.

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1570 files already formatted |
| `mypy` | Success: no issues found in 565 source files |
| `python scripts/check_user_facing.py` | clean |
| `python scripts/cli_surface.py --check` | 0 breaking changes, 0 notes |
| `python benchmarks/vs_refengine/check_coverage.py` | exit 0 (277 files mapped) |
| `pytest plugins/shape-fabric/tests -m "not emulator and not live"` | 4 failed (see §4), 918 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 15 failed, 11994 passed, 24 skipped (38 min). All 15 also fail at base `f94de92` (re-run in a worktree, rust kernel: 15 failed); see §4 |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 14 failed, 11995 passed, 24 skipped (2 h 04 min). The same set as rust, minus `test_bounded_mode_memory_does_not_grow_with_rows`, which passed here |

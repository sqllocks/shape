# DEMO-LIVE: fixes from the owner's live Fabric dry run (2026-10-05)

Branch `fix/DEMO-LIVE`, from `origin/fix/F-12` (243f132f = `build/main-plan` 7fedc4d9 + the F-12
leak-scan fix). Findings from the owner's live dry run of faf5ddb7 (trial capacity FTL4, East US 2,
Python 3.11 notebook runtime, preinstalled deltalake with the pyarrow engine). Times are US Eastern.

The builder cannot reach Fabric: every Fabric surface is mocked in the tests, and each item that
still needs a live check is marked **[VERIFY LIVE]** here and in `integrations/fabric/RUNBOOK.md`,
with the exact check to run.

Environment of every check below: Linux, 4 cores, Python 3.11.15; dev venv
`pip install -e ".[dev]" -e plugins/shape-dbt -r tests/demo/fabric/requirements.txt` plus
`-e plugins/shape-fabric -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-databases`
(the plugin tests need them); unixODBC (`libodbc2`) installed; Rust kernel built from `rust/`.

| Finding | State | Commit |
|---|---|---|
| F-1 Delta writes through the `/lakehouse/default/Tables` mount fail | fixed, [VERIFY LIVE] | see F-1 |
| F-2 `pytest tests/demo/fabric` red in a fresh container | fixed (RUNBOOK §2) | see F-2 |
| F-3 RUNBOOK §4 step 5 defaults disagree with the notebook | fixed (wording) | see F-3 |
| F-4 `%pip ... "sqllocks-shape==0.9.0"` installs PyPI's older 0.9.0 | fixed, [VERIFY LIVE] | see F-4 |
| F-5 RUNBOOK §4 step 6 promises changes the safe-capture baseline cannot give | fixed (wording, test) | see F-5 |
| F-6 UDF requirements and the connection alias literal | fixed, [VERIFY LIVE] | see F-6 |
| F-7 `checkProfile` drops `not_evaluable` | fixed, [VERIFY LIVE] | see F-7 |
| F-8 `diffProfiles` raise payload not bounded | fixed, [VERIFY LIVE] | see F-8 |
| F-9 `profileLakehouseTable` needs `dbo.` | fixed, [VERIFY LIVE] | see F-9 |
| F-10 Functions activity needs a connection | fixed (RUNBOOK), [VERIFY LIVE] | see F-10 |
| F-11 Notebook activity: leave Connection empty | fixed (RUNBOOK) | see F-11 |
| Obs-1 interactive Run all marks the exit cell failed | documented | see Obs-1 |
| Obs-2 `ArtifactNotVerifiedWarning` in notebook output | fixed, [VERIFY LIVE] | see Obs-2 |

## F-1: Delta writes through the lakehouse mount

**Root cause.** delta-rs (the `deltalake` package, pyarrow engine included) commits by writing a
temporary file and renaming it. The `/lakehouse/default/Tables` FUSE mount of a Fabric Python
notebook refuses that rename (`OSError: Generic LocalFileSystem error: Unable to rename file: No such
file or directory (os error 2)`). `shape_setup` cell 4 and `shape_generate` (through
`generation.write_delta_tables` and the `delta` sink) both wrote there. Second, real bug in the same
path: `write_delta_tables` wrapped `tables_dir` in `Path()`, which collapses `delta+abfss://` to
`delta+abfss:/`, so a OneLake URI could never have worked.

**Fix.**
- New `shape.integrations.fabric.onelake`: `default_tables_uri()` builds
  `abfss://<workspaceId>@onelake.dfs.fabric.microsoft.com/<lakehouseId>/Tables` from
  `notebookutils.runtime.context` (`defaultLakehouseWorkspaceId` or `currentWorkspaceId`, and
  `defaultLakehouseId`; the names form `<workspace>@.../<lakehouse>.Lakehouse/Tables`, URL-quoted,
  when the IDs are absent); `storage_options()` is `{"bearer_token":
  notebookutils.credentials.getToken("storage"), "use_fabric_endpoint": "true"}`, exactly the
  owner's working workaround; `tables_location()` returns the pair, or the local folder and no
  options outside Fabric (the tests).
- `generation.write_delta_tables` keeps `tables_dir` as text (no `Path`), accepts `abfss://` (adds
  the sink's `delta+` prefix) and `delta+abfss://`, and passes `storage_options` to the `delta` sink.
  The sink's own `delta_ready`, UTC timestamp cast and `schema_mode="overwrite"` are unchanged, as in
  the workaround.
- `shape.builtins.sources.delta._storage_options` (used by the sink): a token given in
  `storage_options` (`bearer_token`, `azure_storage_token` or `token`) is the credential; no other is
  looked up, so the options reach delta-rs exactly as given (plus `use_fabric_endpoint` for OneLake).
- `shape_generate` writes to `onelake.tables_location(...)` with its storage options.
- `shape_setup` (installs nothing, so it inlines the same logic): writes each table to the OneLake
  URI with the same storage options, `delta_ready` types (microsecond, UTC timestamps; strings) and
  `schema_mode="overwrite"`; a `TABLES_URI` constant overrides the derived URI.
- RUNBOOK §3 step 3, §9 (troubleshooting row for the rename error), §12.6 item 2.

**Tests.** `tests/integrations/test_fabric_onelake.py` (URI from IDs and from quoted names, no
default lakehouse, storage options and the `storage` audience, override, outside Fabric, `abfss://`
and `delta+abfss://` never collapsed with the exact storage options reaching `write_deltalake`, no
credential lookup when a token is given, append keeps the schema, local folders as text or `Path`);
`tests/demo/fabric/test_notebooks.py::test_setup_notebook_writes_to_onelake_by_abfss_in_fabric` and
`::test_setup_notebook_takes_an_explicit_onelake_uri`;
`tests/demo/fabric/test_generate.py::test_in_fabric_the_tables_go_to_onelake_by_abfss_with_the_token`
(the notebooks run under a mocked runtime context and token, `write_deltalake` recorded).

**[VERIFY LIVE].**
1. `shape_setup` with `shape_demo` attached: **Run all**. The second code cell prints `Writing Delta
   tables to abfss://<workspaceId>@onelake.dfs.fabric.microsoft.com/<lakehouseId>/Tables`; compare it
   with the lakehouse's Tables ABFS path (Tables > ... > Properties). Every table prints its row count,
   and `orders_day1`, `orders_day2`, ... appear under Tables and in the SQL endpoint. If the URI is
   wrong or a local path, paste the ABFS path into `TABLES_URI` and rerun, and tell the lead which
   runtime-context keys exist (`print(notebookutils.runtime.context.keys())`).
2. `shape_generate` with the defaults: the helpers cell prints `Delta tables go to abfss://...`; nine
   tables, 21,750 rows; `shape_profile_domain` then passes.

## F-4: the install cell installed PyPI's 0.9.0

**Root cause.** `%pip install --find-links builtin "sqllocks-shape==0.9.0"` asks pip for a name and
version; PyPI has an older `sqllocks-shape` 0.9.0 (no `shape.integrations`), which is an equally
good candidate, so pip installed it. The version stays 0.9.0 (owner decision).

**Fix.** Every install cell installs the uploaded wheels by file path:
`%pip install "numpy>=2,<3" "pandas>=2.2.2,<3" builtin/sqllocks_shape-0.9.0-py3-none-any.whl` (plus
`builtin/sqllocks_shape_domains-...whl` in `shape_generate` and `shape_profile_domain`,
`builtin/sqllocks_shape_dbt-...whl` in `shape_profile_dbt`; `shape demo notebook` lists Shape,
domains, fabric, eventhubs and sqlserver by path). For the Rust kernel the cell names the platform
wheel, `sqllocks_shape-0.9.0-cp311-abi3-manylinux_2_28_x86_64.whl`, to put in place of the pure one
(DM-1: the demo runs on the pure-Python profiler). RUNBOOK §1, §4 step 3, §9, §10 item 2 and 13,
§12.1, §12.6; `udf/requirements.md` (F-6) says to upload the wheel as a private library and never
list `sqllocks-shape==0.9.0` as a public one.

**numpy 2 and the runtime's pandas.** Measured locally in a venv built like the runtime (numpy
1.26.4, pandas 2.1.4, matplotlib 3.8.2), then the install line: pip prints the owner's conflict
(`pandas 2.1.4 requires numpy<2`, `matplotlib 3.8.2 requires numpy<2`). With numpy 2.4.6 and pandas
2.1.4, `import pandas` fails (`ValueError: numpy.dtype size changed, may indicate binary
incompatibility`), and so does `pyarrow.array([1, 2])` with pyarrow 17.0.0, 18.1.0, 19.0.1 and 21.0.0
(pyarrow imports pandas when it builds an array); pyarrow 14.0.2 cannot load with numpy 2 at all. So
`generation.py`'s and `udf.py`'s pandas use does **not** work with numpy 2 + pandas 2.1.4, and
neither does anything else. With `pandas>=2.2.2,<3` added to the line (pip took 2.3.3): profiling a
pyarrow table, the HTML report, `generate_domain` + `write_delta_tables` + profiling the nine tables,
`sample_to_pandas`, `udf.profile_lakehouse_table` (its DataFrame path) and `udf.profile_data_frame`
all work (script run in that venv, output in this session). The remaining pip conflict is
matplotlib's; Shape does not import matplotlib.

**Tests.** `tests/demo/fabric/fabric_helpers.installs_by_path` (only `"numpy>=2,<3"`,
`"pandas>=2.2.2,<3"` and `builtin/*.whl` arguments; no `==`, no `--find-links`; exactly the expected
wheels), used by `test_notebooks.py::test_python_notebook_configure_and_install_cells`,
`test_generate.py::test_valid_nbformat_with_fabric_metadata` and
`test_dbt.py::test_the_notebook_is_a_fabric_python_notebook_that_installs_the_dbt_plugin`;
`tests/plugins/test_optional_plugin_pieces.py::test_the_demo_notebook_installs_the_fabric_plugin_with_both_extras`
and `tests/demo_cmd/test_rehearsal.py::test_the_notebook_installs_from_the_uploaded_wheels`.

**[VERIFY LIVE].** In `shape_profile` (Python 3.11), with only the pure wheel in Resources > builtin,
run the install cell, then the helpers cell: it prints `Shape 0.9.0, kernel: python`, and `import
shape.integrations.fabric.onelake` works. pip's output names only matplotlib in the conflict. Then
`import pandas; pandas.__version__` is 2.2.x or 2.3.x.

## F-6: UDF library set and the connection alias

**Root cause.** `udf/requirements.md` gave open ranges (`numpy>=2.0,<3`, `pyarrow>=14`,
`pandas>=2.0`) that do not resolve on the UDF's Python 3.11 runtime next to
`fabric-user-data-functions` 1.0.142 (`pyarrow>=19.0.1,<20`). And `function_app.py` passed an
imported constant, `LAKEHOUSE_ALIAS`, to `@udf.connection`: the portal reads the alias from the
file statically and did not see the connection.

**Fix.** `requirements.md`: runtime Python 3.11; pinned `numpy` 2.4.6, `pyarrow` 19.0.1, `pandas`
3.0.6 (the owner's working set); the Shape wheel and the domains wheel as private libraries uploaded
from a file (*Add from local*), never `sqllocks-shape` as a public library (F-4). `function_app.py`:
the four decorators are `@udf.connection("shapeLakehouse", "lakehouse")`, with a comment; the import
is gone. RUNBOOK §6 steps 2 and 3.

**Checked locally.** A Python 3.11 venv with exactly `numpy==2.4.6 pyarrow==19.0.1 pandas==3.0.6
fabric-user-data-functions==1.0.142` plus the pure Shape and domains wheels resolves without
conflict; `udf.profile_lakehouse_table` (DataFrame path), `udf.profile_data_frame`,
`generation.sample_to_pandas` and profiling run on it.

**Tests.** `tests/demo/fabric/test_udf.py::test_every_connection_alias_is_the_literal_the_portal_reads`
(every `@udf.connection` first argument is a string literal, on exactly the four lakehouse
functions, equal to `shape.integrations.fabric.udf.LAKEHOUSE_ALIAS`; the constant is not imported)
and `::test_the_requirements_pin_the_working_set_and_upload_the_wheel`.

**[VERIFY LIVE].** Publish `shape_udf` with the pinned public libraries and the two uploaded
wheels: the publish log shows no `sqllocks-shape` downloaded from PyPI; *Manage connections* shows
the `shapeLakehouse` connection bound to the four functions; `profileLakehouseFile` on
`demo/day1/orders.parquet` returns `sampled: false`.

## F-7: `checkProfile` dropped the rules it could not evaluate

**Root cause.** `udf.check_profile` returned `passed` and the violations of `shape.check`, but not
its `not_evaluable` list: on a safe-capture `.shape` (a notebook artifact; `shape.save` defaults to
safe capture since W1-11) the `min`, `max` and `allowed_values` rules cannot be evaluated, so the
check fails with no violation. The result said `passed: false, violationCount: 0` with no reason,
and `failOnViolation` raised `Contract check failed with 0 violation(s)`.

**Fix.** The result carries `notEvaluableCount` and `notEvaluable` (the first `MAX_LISTED` = 100
entries of `{column, rule, reason}`; `truncated` also covers them); the error message adds `N
rule(s) not evaluable: <column: rule; ...>` and says the profile was saved with the safe capture and
that a full-capture `.shape` such as `profileLakehouseFile` writes is needed; the error properties
carry `notEvaluable` (bounded) and `notEvaluableCount`. Entries name a column and a rule, never a
value. RUNBOOK §6 (a paragraph on full capture) and §8.

**Tests.** `tests/demo/fabric/test_udf.py::test_a_safe_capture_profile_says_what_it_could_not_check`,
`::test_fail_on_violation_with_only_unevaluable_rules_says_why`,
`::test_the_unevaluable_entries_are_bounded` (a safe-capture artifact as the notebooks write it; the
fixture contract's `amount` `min` and `max` are the two rules left out).

**[VERIFY LIVE].** In `shape_udf` Test mode: `checkProfile` with `profilePath` = a notebook artifact
(`shape/orders_day1/<timestamp>/orders_day1.shape`), `contract` = `contracts/orders.json`: `passed:
false`, `violationCount: 0`, `notEvaluable` naming `order_total` `min` and `max` (and any other
value rule); with `failOnViolation = true` the error message says so. Then on
`shape/orders/udf_day1.shape` (written by `profileLakehouseFile`): `passed: true`,
`notEvaluableCount: 0`.

## F-8: the `failOnDrift` error payload was unbounded

**Root cause.** `udf.diff_profiles` bounded its result with `bounded()` (900 KB, dropping
`changes` past it) but raised the `failOnDrift` error with `changes[:100]` as they were, and each
change carries its own value lists: `new_categorical_values` on a float column (the demo's
`order_total`, every day-2 value new) lists thousands of values on each side.

**Fix.** New `udf.cap_values` cuts each list or dict value of a change (and of a check violation)
to its first `MAX_VALUES` = 20 items, adds `<key>Count` with the full length and
`valuesTruncated: true`. Both paths of `diff_profiles` (result and `failOnDrift` error) apply it,
and the error payload goes through the same `bounded()` as the result (`changesOmitted: true` past
900 KB). `check_profile` does the same for violations and its `failOnViolation` payload (an
`allowed_values` violation on a float column has the same shape). RUNBOOK §6 shows the bounded
form ("Results and errors are bounded") and the error's `<column: kind; ...>` summary, not the
unbounded output.

**Tests.** `tests/demo/fabric/test_udf.py::test_each_change_keeps_only_the_first_values_and_says_how_many`,
`::test_the_fail_on_drift_error_is_bounded_like_the_result` (400-value float column x 1.4; the error
payload's `new_categorical_values` change keeps 20 values and is under 20 KB; with the bound lowered
the changes are dropped, `changesOmitted: true`), `::test_cap_values_leaves_short_values_alone`.

**[VERIFY LIVE].** `diffProfiles` with the day-1 and day-2 `.shape` files that `profileLakehouseFile`
wrote (`shape/orders/udf_day1.shape`, `udf_day2.shape`), `failOnDrift = true`: the error appears in
the portal (and in the pipeline activity output) at a readable size; its `order_total`
`new_categorical_values` entry has 20 values per side, `currentCount` in the thousands and
`valuesTruncated: true`.

## F-9: `profileLakehouseTable` and the endpoint's schema

**Root cause.** `profile_lakehouse_table` quoted the name as given: `orders_day1` became
`SELECT TOP (n+1) * FROM [orders_day1]`, which the SQL analytics endpoint does not resolve (42S02);
`dbo.orders_day1` works. The test fake resolved unqualified names, so the tests passed.

**Fix.** New `udf.sql_table_name`: a name without a schema gets `dbo` (`DEFAULT_SCHEMA`); each part
is bracket-quoted with `]` doubled (defence in depth: the existing name pattern already admits only
letters, digits and underscores, and is still checked first). RUNBOOK §6 (the table row, a paragraph
on the endpoint's names) and §8.

**Tests.** The fake SQL endpoint in `tests/demo/fabric/test_udf.py` now behaves like the real one
(tables live in `dbo`; an unqualified name raises 42S02), so `test_profile_table_full` asserts
`FROM [dbo].[orders_day1]`; new `test_names_without_a_schema_get_dbo`,
`test_a_closing_bracket_is_doubled_inside_a_quoted_part`,
`test_the_unqualified_and_the_dbo_name_read_the_same_table`; the unsafe-name tests are unchanged.

**[VERIFY LIVE].** `profileLakehouseTable` with `tableName = orders_day1`: returns 500,000 rows
(medium scale), no 42S02; and with `tableName = dbo.orders_day1`: the same result.

## F-2: the local dry run was red in a fresh container

**Root cause.** RUNBOOK §2 gave a hand-picked `pip install` list instead of the tests' own
`tests/demo/fabric/requirements.txt`. The list lacked `fsspec`, which
`integrations/adf/batch/run_gate.py` imports (`_filesystem`) and which `test_adf.py` and
`test_generate_adf.py` exercise (in-process and through the gate subprocess); it also lacked the
dbt plugin and did not install Shape itself. Reproduced in this session: a clean venv with exactly
that list plus the Shape and domains wheels fails those two files with `ModuleNotFoundError: No
module named 'fsspec'`; adding `fsspec` alone makes both pass (48 passed).

**Fix.** RUNBOOK §2 now gives CI's `fabric-demo` steps: the unixODBC system package, Java 17 and
Rust named, a new Python 3.11 venv, `pip install -e '.[dev]' -e plugins/shape-dbt -r
tests/demo/fabric/requirements.txt`, then `pytest tests/demo/fabric -q`, and says why the list
must not be hand-picked.

**Tests.** `tests/demo/fabric/test_runbook_setup.py`: §2's install command is CI's `fabric-demo`
command verbatim; every `pip install` line of §2 installs the requirements file; the requirements
name `fsspec`, `jsonschema`, `fabric-user-data-functions` and `deltalake`; §2 names unixODBC and
Java 17.

**Verified.** A new venv built exactly as §2 says (`python3.11 -m venv`, the three commands), then
`pytest tests/demo/fabric -q`: see "Clean-venv run of RUNBOOK §2" below.

## F-3: the day-1 parameters in RUNBOOK §4 step 5

**Root cause.** Step 5 said "Run with the defaults (`tableName = "orders_day1"`, `contractPath =
"contracts/orders.json"`)", but the notebook's parameters cell has `tableName = "orders"`,
`contractPath = ""`. `demo/TALK.md` relies on the **pipeline's** defaults (beat 5: "Pipeline
`shape_gate_notebook`, default parameters"), which are `orders_day1` and `contracts/orders.json`
(§7.2), not on the notebook's.

**Fix.** Wording only (as preferred): step 5 says to set `tableName = "orders_day1"` and
`contractPath = "contracts/orders.json"` in the parameters cell, and why the cell's own defaults
differ. The notebook is unchanged, so the pipeline-parameter tests are unchanged.

## F-5: what the day-2 notebook diff can show

**Root cause.** The notebook saves its artifact with `shape.save`'s default safe capture; step 6
diffs day 2 against that saved artifact. A safe-capture profile leaves out the values that
`order_total`'s comparisons need, so the diff reports only `status` `new_categorical_values`;
`order_total`'s `range_change` and `category_shift` are not evaluable (and its `mean_shift` is
under the default threshold anyway, `DRIFT.md`). The product is consistent; RUNBOOK §4 step 6
promised "changes listing each documented change".

**Reproduced** with the real demo data (`demo/make_data.py`, medium, seed 42; 500,000 orders):
day-1 profile saved with the default capture, loaded, diffed against the day-2 profile: `drifted:
true`, changes `[("status", "new_categorical_values", "low")]`, not evaluable `customer_id`
`category_shift`, `shipping_cost` and `order_total` `range_change` and `category_shift`; the
contract check of the day-2 profile fails on `status` `allowed_values` and `order_total` `max`.

**Fix.** RUNBOOK §4 step 6: the expected violations, and `changes` holding only `status`'s new
value, with the reason and where `order_total`'s drift shows instead. `demo/DRIFT.md`: new section
"In the Fabric notebook (a saved baseline)". `demo/TALK.md`: a paragraph before the diff caveat
saying the notebook's diff shows only `lost` and how to show `order_total`'s drift (nothing in
TALK.md promised `order_total` drift from the notebook diff; the stage diff it gives is the
full-capture command line).

**Tests.** `tests/demo/content/test_demo_data.py::test_the_notebook_baseline_diff_reports_only_the_new_status`
(the notebook's save/load round trip on the demo data; exact changes; the skipped comparisons; the
contract violations unchanged). `pytest tests/demo/content`: 46 passed.

## F-10: the Functions activity needs a connection

**Fix.** RUNBOOK §7.2 (b) step 2: Connection is required and empty on a fresh workspace; create it
first (*Browse all*, the *Fabric user data functions* connection type, sign in with the
organizational account), then choose workspace, item and function; step 3 reuses it; §12.6 item 5
points to it for `generateSample`. §12.4's pipeline (`shape_generate_gate`) has no Functions
activity.

**[VERIFY LIVE].** Build `shape_gate_udf` by hand on the workspace: the Connection list is empty,
*Browse all* offers the user-data-functions connection type, OAuth sign-in with the organizational
account creates it, and the workspace / item / function pickers fill after it is selected. Write
down the exact labels if they differ, so the runbook can quote them.

## F-11: the Notebook activity's Connection

**Fix.** RUNBOOK §7.2 (a) step 3: "leave Connection empty (it is optional; the dialog's Workspace
identity default makes the activity fail)". Confirmed live by the owner; no further check.

## Obs-1: interactive Run all and the exit cell

**Fix.** RUNBOOK §4, after the exit rule: an interactive Run all marks the last cell failed although
`ExitValue` prints; expected; pipelines read the exit value. Observed live by the owner.

## Obs-2: the not-signed warning in notebook output

**Fix (lead decision).** The notebooks that take `baselinePath` (`shape_profile`,
`shape_profile_spark`, `shape_profile_distributed`, `shape_profile_domain`, `shape_profile_dbt`)
load it through a `load_baseline` helper that silences only `ArtifactNotVerifiedWarning`, inside
`warnings.catch_warnings()`, around that one `shape.load`, with a comment that the baseline is the
notebook's own unsigned artifact. Nothing else is suppressed. RUNBOOK §4 mentions signing
(`shape sign`, `--verify PUBKEY`) as optional hardening.

**Tests.** `tests/demo/fabric/test_notebooks.py::test_the_baseline_load_silences_only_the_not_signed_warning`
(no such warning during a day-2 run with a baseline; the same file still warns on a plain
`shape.load`; another warning raised inside `load_baseline` still shows) and
`::test_every_baseline_goes_through_the_scoped_loader` (each of the five notebooks: the loader is
used, no direct `shape.load(_resolve(baselinePath))`, and the only `warnings.simplefilter` call is
`("ignore", ArtifactNotVerifiedWarning)`).

**[VERIFY LIVE].** `shape_profile` day 2 with `baselinePath`: no "is not signed" line in the output.

## Clean-venv run of RUNBOOK §2 (F-2)

2026-10-05 ~19:00 EDT: `python3.11 -m venv`, `pip install -U pip`, `pip install -e '.[dev]' -e
plugins/shape-dbt -r tests/demo/fabric/requirements.txt`, `pytest tests/demo/fabric -q`:
**259 passed, 0 failed, 0 errors** (7 min 25 s), header `shape API under test = real`.
(unixODBC and Java were present on the machine.)

## Also changed (F-1 hardening)

In Fabric (`notebookutils.credentials` present) with no default lakehouse in the runtime context,
`onelake.tables_location` and `shape_setup` raise a clear error ("attach the default lakehouse, or
set TABLES_URI") instead of falling back to the mount, which would fail with the rename error.
Tests: `test_no_default_lakehouse_in_fabric_is_an_error_not_the_mount`,
`test_setup_notebook_in_fabric_without_a_lakehouse_says_so`.

## Questions for the lead

- `src/shape/scale/spark.py` (Synapse/Spark job requirements) and `integrations/synapse/RUNBOOK.md`
  still resolve `sqllocks-shape==<v>` by name; they are not Fabric install cells, so this lane left
  them. The same PyPI-collision risk applies there.
- Long `%pip` lines in the generated notebooks exceed ruff's 100 columns (E501 in
  `integrations/`, which CI does not lint; the baseline had the same on the old `--find-links`
  lines). A magic line cannot be wrapped or carry `# noqa` (pip would read it as arguments).

## Final checks on 05986556 (2026-10-05, about 20:00 EDT)

- `ruff check src tests plugins benchmarks/vs_refengine`: clean. `ruff format --check` (src tests
  plugins benchmarks/vs_refengine scripts integrations): clean apart from the pre-existing
  `integrations/adf/batch/run_generate_gate.py`. Over `scripts` and `integrations` the only findings
  not already on the base are the long `%pip` lines (see "Questions for the lead").
- `mypy`: no issues in 685 source files. All 8 `scripts/check_*.py` exit 0, `check_user_facing.py`
  included.
- Notebooks regenerated with `build_notebooks.py`; `test_generated_notebooks_are_current` passes.
- `pytest tests/demo tests/integrations plugins/shape-fabric/tests -m "not emulator and not live"`:
  `SHAPE_KERNEL=rust` 1742 passed, 1 failed; `SHAPE_KERNEL=python` 1742 passed, 1 failed. In both,
  the one failure is the pre-existing one below. `tests/demo/content` is part of that run, and it
  passed.

## Pre-existing failures seen in this environment (not caused by this lane)

- `plugins/shape-fabric/tests/test_lakehouse.py::test_parquet_to_a_local_folder_round_trips` fails
  with pyarrow 19.0.1 (the version `tests/demo/fabric/requirements.txt` resolves, through
  `fabric-user-data-functions`): a dictionary column reads back with `int32` indices, the test
  expects `int8`. It fails identically on the untouched base (243f132f).

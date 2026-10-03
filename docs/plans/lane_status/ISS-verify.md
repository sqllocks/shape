# ISS-verify: issues #31, #32 and #7 (lane/ISS-verify)

Branch `lane/ISS-verify`, cut from `build/main-plan` (4051235; `origin/build/main-plan` had not moved at the
last check). Commit prefix `ISS-verify:`. No §11 or §2.3 edit, no PR, no issue comment, label or close. Live
Fabric runs are the owner's (O-07) and were **not** run; items that need them are marked `[VERIFY]`.
`$SPINDLE_ROOT` was not touched. No D-xx, T-xx, gate or tolerance changed; the contract format v1 (§12.3) is
unchanged (the new verify configuration is a separate, versioned document).

Environment: Python 3.11, `~/.venvs/shape` (`pip install -e plugins/shape-domains -e ".[dev,advanced]"`, Rust
kernel built by maturin), `~/.venvs/shape-fabric` (`-e '.[dev]' -r tests/demo/fabric/requirements.txt`, unixODBC
installed).

## #31: a mixed-format directory loads one format and ignores the rest

**Reproduced** on this branch: `vday/orders.parquet` + `vday/customers.csv`, `shape verify vday --schema
gates.json` printed `ERROR [schema_conformance]: Expected table 'customers' not found in data`, `Row counts:
orders: 3`, exit 1. Cause: `load_tables(dir, "auto")` picked the first of parquet, csv, jsonl that the directory
held and globbed only that.

**Fix** (`src/shape/quality/verify.py`):
- `auto` loads every parquet, csv and jsonl file of the directory, each by its own extension.
- A table that exists in two formats (`orders.csv` and `orders.parquet`) is refused (`ValueError`, exit 2) naming
  both files.
- An explicit `--format X` still loads only that format, and now emits a `UserWarning` per skipped file of another
  supported format: `skipped customers.csv: format csv differs from the requested parquet; use --format csv, or
  --format auto to load every file`.
- New `data_files(path, fmt)` returns the files that were loaded (used by the file gate).

Regression tests (fail before the fix: `['order'] != ['customer', 'event', 'order']`) in
`tests/quality/test_verify.py`: `test_a_mixed_format_directory_loads_every_file`,
`test_a_table_in_two_formats_is_refused`, `test_an_explicit_format_names_the_files_it_skips`,
`test_cli_verify_a_mixed_directory_runs_the_referential_gate`.
Behaviour change to note: other callers of `load_tables(..., "auto")` (`shape fidelity`, `shape emit`, the CTGAN
builtin) now also see every file of a mixed directory.

## #32: the CLI cannot run the range, temporal, drift and file gates

**Reproduced**: `shape verify --help` had no option for them; the report ran four gates at most. Also reproduced:
a relationship without `name` gave `invalid relationship {...}: KeyError('name')`.

**Fix**:
- `shape verify --config CONFIG.json` (`src/shape/quality/verifyconfig.py`, `src/shape/cli/main.py`). The
  configuration is its own document: `format: shape-verify-config`, `version: 1`, keys `ranges`, `date_range`,
  `no_future`, `ordering`, `baseline`, `distribution_alpha`, `file_paths`, `check_data_files` (the
  `ValidationContext.config` keys, plus the two file keys). Each gate runs only when its keys are present, with or
  without `--schema`, after the schema gates. An unknown key, wrong type or bad date is refused (exit 2) with a
  message naming the key. The report (JSON and Markdown) names the config and the reproduce command includes it.
  Documented in `docs/VERIFY.md`.
- Chosen: a separate `--config` file rather than a `config` section in the gates JSON, because the gates file may
  also be a profile or a Shape model v2, and §12.3's contract format must not change.
- Relationship errors: `relationships[0]: missing required key "name" (a shape-gates relationship needs name,
  parent, child, parent_columns, child_columns; got [...])` (`gatespec.py`). The existing test that expected the
  old `invalid relationship` text now expects the new message.
- Found while testing the issue's "late-arriving rows" case: CSV and JSONL timestamps load as text, so
  `date_range` and `no_future` silently checked nothing from the CLI. `temporal_consistency` now **warns**
  (`date_range checked nothing: ...`, `<col>: not a timestamp column ...; not checked`). No gate result that was an
  error changed; only warnings were added (visible with `--strict`).

Regression tests in `tests/quality/test_verify_config.py` (the CLI runs the four gates and gives the same errors as
the Python API; config without a schema; file gate; report; 11 bad-config cases; versioned document; missing-key
message; the temporal warnings). They fail before the fix (no `--config`, no `VerifyConfig`).

Not done: a baseline given as a profile file (only the inline dtype map), and `--config` flags for single rules;
say if wanted.

## #7: Fabric gate pipelines run `%pip` without enabling it

**Reproduced** (static; no workspace): `shape_profile`, `shape_generate` and `shape_profile_domain` install Shape
with `%pip install --find-links builtin ...`; `grep -r _inlineInstallationEnabled integrations/` found nothing;
`shape_gate_notebook` and `shape_generate_gate` ran them with no such parameter.
Current Microsoft Learn (read in this session: "Manage Apache Spark libraries", Python inline installation, and
"Use Python experience on Notebook", Known limitations): inline `%pip` is disabled by default in notebook pipeline
runs and is enabled with a Boolean notebook-activity parameter `_inlineInstallationEnabled = true`; **Environment
integration isn't available on Python notebooks**; `%pip` is not supported in High Concurrency mode or in
reference runs; `%pip` can give different results run to run and Microsoft recommends an Environment for
pipelines. So for a Python notebook the supported mechanism in a pipeline is `%pip` plus the parameter; the
Environment route exists only for the PySpark notebook (pipeline (a2), which has no `%pip`).
Synapse notebooks and pipelines use pool or workspace packages and have no `%pip`; ADF uses a container: not
affected. `src/shape/integrations/fabric/spark.py`'s docstring no longer says `%pip` for Synapse.

**Fix**: `build_pipelines.py` adds `{"value": true, "type": "bool"}` as `_inlineInstallationEnabled` on the notebook
activities of `shape_gate_notebook` (`ProfileTable`) and `shape_generate_gate` (`GenerateDomain`,
`ProfileAndCheck`); not on `shape_gate_spark`. The three committed pipeline JSONs were regenerated. The three Python
notebooks' install cells say so in a comment (`build_notebooks.py`, `.ipynb` patched with the same text; the
committed notebook JSON is otherwise unchanged). RUNBOOK sections 4, 7.2, 9, 10 (new item 13), 11, 12.1 and 12.6,
and `environment/README.md` updated.

Regression test `tests/demo/fabric/test_inline_install.py`: every pipeline notebook activity whose notebook has a
`%pip` cell passes the Boolean flag and no other does (fails before: 3 of 5 cases). Two existing tests that
compare the passed parameters with the notebook's parameter cell now ignore that one name
(`test_pipelines.py`, `test_generate.py`): Fabric reads it, the notebook does not.

`[VERIFY]` (owner, O-07; RUNBOOK section 10 item 13 and the section 11 checklist): the parameter is accepted as a
Boolean in the imported definition and makes `%pip` run in a pipeline run (and without it the run fails at
`import shape`); the `builtin` wheels install in a pipeline run; no High Concurrency tag is set. Not addressed
(issue's "Related"): a business-date pipeline parameter and a baseline lookup for daily runs.

## Checks (this session, after the last code change)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_spindle) | All checks passed / 715 files already formatted |
| `mypy` (strict) | Success: no issues found in 308 source files |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | no output |
| `lint-imports` | Contracts: 1 kept, 0 broken |
| `python scripts/check_user_facing.py` | clean |
| `bandit -q -r src -ll` | exit 0 |
| START (median of 10 `shape --version`) | 62 ms |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric` | 4521 passed, 46 deselected |
| same with `SHAPE_KERNEL=python` | 4521 passed, 46 deselected |
| `pytest tests/demo/fabric tests/demo/content` (shape-fabric venv) | 254 passed |

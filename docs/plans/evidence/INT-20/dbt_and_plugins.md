# INT-20: shape-dbt fix check and the healthcare-without-faker test move

Tree: `int/INT-20` at `0f2f75c6` plus the move below. Fresh session venv per §1: Python 3.11.15,
`pip install -e ".[dev,streaming,advanced]"` (maturin built the Rust extension), every `plugins/*`
editable, `pip install -r tests/demo/fabric/requirements.txt`, `libodbc2`, dbt-core 1.12.5 and
dbt-duckdb 1.11.0 (pyarrow 19.0.1, Faker 40.40.0, pytest 9.1.1). Pinned baseline checkout cloned
and set up per §1.2 (HEAD 422e78d), only read. Each run had its own `TMPDIR`; no `--basetemp`.

## 1. shape-dbt after 0f2f75c6 (`fromdbt` no longer passes `raw_type`)

The `dbt` tests need `dbt deps`; the hub's tarball host is blocked here, so, as in
`docs/plans/lane_status/INT-16.md`, `SHAPE_DBT_PACKAGES_FILE` pointed at a `packages.yml` with
`local:` clones (session scratchpad, outside the repository) of dbt-utils 1.4.1 and
dbt-expectations 0.10.10. dbt-expectations' own `packages.yml` in that clone points at a local
dbt-date 0.21.0 clone: listing dbt-date at the top level as well makes `dbt deps` stop with
"Found duplicate project dbt_date" (seen on the first attempt; 7 errors, all at `dbt deps`).

```bash
source scripts/env.sh && SHAPE_KERNEL=<k> SHAPE_DBT_PACKAGES_FILE=<scratch>/packages.yml \
  pytest -m "not emulator and not live" plugins/shape-dbt/tests
```

| Kernel | Result (includes the 7 tests in `test_dbt_build.py`) |
|---|---|
| rust | 115 passed |
| python | 115 passed |

No failure, so no comparison run on `origin/build/main-plan` was needed and there is nothing to
classify.

## 2. `test_healthcare_inference_without_faker_fails_and_names_the_extra`

Reproduced first: with the 12 first-party plugins uninstalled, the test as it was at `0f2f75c6` in
`tests/plugins/test_optional_plugin_pieces.py` fails (1 failed, 8 passed in that file): the run
needs the shape-domains plugin's healthcare domain.

Moved unchanged (same name, same two assertions, same subprocess import blocker for `faker`) to
`plugins/shape-domains/tests/test_healthcare_without_faker.py`, with the `_ABSENT` /
`_run_without` helpers it uses. `tests/plugins/test_optional_plugin_pieces.py` drops the test and
the helpers only it used, and its docstring names the new place. Nothing skipped or xfailed. The
moved test passing shows the `faker` isolation still works there: with `faker` importable the
command would exit 0, not 1.

```bash
source scripts/env.sh && SHAPE_KERNEL=<k> pytest -m "not emulator and not live" tests/plugins
source scripts/env.sh && SHAPE_KERNEL=<k> pytest -m "not emulator and not live" plugins/shape-domains/tests
```

| Run | rust | python |
|---|---|---|
| `tests/plugins`, plugins installed | 485 passed | 485 passed |
| `tests/plugins`, all 12 plugins uninstalled | 482 passed, 3 skipped | 482 passed, 3 skipped |
| `tests/plugins`, plugins reinstalled (editable) | 485 passed | 485 passed |
| `plugins/shape-domains/tests` | 13 passed | 13 passed |

The 3 skips with plugins uninstalled are the existing `pytest.importorskip("shape_behavior")`
calls in `tests/plugins/test_plugin_kit_install.py` (lines 193, 200, 219), not changed here.

## 3. Static checks

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1990 files already formatted |
| `mypy` | no issues in 684 source files |
| `python scripts/check_user_facing.py` | clean |

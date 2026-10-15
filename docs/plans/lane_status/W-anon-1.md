# W-anon-1 — Withdraw the incompatible Anonymeter extra for 0.9.4

Status: implemented; plugin checks, wheel builds, clean install and documentation checks pass.
Broader core packaging checks have an environment blocker; see open questions.

## Decision and scope

Owner decision dated 2026-10-15: this closes the W5-08 / issue #84 pin conflict
for 0.9.4 by withdrawal. The adapter remains and still imports independently installed
Anonymeter; only the missing-library error changes. All five other extras and their
requirements remain exactly unchanged. No version or core dependency changes.
W-uv-1 can resolve the workspace without the incompatible extra.

For the lead: COMPLETION_PLAN.md row **138** (currently source line 2183) records
W5-08's conflict. Its decisions and gates were not edited. Historical W5-08 status
was left intact. Authoring's claim of one extra per integration was corrected;
trust-model.md has no install claim requiring an edit. Existing integration-test
gating is unchanged; no skip or xfail was added.

Base: `origin/main` at `a8e476c`.
Origin verified as `https://github.com/sqllocks/shape.git` before fetching.
`git fetch origin` and `git checkout -B lane/W-anon-1 origin/main`: exit 0.

## Commands and results

Commands below ran from the checkout with `.venv/bin` tools unless an absolute path
or different working directory is shown. Python 3.12.14; pytest 9.1.1.

| Command | Exit | Result |
|---|---:|---|
| `python -m pip wheel --no-deps --no-build-isolation plugins/shape-integrations -w /tmp/W-anon-1/base` | 0 | Built on origin/main before implementation |
| Initial red pytest invocation before editable plugin setup | 4 | Missing `shape_integrations` at collection; setup corrected below |
| `python -m pip install --no-deps --no-build-isolation -e plugins/shape-integrations` | 0 | Test environment setup |
| Red command below | 1 | Both requested regression tests failed for expected reasons |
| Initial `pytest plugins/shape-integrations -m "not emulator and not live"` | 1 | 151 passed, 20 existing skips; new test incorrectly expected ImportError.name to survive its constructor; corrected the assertion |
| Initial `ruff check plugins/shape-integrations` / `ruff format --check plugins/shape-integrations` | 1 / 1 | Message line formatting corrected |
| `pytest plugins/shape-integrations -m "not emulator and not live"` | 0 | 152 passed, 20 skipped by unchanged optional-library gates |
| `ruff check plugins/shape-integrations` | 0 | Clean |
| `ruff format --check plugins/shape-integrations` | 0 | 28 files formatted |
| `python scripts/check_plugin_skeletons.py` | 0 | 12 distributions, version 0.9.3 |
| `python scripts/check_versions.py` | 0 | 58 locations agree on 0.9.3 |
| Inventory command below | 1 | 245 passed, 16 setup errors: Cargo unavailable while maturin builds core sdist |
| `python -m pip wheel --no-deps --no-build-isolation plugins/shape-integrations -w /tmp/W-anon-1/lane` | 0 | Lane wheel built |
| `python /tmp/W-anon-1/metadata.py` | 0 | Dependency headers differ only by the two required removals; full diff below |
| `python -m venv /tmp/W-anon-1/clean` | 0 | Fresh venv without system packages |
| `/tmp/W-anon-1/clean/bin/pip install /tmp/W-anon-1/lane/*.whl` (from `/tmp`) | 0 | PyPI core 0.9.3, NumPy 2.5.4, PyArrow 26.0.0 and lane plugin installed |
| Clean-venv Python fixture preparation and assertions | 0 | Wrote three Parquet tables; asserted core version 0.9.3 and Anonymeter absent |
| `/tmp/W-anon-1/clean/bin/shape evaluate anonymeter /tmp/W-anon-1/real /tmp/W-anon-1/synth --control /tmp/W-anon-1/control` (from `/tmp`) | 2 | Expected missing-Anonymeter exit; output below |
| `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` | 0 | Strict build passed |
| `python scripts/check_doc_links.py --site site` | 0 | Zero broken links |
| `python scripts/check_user_facing.py --site site` | 0 | Clean |
| Initial staged `git diff --cached --check` | 2 | Captured output contained trailing whitespace; normalized it |
| Final `git diff --cached --check` | 0 | No whitespace errors |
| `git diff --stat origin/main` | 0 | Only scoped plugin files, tests, docs, changelog and this status |

Inventory command:

```sh
pytest tests/plugins/test_extras.py tests/release/test_packaging.py tests/release/test_docs.py tests/release/test_offline_lock.py tests/packaging/test_dependency_floors.py tests/packaging/test_pure_wheel_metadata.py
```

The searches across scripts/ and tests/ found extras inventory handling in version,
offline-lock, release documentation and packaging checks. No existing check was
changed. All 16 errors occur in unchanged core-sdist fixtures before comparisons:

```text
maturin failed
Caused by: Cargo metadata failed. Do you have cargo in your PATH?
Caused by: No such file or directory (os error 2)
245 passed, 16 errors in 27.22s
```

## Red-first evidence

Only tests had changed when this ran; production files still matched origin/main.

```sh
pytest plugins/shape-integrations/tests/test_skeleton.py::test_published_extras_withdraw_anonymeter_and_preserve_other_requirements plugins/shape-integrations/tests/test_evaluate_unit.py::test_without_the_library_the_command_exits_2_with_actionable_message -k 'not sdmetrics'
```

Exit 1. Complete output (trailing whitespace normalized):

```text
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspace/shape
configfile: pyproject.toml
plugins: Faker-40.43.0, hypothesis-6.169.1, platformdirs-4.13.0, cov-7.1.0, sqllocks-shape-0.9.2
collected 3 items / 1 deselected / 2 selected

plugins/shape-integrations/tests/test_skeleton.py F                      [ 50%]
plugins/shape-integrations/tests/test_evaluate_unit.py F                 [100%]

=================================== FAILURES ===================================
__ test_published_extras_withdraw_anonymeter_and_preserve_other_requirements ___

    def test_published_extras_withdraw_anonymeter_and_preserve_other_requirements():
        extras = _project()["optional-dependencies"]
>       assert extras == {
            "openlineage": ["openlineage-python>=1.20,<2"],
            "mlflow": ["mlflow>=3,<4"],
            "presidio": ["presidio-analyzer>=2.2,<3"],
            "sdmetrics": ["sdmetrics>=0.18,<1"],
            "ibis": ["ibis-framework[duckdb]>=12,<13", "duckdb>=1.2,<2"],
        }
E       AssertionError: assert {'openlineage....18,<1'], ...} == {'openlineage....18,<1'], ...}
E
E         Omitting 5 identical items, use -vv to show
E         Left contains 1 more item:
E         {'anonymeter': ['anonymeter>=1,<2']}
E         Use -v to get more diff

plugins/shape-integrations/tests/test_skeleton.py:46: AssertionError
_ test_without_the_library_the_command_exits_2_with_actionable_message[anonymeter-anonymeter-extra_args1] _

dirs = ('/tmp/pytest-of-agent/pytest-0/test_without_the_library_the_c0/real', '/tmp/pytest-of-agent/pytest-0/test_without_the_library_the_c0/synth', '/tmp/pytest-of-agent/pytest-0/test_without_the_library_the_c0/control')
capsys = <_pytest.capture.CaptureFixture object at 0x7fc45d307470>
hide_library = <function hide_library.<locals>.hide at 0x7fc45d063ce0>
tool = 'anonymeter', library = 'anonymeter', extra_args = ['--control', 'C']

    @pytest.mark.parametrize(
        ("tool", "library", "extra_args"),
        [("sdmetrics", "sdmetrics", []), ("anonymeter", "anonymeter", ["--control", "C"])],
    )
    def test_without_the_library_the_command_exits_2_with_actionable_message(
        dirs, capsys, hide_library, tool, library, extra_args
    ):
        hide_library(library)
        args = [a if a != "C" else dirs[2] for a in extra_args]
        assert run(tool, dirs[0], dirs[1], *args) == 2
        if tool == "anonymeter":
>           assert capsys.readouterr().err.strip() == (
                "shape: error: The 'anonymeter' extra was withdrawn in 0.9.4 because "
                "Anonymeter 1.x requires NumPy < 1.27 and Shape requires NumPy >= 2. "
                "The Anonymeter report becomes available again when Anonymeter supports NumPy 2."
            )
E           assert "shape: error...[anonymeter]'" == 'shape: error...orts NumPy 2.'
E
E             - shape: error: The 'anonymeter' extra was withdrawn in 0.9.4 because Anonymeter 1.x requires NumPy < 1.27 and Shape requires NumPy >= 2. The Anonymeter report becomes available again when Anonymeter supports NumPy 2.
E             + shape: error: Anonymeter needs the 'anonymeter' extra: pip install 'sqllocks-shape-integrations[anonymeter]'

plugins/shape-integrations/tests/test_evaluate_unit.py:224: AssertionError
=========================== short test summary info ============================
FAILED plugins/shape-integrations/tests/test_skeleton.py::test_published_extras_withdraw_anonymeter_and_preserve_other_requirements
FAILED plugins/shape-integrations/tests/test_evaluate_unit.py::test_without_the_library_the_command_exits_2_with_actionable_message[anonymeter-anonymeter-extra_args1]
======================= 2 failed, 1 deselected in 0.75s ========================
```

## Clean-venv CLI output

```text
shape: error: The 'anonymeter' extra was withdrawn in 0.9.4 because Anonymeter 1.x requires NumPy < 1.27 and Shape requires NumPy >= 2. The Anonymeter report becomes available again when Anonymeter supports NumPy 2.
```

## Wheel METADATA comparison

Both wheels use the same setuptools backend, no build isolation, and version 0.9.3.
The header assertion compares every header line, requiring exactly these removals
and no additions:

```diff
-Provides-Extra: anonymeter
-Requires-Dist: anonymeter<2,>=1; extra == "anonymeter"
```

Full METADATA diff (including the README long description):

```diff
--- origin/main/METADATA
+++ lane/METADATA
@@ -27,8 +27,6 @@
 Requires-Dist: presidio-analyzer<3,>=2.2; extra == "presidio"
 Provides-Extra: sdmetrics
 Requires-Dist: sdmetrics<1,>=0.18; extra == "sdmetrics"
-Provides-Extra: anonymeter
-Requires-Dist: anonymeter<2,>=1; extra == "anonymeter"
 Provides-Extra: ibis
 Requires-Dist: ibis-framework[duckdb]<13,>=12; extra == "ibis"
 Requires-Dist: duckdb<2,>=1.2; extra == "ibis"
@@ -37,7 +35,7 @@
 # sqllocks-shape-integrations

 Thin, optional adapters between Shape and OpenLineage, MLflow, Presidio, SDMetrics, Anonymeter
-and Ibis/DuckDB. One distribution, one extra per integration:
+and Ibis/DuckDB. One distribution, with extras for the available integrations:

 ```
 pip install 'sqllocks-shape-integrations[openlineage]'
@@ -45,3 +43,5 @@

 See [docs/plugins/integrations.md](../../docs/plugins/integrations.md) for each integration:
 install, command or API, what is sent or written, and what is never sent.
+
+Anonymeter adapter: **withdrawn in 0.9.4: Anonymeter 1.x requires NumPy < 1.27; Shape requires NumPy >= 2**. The adapter code stays for independently installed Anonymeter in a separate environment. The Anonymeter report becomes available again when Anonymeter supports NumPy 2. Anonymeter measures privacy risk; Shape does not make data anonymous.
```

## Open questions / verification limits

- The request requires updating README.md and also requires the full METADATA diff
  to contain only two removed lines. Setuptools embeds that README as METADATA's
  long description, so these requirements conflict. **May the two-line-only rule
  apply to METADATA headers, with the required README changes in its body?**
  No packaging configuration or metadata check was changed to hide this difference.
- Broader core packaging validation needs Cargo. **Can the lead rerun the inventory
  command in the standard Rust-enabled environment?** No gate was lowered and no
  release/build files were changed. The missing tool is unrelated to removing the extra.

## Changed files

- `plugins/shape-integrations/pyproject.toml`: remove only the Anonymeter extra line.
- `plugins/shape-integrations/src/shape_integrations/extras.py`: published extras inventory and Anonymeter-only missing-library message.
- `plugins/shape-integrations/src/shape_integrations/anonymeter_eval.py`: correct obsolete extra reference in module docstring; adapter implementation unchanged.
- `plugins/shape-integrations/tests/test_skeleton.py`: exact remaining requirements and preserved missing-library error type.
- `plugins/shape-integrations/tests/test_evaluate_unit.py`: withdrawal message and exit 2.
- `plugins/shape-integrations/README.md`, `docs/plugins/integrations.md`, `docs/plugins/authoring.md`: withdrawal and corrected availability.
- `CHANGELOG.md`: requested Unreleased entry.
- `docs/plans/lane_status/W-anon-1.md`: decision and evidence.

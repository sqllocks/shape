# W-cli-polish-1 — 0.9.4

Base: `origin/main` at `a8e476c`; origin verified as `https://github.com/sqllocks/shape.git`.
Branch: `lane/W-cli-polish-1`. No AGENTS.md exists in this checkout or its parent directories.
Read CLAUDE.md, CONTRIBUTING.md and all requested CLI, Excel, chaos, landing, Action, PR bot and Fabric docs, plus action.yml and its comment renderer.
W-uv-1 is absent from main; use the existing `.venv` and Makefile workflow.

## Baseline regression evidence (before implementation changes)

Each test below was added to the unmodified origin/main implementation and run separately with `.venv/bin/pytest -q NODE`. No skip, xfail or gate adjustment was added.

### Item a

`tests/cli/test_profile_generation_cli.py::test_plan_defaults_to_text_and_json_remains_structured` — exit 1.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________ test_plan_defaults_to_text_and_json_remains_structured ____________

sales_profile = PosixPath('/tmp/pytest-of-agent/pytest-0/test_plan_defaults_to_text_and0/sales.shape')
capsys = <_pytest.capture.CaptureFixture object at 0x7f1f2d79f470>

    def test_plan_defaults_to_text_and_json_remains_structured(sales_profile, capsys):
        code, out, _ = run(capsys, "plan", sales_profile, "--status", "not_modelled")
        assert code == 0
>       assert not out.lstrip().startswith("{")
E       assert not True
E        +  where True = <built-in method startswith of str object at 0xcd72440>('{')
E        +    where <built-in method startswith of str object at 0xcd72440> = '{"counts": {"approximate": 22, "not_modelled": 14, "preserved": 53}, "executable": true, "items": [{"evidence": "sale..., {"evidence": "dataset.row_order", "reason": "the order of the rows is not reproduced", "status": "not_modelled"}]}\n'.startswith
E        +      where '{"counts": {"approximate": 22, "not_modelled": 14, "preserved": 53}, "executable": true, "items": [{"evidence": "sale..., {"evidence": "dataset.row_order", "reason": "the order of the rows is not reproduced", "status": "not_modelled"}]}\n' = <built-in method lstrip of str object at 0xcd72440>()
E        +        where <built-in method lstrip of str object at 0xcd72440> = '{"counts": {"approximate": 22, "not_modelled": 14, "preserved": 53}, "executable": true, "items": [{"evidence": "sale..., {"evidence": "dataset.row_order", "reason": "the order of the rows is not reproduced", "status": "not_modelled"}]}\n'.lstrip

tests/cli/test_profile_generation_cli.py:181: AssertionError
=========================== short test summary info ============================
FAILED tests/cli/test_profile_generation_cli.py::test_plan_defaults_to_text_and_json_remains_structured
1 failed in 1.36s
```

### Item b

`tests/chaos/test_input_check.py::test_chaos_reads_write_landing_output` — exit 1.

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
__________________ test_chaos_reads_write_landing_output[csv] __________________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-1/test_chaos_reads_write_landing0')
capsys = <_pytest.capture.CaptureFixture object at 0x7f7954483500>, fmt = 'csv'

    @pytest.mark.parametrize("fmt", ["csv", "jsonl", "parquet"])
    def test_chaos_reads_write_landing_output(tmp_path, capsys, fmt):
        from shape.generation.landing import write_landing
        from shape.io.provenance import write_provenance

        src, out = tmp_path / "landing", tmp_path / "out"
        table = pa.table({"id": [1, 2], "amount": [5, 6]})
        files = write_landing({"orders": table}, src, default_format=fmt, batch_date="2026-10-15")
        write_provenance(src, [(f.path, f.rows) for f in files], seed=42)
        code, _, err = run(
            capsys,
            "chaos",
            "--input",
            src,
            "-o",
            out,
            "--corrupt",
            "duplicates=0.5@orders",
            "-f",
            "parquet",
        )
>       assert code == 0, err
E       AssertionError: shape: error: no CSV, Parquet or JSON Lines files found in /tmp/pytest-of-agent/pytest-1/test_chaos_reads_write_landing0/landing
E
E       assert 2 == 0

tests/chaos/test_input_check.py:245: AssertionError
_________________ test_chaos_reads_write_landing_output[jsonl] _________________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-1/test_chaos_reads_write_landing1')
capsys = <_pytest.capture.CaptureFixture object at 0x7f795220bec0>
fmt = 'jsonl'

    @pytest.mark.parametrize("fmt", ["csv", "jsonl", "parquet"])
    def test_chaos_reads_write_landing_output(tmp_path, capsys, fmt):
        from shape.generation.landing import write_landing
        from shape.io.provenance import write_provenance

        src, out = tmp_path / "landing", tmp_path / "out"
        table = pa.table({"id": [1, 2], "amount": [5, 6]})
        files = write_landing({"orders": table}, src, default_format=fmt, batch_date="2026-10-15")
        write_provenance(src, [(f.path, f.rows) for f in files], seed=42)
        code, _, err = run(
            capsys,
            "chaos",
            "--input",
            src,
            "-o",
            out,
            "--corrupt",
            "duplicates=0.5@orders",
            "-f",
            "parquet",
        )
>       assert code == 0, err
E       AssertionError: shape: error: no CSV, Parquet or JSON Lines files found in /tmp/pytest-of-agent/pytest-1/test_chaos_reads_write_landing1/landing
E
E       assert 2 == 0

tests/chaos/test_input_check.py:245: AssertionError
________________ test_chaos_reads_write_landing_output[parquet] ________________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-1/test_chaos_reads_write_landing2')
capsys = <_pytest.capture.CaptureFixture object at 0x7f7951b15f70>
fmt = 'parquet'

    @pytest.mark.parametrize("fmt", ["csv", "jsonl", "parquet"])
    def test_chaos_reads_write_landing_output(tmp_path, capsys, fmt):
        from shape.generation.landing import write_landing
        from shape.io.provenance import write_provenance

        src, out = tmp_path / "landing", tmp_path / "out"
        table = pa.table({"id": [1, 2], "amount": [5, 6]})
        files = write_landing({"orders": table}, src, default_format=fmt, batch_date="2026-10-15")
        write_provenance(src, [(f.path, f.rows) for f in files], seed=42)
        code, _, err = run(
            capsys,
            "chaos",
            "--input",
            src,
            "-o",
            out,
            "--corrupt",
            "duplicates=0.5@orders",
            "-f",
            "parquet",
        )
>       assert code == 0, err
E       AssertionError: shape: error: no CSV, Parquet or JSON Lines files found in /tmp/pytest-of-agent/pytest-1/test_chaos_reads_write_landing2/landing
E
E       assert 2 == 0

tests/chaos/test_input_check.py:245: AssertionError
=========================== short test summary info ============================
FAILED tests/chaos/test_input_check.py::test_chaos_reads_write_landing_output[csv]
FAILED tests/chaos/test_input_check.py::test_chaos_reads_write_landing_output[jsonl]
FAILED tests/chaos/test_input_check.py::test_chaos_reads_write_landing_output[parquet]
3 failed in 1.00s
```

### Item c

`tests/excel/test_excel_source.py::test_six_datetime_sentinel_cells_are_reported` — exit 0.

```text
.                                                                        [100%]
1 passed in 0.51s
```

### Item d

`tests/prbot/test_action.py::test_missing_baseline_comment_keeps_the_source_name` — exit 1.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____________ test_missing_baseline_comment_keeps_the_source_name ______________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-3/test_missing_baseline_comment_0')

    def test_missing_baseline_comment_keeps_the_source_name(tmp_path):
        project = tmp_path / "project"
        shutil.copytree(FIXTURES / "pass", project)
        (project / "baseline" / "orders.shape").unlink()
        ran = _run_action(tmp_path, str(project))
        assert ran["code"] == 0 and ran["gate"] == 1
>       assert "### Source: orders\n" in ran["summary"]
E       AssertionError: assert '### Source: orders\n' in '<!-- shape-pr-comment -->\n## Shape data check\n\n**Verdict: fail** - 1 source, 0 findings\n\n### Source: orders.resu...he command exited with code 2 and listed no findings: see the job log for the reason.\n\n---\n<sub>Shape 0.9.3</sub>\n'

tests/prbot/test_action.py:466: AssertionError
=========================== short test summary info ============================
FAILED tests/prbot/test_action.py::test_missing_baseline_comment_keeps_the_source_name
1 failed in 1.60s
```

## Decisions

- a: CLI_STABILITY.md applies its promises to future 1.x, excludes human layout from compatibility, and explicitly permits documented-behavior fixes. Keep the structured `--json` keys and envelope.
- c: The reported failure does not reproduce on origin/main: six real datetime cells yield count 6, share 6/7, all six cell coordinates and five ISO examples. `_column_findings` already checks datetime/date values against `SENTINEL_DATES`; the existing mixed text/datetime test also checks both coordinates. Intent is typed dates as well as text. Retain the implementation and add explicit regression coverage and documentation; a red result cannot honestly be recorded for this case.
- e: `src/shape/cli/stability.py` defines levels: `ci` is absent from `STABLE`, hence experimental. `annotate` applies that status; `scripts/cli_surface.py` and `tests/cli/test_cli_surface.py` enforce it against `tests/cli/cli_surface_v1.json`. PR_BOT.md and CI.md already agree. Make GITHUB_ACTION.md experimental too, without promotion.

## Item results

- a: green, exit 0: 134 tests across the profile generation and schema CLI, vault, proposals, safe capture and mixed copula modules. Command: `.venv/bin/pytest -q tests/cli/test_profile_generation_cli.py tests/cli/test_iss_gen_cli.py tests/vault/test_generate.py tests/proposals/test_type_proposals.py tests/proposals/test_cli.py tests/safecapture/test_readers.py tests/joint/test_mixed_copula_generation.py`. Default text covers profile, evidence and generation-schema paths; semantic JSON tests now request `--json` explicitly. JSON structure and status filtering are preserved.

Additional b provenance refusals, after correcting the test setup to name a corruption, before implementation: `.venv/bin/pytest -q tests/chaos/test_input_check.py -k nested_landing` — exit 1, 2 failed (expected marked/hash diagnostics, got no data files).

- b: green, exit 0: `.venv/bin/pytest -q tests/chaos tests/io/test_provenance.py tests/cli/test_incremental_cli.py` — 213 passed. Covers all three `write_landing` formats, nested provenance verification/refusal, multi-date concatenation, deterministic path order, ground-truth-log exclusion, and reading chaos landing output again. Single-file Parquet reads preserve the data schema without an inferred partition column.

- c: green before and after documentation clarification; `.venv/bin/pytest -q tests/excel` — exit 0, 64 passed, one expected unsigned-artifact warning. No runtime change was needed or made. This is the sole exception to the requested red/green sequence because the reported behavior already works on the base revision.

- d: green, exit 0: `.venv/bin/pytest -q tests/prbot` — 152 passed. Includes the missing-baseline Action shell integration and filename fallback coverage for `orders`, `orders.result`, and `orders.daily`. Additional pre-fix fallback test: `.venv/bin/pytest -q tests/prbot/test_comment.py::test_action_result_filename_fallback_preserves_source_name` — exit 1, 3 failed. Project annotations still take precedence over filename fallback; verdict/gate logic is unchanged.

- e: corrected the Fabric plugin publication sentence to use PyPI installs and changed GITHUB_ACTION.md to experimental. PR_BOT.md already has the correct status and requires no edit. An inline Python assertion comparing both page statuses with `shape.cli.stability.level("ci")` and checking the Fabric publication sentence exited 0.

## Verification

| Check | Exit | Evidence |
|---|---:|---|
| Item a baseline / green | 1 / 0 | Above; 134 related tests green |
| Item b baseline / green | 1 / 0 | Above; 213 related tests green |
| Item c baseline / green | 0 / 0 | Already supported on main; 64 Excel tests green |
| Item d baseline / green | 1 / 0 | Above; 152 PR bot / Action tests green |
| Demo CLI and landing / batch modules | 0 | `pytest -q tests/demo/core/test_cli.py tests/iss_gaps/test_landing_and_batches.py`: 41 passed |
| CLI modules, unrestricted | 1 | 835 passed; one existing skip for absent local databases plugin and one live Azure TCP failure (DNS passes). No skips added. Both the live test and doctor_checks.py are unchanged from origin/main (`git diff --exit-code`: 0). |
| Install local databases plugin | 0 | `.venv/bin/pip install --no-deps --no-build-isolation -e plugins/shape-databases`; resolves the missing-plugin prerequisite |
| Strict docs build | 0 | `NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict`; built in 31.49s, no warnings |
| Docs links | 0 | `.venv/bin/python scripts/check_doc_links.py --site site`: 0 broken links |
| User-facing wording | 0 | `.venv/bin/python scripts/check_user_facing.py --site site`: clean |
| Docs language | 0 | `.venv/bin/python scripts/check_docs_language.py --site site`: 0 hits |
| Working diff whitespace | 0 | `git diff --check` |

The initial unrestricted CLI run included existing live tests; the failing one attempts a direct TCP connection to Azure. The selected cloud environment uses an HTTP proxy and has no configured TCP grants. This is recorded as an environment limitation, not suppressed or changed. Non-live CLI and full make results follow below.

## Changed files

- `CHANGELOG.md`
- `docs/CHAOS.md`
- `docs/CLI.md`
- `docs/EXCEL.md`
- `docs/GITHUB_ACTION.md`
- `docs/LANDING.md`
- `docs/plans/lane_status/W-cli-polish-1.md`
- `plugins/shape-fabric/README.md`
- `src/shape/chaos/input_check.py`
- `src/shape/cli/chaos.py`
- `src/shape/cli/findings.py`
- `src/shape/cli/incremental.py`
- `src/shape/cli/main.py`
- `src/shape/io/provenance.py`
- `tests/chaos/test_input_check.py`
- `tests/cli/test_iss_gen_cli.py`
- `tests/cli/test_profile_generation_cli.py`
- `tests/demo/core/test_cli.py`
- `tests/excel/test_excel_source.py`
- `tests/joint/test_mixed_copula_generation.py`
- `tests/prbot/test_action.py`
- `tests/prbot/test_comment.py`
- `tests/proposals/test_cli.py`
- `tests/proposals/test_type_proposals.py`
- `tests/safecapture/test_readers.py`
- `tests/vault/test_generate.py`

## Broader check diagnostics

- Non-live CLI command: `.venv/bin/pytest -q -m "not emulator and not live and not heavy" tests/cli` — exit 0, 835 passed, 2 existing live tests deselected, no skips, after installing the local databases plugin.
- `PATH="/workspace/shape/.venv/bin:$PATH" make check` — exit 2, interrupted for triage after failure markers during the core coverage phase. All preceding Makefile commands exited 0: Ruff lint, Ruff format, mypy (701 source files), compileall, Vulture, import-linter, requirements, secrets, user-facing wording, exit-code/failure-mode generation, shipped data, plugin skeletons, versions, release workflows, conformance coverage, CLI surface and v1 proof checks. The interruption triggered a pytest tmp_path teardown KeyError; no complete coverage result was produced. The failure cache named the existing permissions test and six new landing cases.
- Focused triage: `.venv/bin/pytest -q tests/chaos/test_input_check.py tests/builtins/test_bugs_builtins_file_sink_atomic.py` — exit 1, 27 passed and the existing permissions test failed (output mode 0600 under this environment's restrictive umask).
- The permissions test copied verbatim from origin/main also exits 1 with mode 0600. Its source and the sink implementation are unchanged (`git diff --exit-code origin/main -- src/shape/builtins/sinks tests/builtins/test_bugs_builtins_file_sink_atomic.py`: exit 0).
- Expanded fast check with a normal umask: `umask 022; .venv/bin/pytest -q -x -m "not emulator and not live and not heavy" tests/api tests/artifact tests/benchmarks tests/bridge tests/builtins tests/capture tests/chaos` — exit 0, 2,416 passed, 13 deselected. This includes the formerly failing permissions case and all landing tests.
- `.venv/bin/pytest -q -x tests/capture tests/chaos` — exit 0, 225 passed.
- Full-collection landing selection — exit 0, 6 passed, 18,719 deselected; collection reported one existing optional Pydantic-v2 skip. An exploratory `--lf --collect-only` command without the Makefile's directory exclusions exits 2 because the Fabric demo conftest needs nbformat; it was not used as a verification result.
- Mistake, discarded as verification: one focused diagnostic used `--cov=shape --cov-fail-under=0` and exited 0 (23 tests, 14% partial coverage). That invocation lowered the threshold for that diagnostic and should not have done so. No repository gate/configuration was modified; its result is not counted as a passing coverage check. The full coverage reproduction uses the unchanged 86% floor.
- In the full coverage reproduction under umask 022, the landing cases passed after the preceding modules; the six initial failure-cache entries have not reproduced. No test was skipped/xfail-ed or altered to suppress them.

- Full coverage reproduction with the original gate: `umask 022; .venv/bin/pytest -q -x -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86` — exit 143, deliberately terminated after passing the landing cases and reaching 16% without a failure marker. This was diagnostic, not a completed coverage check. Finish uses the authorized fast-check alternative; full coverage, heavy, Python-kernel-only and Rust stages were not completed.

## Final disposition

All five items have a separate signed-off commit. Items a, b and d have recorded red-to-green regressions. Item c already works on origin/main and has explicit six-datetime-cell coverage plus clarified docs. Item e follows the existing experimental status source. No skip/xfail or repository gate changes were added. No PR, tag, release, or push outside lane/W-cli-polish-1 is part of this task.

Open implementation questions: none. Limitations: the original Excel workbook was not supplied; its reported difference cannot be explained from the six-cell reproduction. Full make verification is incomplete as detailed above.

Final commit checks: DCO exit 0 (all five signed off); consolidation tree comparison exit 0; final commit-range whitespace check exit 0 after removing trailing whitespace copied from pytest output (first range check exited 2).

## Main merge — 2026-10-15

Merged `origin/main` at `2d4e86e06f1f3ff90aae65aeefa54820a3f5bd3e` (including W-play-1 and W-anon-1) into the lane starting at `a8a5cb0519bed45b8b47d02d3179d66eabf70ef7`. This is a merge, with no rebase or force-push.

Only CHANGELOG.md conflicted. The resolution preserves main's Playground and integration entries and all five lane entries under Unreleased, including the exact explicit migration wording for `shape plan`. A comparison against origin/main of all other files changed by main exited 0: main's versions were retained. The merge-resolution changes are limited to CHANGELOG.md and this status file.

| Merge / resolution command | Exit | Result |
|---|---:|---|
| `git remote get-url origin` | 0 | Verified sqllocks/shape.git |
| `git branch --show-current` / `git status --short` | 0 / 0 | lane/W-cli-polish-1; initially clean |
| `git fetch origin` | 0 | Updated origin/main |
| `git merge --no-ff --no-commit origin/main` | 1 | Expected CHANGELOG.md conflict; resolved before committing |
| Python conflict resolution; `git add CHANGELOG.md` | 0 / 0 | Preserved both sets of Unreleased entries |
| Compare main's other changed files with origin/main | 0 | No differences |
| `git diff --cached --check` | 0 | No whitespace errors after resolution |

### Check environment and initial results

The first lane run passed 1,407 tests (2 existing live tests deselected), and every static/build/wording/link check passed. The first complete docs/release run exited 1: 541 passed, 16 failed, 3 errors. Failures included missing Playwright, missing source plugins, missing Cargo on PATH, differing NumPy/PyArrow/tzdata versions, and absent local dbt fixtures. Full results were retained while preparing the documented environment.

Installed requirements-docs.txt, all twelve first-party plugins from this checkout with --no-deps, a task-local minimal stable Rust toolchain, and the three pinned local dbt dependency repositories. The prepared commands inherit PATH entries for the lane's .venv and task-local Cargo, task-local RUSTUP_HOME/CARGO_HOME/CARGO_TARGET_DIR, SHAPE_DOCS_DBT_PACKAGES, a DOCS_PROXY_IP resolved from the environment's configured proxy and the system CA bundle. TLS verification remains enabled. A normal umask 022 is used. No test, gate, transcript, or main-owned file was changed to pass checks.

The managed Docker daemon check exited 0. One Cargo version probe without the task-local RUSTUP_HOME exited 1; the correctly configured probe exited 0 (Cargo 1.99.1). An exploratory transcript comparison using system Python exited 1 (pytest unavailable there); rerunning it with .venv Python exited 0. The original test workspaces were removed after preserving their transcripts to recover temporary disk space.

### Commands and exit codes

All commands below ran from /workspace/shape. Prepared checks use the environment described above. The eighteen `static-NN` entries were run twice (before and after preparation); both runs exited 0 for every entry. Their initial outputs are also preserved in the tool history.

| Run | Command | Exit |
|---|---|---:|
| cleanup-first-run | `.venv/bin/python /tmp/W-cli-polish-1-merge/cleanup.py` | 0 |
| cleanup-prepared-run | `.venv/bin/python /tmp/W-cli-polish-1-merge/cleanup-prepared.py` | 0 |
| dbt-packages | `.venv/bin/python scripts/docs_dbt_packages.py --directory /tmp/docs-dbt-packages` | 0 |
| dco | `.venv/bin/python scripts/check_dco.py origin/main HEAD` | 0 |
| docs-bootstrap-isolated | `.venv/bin/pytest -q 'tests/docs/test_reference_examples.py::test_reference_page[CONTRIBUTING]'` | 0 |
| docs-build-prepared | `env NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict` | 0 |
| docs-build | `env NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict` | 0 |
| docs-deps-dry-run | `.venv/bin/pip install --dry-run -r requirements-docs.txt` | 0 |
| docs-deps-install | `.venv/bin/pip install -r requirements-docs.txt` | 0 |
| docs-links-prepared | `.venv/bin/python scripts/check_doc_links.py --site site` | 0 |
| docs-links | `.venv/bin/python scripts/check_doc_links.py --site site` | 0 |
| docs-release-prepared | `.venv/bin/pytest -q -n 3 tests/docs tests/release` | 1 |
| docs-release | `.venv/bin/pytest -q tests/docs tests/release` | 1 |
| docs-user-facing-prepared | `.venv/bin/python scripts/check_user_facing.py --site site` | 0 |
| docs-user-facing | `.venv/bin/python scripts/check_user_facing.py --site site` | 0 |
| docs-wording-prepared | `.venv/bin/python scripts/check_docs_language.py --site site` | 0 |
| docs-wording | `.venv/bin/python scripts/check_docs_language.py --site site` | 0 |
| final-merge-proof | `.venv/bin/python /tmp/W-cli-polish-1-merge/proof.py` | 0 |
| lane-tests-prepared | `.venv/bin/pytest -q -m 'not emulator and not live and not heavy' tests/cli tests/chaos tests/excel tests/prbot tests/io/test_provenance.py tests/vault/test_generate.py tests/proposals/test_type_proposals.py tests/proposals/test_cli.py tests/safecapture/test_readers.py tests/joint/test_mixed_copula_generation.py tests/demo/core/test_cli.py tests/iss_gaps/test_landing_and_batches.py` | 0 |
| lane-tests | `.venv/bin/pytest -q -m 'not emulator and not live and not heavy' tests/cli tests/chaos tests/excel tests/prbot tests/io/test_provenance.py tests/vault/test_generate.py tests/proposals/test_type_proposals.py tests/proposals/test_cli.py tests/safecapture/test_readers.py tests/joint/test_mixed_copula_generation.py tests/demo/core/test_cli.py tests/iss_gaps/test_landing_and_batches.py` | 0 |
| main-build-inputs | `git diff --exit-code origin/main -- CONTRIBUTING.md Makefile pyproject.toml rust/shape-kernel` | 0 |
| rust-install | `env RUSTUP_HOME=/tmp/W-cli-polish-1-merge/rustup CARGO_HOME=/tmp/W-cli-polish-1-merge/cargo sh /tmp/W-cli-polish-1-merge/rustup-init.sh -y --profile minimal --default-toolchain stable --no-modify-path` | 0 |
| source-plugins | `.venv/bin/pip install --no-deps --no-build-isolation -e plugins/shape-behavior -e plugins/shape-databases -e plugins/shape-dbt -e plugins/shape-domains -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-healthcare-codes -e plugins/shape-healthcare-standards -e plugins/shape-integrations -e plugins/shape-kafka -e plugins/shape-simulation -e plugins/shape-sqlserver` | 0 |
| static-00 | `ruff check src tests plugins benchmarks/vs_refengine` | 0 |
| static-01 | `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 |
| static-02 | `mypy` | 0 |
| static-03 | `/workspace/shape/.venv/bin/python -m compileall -q src/shape` | 0 |
| static-04 | `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | 0 |
| static-05 | `lint-imports` | 0 |
| static-06 | `/workspace/shape/.venv/bin/python scripts/check_requirements.py` | 0 |
| static-07 | `/workspace/shape/.venv/bin/python scripts/check_secrets.py` | 0 |
| static-08 | `/workspace/shape/.venv/bin/python scripts/check_user_facing.py` | 0 |
| static-09 | `/workspace/shape/.venv/bin/python scripts/gen_exit_codes.py --check` | 0 |
| static-10 | `/workspace/shape/.venv/bin/python scripts/gen_failure_modes.py --check` | 0 |
| static-11 | `/workspace/shape/.venv/bin/python scripts/check_shipped_data.py` | 0 |
| static-12 | `/workspace/shape/.venv/bin/python scripts/check_plugin_skeletons.py` | 0 |
| static-13 | `/workspace/shape/.venv/bin/python scripts/check_versions.py` | 0 |
| static-14 | `/workspace/shape/.venv/bin/python scripts/check_release_workflows.py` | 0 |
| static-15 | `/workspace/shape/.venv/bin/python scripts/check_conformance_coverage.py` | 0 |
| static-16 | `/workspace/shape/.venv/bin/python scripts/cli_surface.py --check` | 0 |
| static-17 | `/workspace/shape/.venv/bin/python scripts/check_v1_done.py` | 0 |
| static-prepared | `.venv/bin/python /tmp/W-cli-polish-1-merge/static.py` | 0 |
| whitespace-index | `git diff --cached --check` | 0 |
| whitespace-worktree | `git diff --check` | 0 |

### Final merge verification results

- Prepared lane suite: exit 0, 1,407 passed, 2 existing live tests deselected, 34 warnings.
- All eighteen static checks: exit 0 on both runs. Strict docs build, docs language, user-facing wording and source/site links: exit 0 on both runs.
- Complete prepared docs/release suite: exit 1, 559 passed, 1 failed, 2 warnings. Its only failure was `tests/docs/test_reference_examples.py::test_reference_page[CONTRIBUTING]`: pip reported `BadZipFile` reading the editable wheel while parallel bootstrap builds shared CARGO_TARGET_DIR.
- Isolated serial retry of that exact failed node: exit 0, 1 passed in 258.69 seconds. For this retry CARGO_TARGET_DIR was a separate task-local directory (`/tmp/W-cli-polish-1-merge/cargo-target-bootstrap`), avoiding concurrent wheel-output writes. No source, test, transcript, gate, or assertion was changed. Every docs/release test therefore has a passing execution; the full parallel command's exit 1 remains recorded, not presented as a clean full-suite pass.
- Final merge proof, whitespace checks and existing lane DCO check: exit 0. Both complete pre-merge Unreleased blocks are present verbatim, no conflicts remain, and no differences from main occur outside the existing lane scope. CONTRIBUTING.md and all core build/Rust inputs equal main.

Ready to create the signed-off merge commit with the W-cli-polish-1 prefix and push only refs/heads/lane/W-cli-polish-1. The merge preserves main's files outside lane scope. No rebase, force-push, new PR or tag was used. This round used the existing fast-check alternative for the lane; full make/coverage/heavy/kernel-only/Rust check stages were not rerun.

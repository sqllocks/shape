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

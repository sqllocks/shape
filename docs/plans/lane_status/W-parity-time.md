# W-parity-time lane status

## Change and findings

The profile verifier excludes exactly `dow_histogram` and `temporal_histogram`
for exactly `edge/x_time_only.csv` and `edge/x_time_ampm.csv`. Its comment cites
profile_1to1/README.md's documented time-only date behavior. The comparison matrix
omits those excluded cells; hours and every other field retain their original
rules and tolerances. No product code, recordings, golden inputs or plan files
changed.

Regression tests recreate the authenticated fixture bytes and run the real
profiler with its parser clock pinned to Thursday 2027-11-04. Both weekday and
month/year histograms differ from the recording. Both fixtures then produce zero
mismatches, while changing an hour bucket by 2e-9 produces exactly one mismatch.
Additional controls retain date comparisons for other paths and independently
perturb every remaining field on both time-only fixtures.

The AM/PM recording contains a mix of year-1 values and values dated on the
recording day, so the test checks a previously empty weekday rather than assuming
all values have the same date. The recording is read through the authenticated
store and input digests are checked by the verifier normally.

## Setup and command results

All changes and commands after the initial origin check used this independent
clone. Source imports for core and all five plugins were explicitly verified to
resolve inside this clone. Python 3.11 is absent, so supported Python 3.12.14 was
used. Rust uses the existing shared toolchain at /workspace/.onboarding.
Noneditable dependencies were reused by local symlinks, excluding original core
and plugin editable metadata. Console-script Python paths were rewritten to the
local venv; Ruff's standalone binary was reused.

| Command | Exit | Result |
| --- | --- | --- |
| `cd /workspace/shape && git remote get-url origin && git config user.name && git config user.email` | 0 | Origin is the required public repository. |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-parity-time`; set origin to original GitHub URL and copy git identity | 0 | Independent lane checkout. |
| `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`; `git checkout -B lane/W-parity-time origin/release/0.9.3` | 0 | Correct base and lane. |
| `rg --files -g AGENTS.md -g CLAUDE.md -g CONTRIBUTING.md`; read CLAUDE.md, CONTRIBUTING.md, harness source/README, existing tests, Makefile and pyproject.toml; `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md` | 0 | No AGENTS.md; Decisions reviewed. Follow-up supplied by task. Plan stays uncommitted. |
| Runtime/file discovery (`python --version`, `ls`, `rg`, `readlink`, `du`, `ps`, `df`) | 0; expected missing-path probes 2 | Python 3.12 and existing dependencies/toolchain found. Initial nonexistent dtparse paths corrected via `rg --files`. |
| `python3.11 -m venv .venv && . .venv/bin/activate && make bootstrap` | 127 | Python 3.11 command unavailable. |
| `python -m venv .venv && . .venv/bin/activate && make bootstrap` | 130 | Interrupted to avoid downloading a duplicate 450 MB Spark dependency; reused installed noneditable dependencies. |
| Dependency-symlink and local-console-script preparation Python commands | 0 | Source isolation retained. |
| Initial test run without source path, before editable installation | 1 | 4 ModuleNotFoundError failures; setup evidence only. |
| Corrected regression-test preparation, before verifier change | 0 | Removed an invalid assumption that every AM/PM date is the recording date. |
| Red focused tests with `PYTHONPATH=$PWD/src SHAPE_KERNEL=python .venv/bin/python -m pytest -q tests/benchmarks/test_profile_verify_recorded.py` | 1 | 4 intended failing cases, 17 passed; full output below. |
| Verifier implementation and status-file creation Python commands | 0 | Narrow fixture/field exception. |
| Same focused tests after implementation | 0 | 21 passed before adding two unchanged-field controls. |
| First `python -m ruff` attempt | 1 | Reused package needed its standalone binary in the local venv. |
| `ruff format` on changed Python files | 0 | Formatting applied. |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | All checks passed, including final repeat after test edits. |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2150 files formatted. |
| `.venv/bin/python -m mypy` | 0 | No issues in 701 source files. |
| `PYTHONPATH=$PWD/src SHAPE_KERNEL=python .venv/bin/python -m pytest -q tests/benchmarks benchmarks/vs_refengine/tests tests/profile/test_dtparse.py` | 1 | 540 passed; 2 failures because local domains plugin install was still pending; 2 existing native-kernel prerequisite skips while bootstrap built. |
| `pip install --no-deps -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | Local plugin installs. |
| `pip install --no-deps -e plugins/shape-domains` | 0 | Resolved the two setup-only neighbour failures. |
| Rerun both failed `test_record_baseline` tests, `tests/benchmarks/test_profile_verify_recorded.py`, `tests/profile/test_dtparse.py` with the same source/clock environment | 0 | 34 passed, including all 23 tests in the changed harness test file. |
| Import-location check for core and all five plugins | 0 | Every source path resolves inside this clone. |

## Final validation additions

- `PIP_NO_DEPS=1 make bootstrap` with the local venv and shared Rust environment:
  exit 0. Built the native kernel and installed this clone's core/domains package.
- `pip install --no-deps -e '.[docs]' -e plugins/shape-domains
  -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric
  -e plugins/shape-kafka`: exit 0. Installed requested local editable packages
  while retaining the reused noneditable docs/development dependencies.
- Native rerun, with no pure-Python override:
  `.venv/bin/python -m pytest -q tests/benchmarks/test_profile_verify_recorded.py
  tests/benchmarks/test_record_baseline.py::test_composite_recorder_uses_only_per_table_planned_counts
  tests/benchmarks/test_record_baseline.py::test_composite_plans_are_the_releases_own_not_an_imported_dump
  tests/benchmarks/test_bench_scripts.py tests/profile/test_dtparse.py`:
  exit 0, **49 passed**, no skips. This covers the initial broad-run prerequisite skips.
- `rustc --version`: exit 0, Rust 1.99.0. An exploratory import of a nonexistent
  kernel `backend` helper exited 1; corrected `importlib.util.find_spec` inspection
  exited 0 and confirmed the native module inside this clone's source directory.
- `git diff --check` and final source/status inspection: exit 0.
- Final `python scripts/check_secrets.py` after adding this status file: exit 0,
  Basic secret check OK.

## Required full check attempt

`timeout 600 make check` was run exactly once, with the local venv/toolchain,
`PYTHONPATH=$PWD/src` and `SHAPE_KERNEL=python` (the native build was still running
when the attempt began). **Exit 124:** the 600-second bound terminated the full
core pytest stage at 6% progress. No test failure was printed before termination.
This is an incomplete full-suite check, not a passing full-suite claim; coverage,
later heavy/kernel and Rust stages were not reached. All preflight stages shown
below completed successfully. No gates or tolerances were changed.

The changed harness and its neighbours were separately validated, including a
native run after setup completed. The lead's full GitHub CI run is still required
for complete suite and coverage evidence.

```text
ruff check src tests plugins benchmarks/vs_refengine
All checks passed!
ruff format --check src tests plugins benchmarks/vs_refengine
2089 files already formatted
mypy
Success: no issues found in 701 source files
python -m compileall -q src/shape
vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
lint-imports

╔══╗─────────▶╔╗ ╔╗      ╔╗◀───┐
╚╣╠╝◀─────┐  ╔╝╚╗║║────▶╔╝╚╗   │
 ║║   ╔══╦══╦╩╗╔╝║║  ╔╦═╩╗╔╝╔═╦══╗
 ║║╔══╣╔╗║╔╗║╔╣║ ║║ ╔╬╣╔╗║║ ║│║╔═╝
╔╣╠╣║║║╚╝║╚╝║║║╚╗║╚═╝║║║║║╚╗║═╣║
╚══╩╩╩╣╔═╩══╩╝╚═╝╚═══╩╩╝╚╩═╩╩═╩╝
  └──▶║║                    ▲ 
      ╚╝────────────────────┘


---------
Contracts
---------

Analyzed 641 files, 2220 dependencies.
--------------------------------------

Built-ins load only through the plugin registry (plan 4.3) KEPT (1 ignored import)

Contracts: 1 kept, 0 broken.
python scripts/check_requirements.py
Requirement registry OK: 89 requirements.
python scripts/check_secrets.py
Basic secret check OK.
python scripts/check_user_facing.py
check_user_facing: clean
python scripts/gen_exit_codes.py --check
python scripts/gen_failure_modes.py --check
python scripts/check_shipped_data.py
shipped data OK: 167 data files, no run-time download
python scripts/check_plugin_skeletons.py
plugin skeletons OK (12 distributions, version 0.9.3)
python scripts/check_versions.py
versions OK: 58 locations agree on 0.9.3
python scripts/check_release_workflows.py
release workflows OK: publish.yml, release.yml, wheels.yml keep T-25 and T-04
python scripts/check_conformance_coverage.py
32 normative statements, 32 conformance tests
python scripts/cli_surface.py --check
CLI surface: 0 breaking change(s), 0 note(s)
python scripts/check_v1_done.py
v1 definition of done: 29 item(s), 0 problem(s)
pytest -q -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86
........................................................................ [  0%]
........................................................................ [  0%]
........................................................................ [  1%]
........................................................................ [  1%]
........................................................................ [  1%]
........................................................................ [  2%]
........................................................................ [  2%]
........................................................................ [  3%]
........................................................................ [  3%]
........................................................................ [  3%]
........................................................................ [  4%]
........................................................................ [  4%]
........................................................................ [  5%]
........................................................................ [  5%]
........................................................................ [  5%]
........................................................................ [  6%]
..............................................................make: *** [Makefile:25: check] Terminated
```

## Scope and publication

`git ls-remote --heads origin lane/W-parity-time` exited 0 with no existing remote
lane. Only the harness verifier, its unit tests and this status file are staged
for the final signed commit. No PR, merge, tag or release-branch write is requested.

Open questions: none.

## Red evidence (before implementation)

`PYTHONPATH=$PWD/src SHAPE_KERNEL=python .venv/bin/python -m pytest -q tests/benchmarks/test_profile_verify_recorded.py` exited 1. The real time-only fixtures, with the parser clock pinned to Thursday 2027-11-04 instead of the recording weekday, produce two date-derived mismatches each; hour perturbations produce three instead of the required one.

```text
...........FFFF......                                                    [100%]
=================================== FAILURES ===================================
__ test_time_only_profiles_on_a_different_weekday[False-edge/x_time_only.csv] __

dataset = 'edge/x_time_only.csv', change_hour = False
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fdf6c21fdd0>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-8/test_time_only_profiles_on_a_d0')
capsys = <_pytest.capture.CaptureFixture object at 0x7fdf6c21dc10>

    @pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
    @pytest.mark.parametrize("change_hour", [False, True])
    def test_time_only_profiles_on_a_different_weekday(dataset, change_hour, monkeypatch, tmp_path, capsys):
        import datetime as dt
    
        from shape.profile.reference import dtparse
    
        v = verifier()
        baseline = v.sd.restore_order(v.load("profile", dataset)["profile"])
        # Recordings date time-only values to Tuesday in October 2026 (AM/PM
        # also contains year-1 values). Pin a Thursday in November of another year.
        today = dt.datetime(2027, 11, 4)
        assert baseline["columns"]["t"]["dow_histogram"][today.weekday()] == 0.0
        monkeypatch.setattr(dtparse, "_midnight", lambda supplied_today: today)
        assert dtparse.parse_mixed("09:00:00").date() == today.date()
    
        times = (
            [f"{h:02d}:{m:02d}:00" for h in (9, 10, 13) for m in (0, 30)]
            if dataset == "edge/x_time_only.csv"
            else ["3pm", "10am", "11:45 PM", "7:05 am"]
        )
        path = tmp_path / dataset
        path.parent.mkdir()
        path.write_text("id,t\n" + "".join(f"{i},{times[i % len(times)]}\n" for i in range(60)))
        monkeypatch.setattr(v, "DATA", tmp_path)
        candidate = v.shape_profile(dataset)
        assert candidate["columns"]["t"]["dow_histogram"] != baseline["columns"]["t"]["dow_histogram"]
        assert candidate["columns"]["t"]["temporal_histogram"] != baseline["columns"]["t"]["temporal_histogram"]
        if change_hour:
            candidate["columns"]["t"]["hour_histogram"][0] += 2e-9
        monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
>       assert v.main([dataset]) == int(change_hour)
E       AssertionError: assert 1 == 0
E        +  where 1 = <function main at 0x7fdf54087740>(['edge/x_time_only.csv'])
E        +    where <function main at 0x7fdf54087740> = <module 'frz_profile_verify' from '/workspace/prompt-runs/checkouts/W-parity-time/benchmarks/vs_refengine/profile_1to1/verify.py'>.main
E        +  and   0 = int(False)

tests/benchmarks/test_profile_verify_recorded.py:164: AssertionError
----------------------------- Captured stdout call -----------------------------
edge/x_time_only.csv: 2 mismatches
  table.row_count: 1/1
  table.primary_key: 1/1
  table.detected_fks: 1/1
  table.correlation_matrix: 1/1
  dtype: 2/2
  null_count: 2/2
  null_rate: 2/2
  cardinality: 2/2
  cardinality_ratio: 2/2
  is_unique: 2/2
  is_enum: 2/2
  enum_values: 2/2
  min_value: 2/2
  max_value: 2/2
  mean: 2/2
  std: 2/2
  distribution: 2/2
  distribution_params: 2/2
  pattern: 2/2
  is_primary_key: 2/2
  is_foreign_key: 2/2
  fk_ref_table: 2/2
  quantiles: 2/2
  hour_histogram: 2/2
  dow_histogram: 1/2
  temporal_histogram: 1/2
  string_length: 2/2
  outlier_rate: 2/2
  value_counts_ext: 2/2
  value_counts_ext_order: 2/2
  fit_score: 2/2
MISMATCH edge/x_time_only.csv.t.dow_histogram [list_abs]: baseline=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0] candidate=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
MISMATCH edge/x_time_only.csv.t.temporal_histogram [nested_abs]: baseline={"hi_year": 2026, "lo_year": 2026, "month_weights": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0], "year_weights": [1.0]} candidate={"lo_year": 2027, "hi_year": 2027, "year_weights": [1.0], "month_weights": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]}
2 mismatches
__ test_time_only_profiles_on_a_different_weekday[False-edge/x_time_ampm.csv] __

dataset = 'edge/x_time_ampm.csv', change_hour = False
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fdf54333410>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-8/test_time_only_profiles_on_a_d1')
capsys = <_pytest.capture.CaptureFixture object at 0x7fdf5595d5e0>

    @pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
    @pytest.mark.parametrize("change_hour", [False, True])
    def test_time_only_profiles_on_a_different_weekday(dataset, change_hour, monkeypatch, tmp_path, capsys):
        import datetime as dt
    
        from shape.profile.reference import dtparse
    
        v = verifier()
        baseline = v.sd.restore_order(v.load("profile", dataset)["profile"])
        # Recordings date time-only values to Tuesday in October 2026 (AM/PM
        # also contains year-1 values). Pin a Thursday in November of another year.
        today = dt.datetime(2027, 11, 4)
        assert baseline["columns"]["t"]["dow_histogram"][today.weekday()] == 0.0
        monkeypatch.setattr(dtparse, "_midnight", lambda supplied_today: today)
        assert dtparse.parse_mixed("09:00:00").date() == today.date()
    
        times = (
            [f"{h:02d}:{m:02d}:00" for h in (9, 10, 13) for m in (0, 30)]
            if dataset == "edge/x_time_only.csv"
            else ["3pm", "10am", "11:45 PM", "7:05 am"]
        )
        path = tmp_path / dataset
        path.parent.mkdir()
        path.write_text("id,t\n" + "".join(f"{i},{times[i % len(times)]}\n" for i in range(60)))
        monkeypatch.setattr(v, "DATA", tmp_path)
        candidate = v.shape_profile(dataset)
        assert candidate["columns"]["t"]["dow_histogram"] != baseline["columns"]["t"]["dow_histogram"]
        assert candidate["columns"]["t"]["temporal_histogram"] != baseline["columns"]["t"]["temporal_histogram"]
        if change_hour:
            candidate["columns"]["t"]["hour_histogram"][0] += 2e-9
        monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
>       assert v.main([dataset]) == int(change_hour)
E       AssertionError: assert 1 == 0
E        +  where 1 = <function main at 0x7fdf4c6ffa60>(['edge/x_time_ampm.csv'])
E        +    where <function main at 0x7fdf4c6ffa60> = <module 'frz_profile_verify' from '/workspace/prompt-runs/checkouts/W-parity-time/benchmarks/vs_refengine/profile_1to1/verify.py'>.main
E        +  and   0 = int(False)

tests/benchmarks/test_profile_verify_recorded.py:164: AssertionError
----------------------------- Captured stdout call -----------------------------
edge/x_time_ampm.csv: 2 mismatches
  table.row_count: 1/1
  table.primary_key: 1/1
  table.detected_fks: 1/1
  table.correlation_matrix: 1/1
  dtype: 2/2
  null_count: 2/2
  null_rate: 2/2
  cardinality: 2/2
  cardinality_ratio: 2/2
  is_unique: 2/2
  is_enum: 2/2
  enum_values: 2/2
  min_value: 2/2
  max_value: 2/2
  mean: 2/2
  std: 2/2
  distribution: 2/2
  distribution_params: 2/2
  pattern: 2/2
  is_primary_key: 2/2
  is_foreign_key: 2/2
  fk_ref_table: 2/2
  quantiles: 2/2
  hour_histogram: 2/2
  dow_histogram: 1/2
  temporal_histogram: 1/2
  string_length: 2/2
  outlier_rate: 2/2
  value_counts_ext: 2/2
  value_counts_ext_order: 2/2
  fit_score: 2/2
MISMATCH edge/x_time_ampm.csv.t.dow_histogram [list_abs]: baseline=[0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0] candidate=[0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0]
MISMATCH edge/x_time_ampm.csv.t.temporal_histogram [nested_abs]: baseline={"hi_year": 2026, "lo_year": 1, "month_weights": [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0], "year_weights": [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0. candidate={"lo_year": 1, "hi_year": 2027, "year_weights": [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0
2 mismatches
__ test_time_only_profiles_on_a_different_weekday[True-edge/x_time_only.csv] ___

dataset = 'edge/x_time_only.csv', change_hour = True
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fdf54102cf0>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-8/test_time_only_profiles_on_a_d2')
capsys = <_pytest.capture.CaptureFixture object at 0x7fdf541032f0>

    @pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
    @pytest.mark.parametrize("change_hour", [False, True])
    def test_time_only_profiles_on_a_different_weekday(dataset, change_hour, monkeypatch, tmp_path, capsys):
        import datetime as dt
    
        from shape.profile.reference import dtparse
    
        v = verifier()
        baseline = v.sd.restore_order(v.load("profile", dataset)["profile"])
        # Recordings date time-only values to Tuesday in October 2026 (AM/PM
        # also contains year-1 values). Pin a Thursday in November of another year.
        today = dt.datetime(2027, 11, 4)
        assert baseline["columns"]["t"]["dow_histogram"][today.weekday()] == 0.0
        monkeypatch.setattr(dtparse, "_midnight", lambda supplied_today: today)
        assert dtparse.parse_mixed("09:00:00").date() == today.date()
    
        times = (
            [f"{h:02d}:{m:02d}:00" for h in (9, 10, 13) for m in (0, 30)]
            if dataset == "edge/x_time_only.csv"
            else ["3pm", "10am", "11:45 PM", "7:05 am"]
        )
        path = tmp_path / dataset
        path.parent.mkdir()
        path.write_text("id,t\n" + "".join(f"{i},{times[i % len(times)]}\n" for i in range(60)))
        monkeypatch.setattr(v, "DATA", tmp_path)
        candidate = v.shape_profile(dataset)
        assert candidate["columns"]["t"]["dow_histogram"] != baseline["columns"]["t"]["dow_histogram"]
        assert candidate["columns"]["t"]["temporal_histogram"] != baseline["columns"]["t"]["temporal_histogram"]
        if change_hour:
            candidate["columns"]["t"]["hour_histogram"][0] += 2e-9
        monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
        assert v.main([dataset]) == int(change_hour)
        output = capsys.readouterr().out
        if change_hour:
>           assert "1 mismatches" in output
E           assert '1 mismatches' in 'edge/x_time_only.csv: 3 mismatches\n  table.row_count: 1/1\n  table.primary_key: 1/1\n  table.detected_fks: 1/1\n  ta... "year_weights": [1.0], "month_weights": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]}\n3 mismatches\n'

tests/benchmarks/test_profile_verify_recorded.py:167: AssertionError
__ test_time_only_profiles_on_a_different_weekday[True-edge/x_time_ampm.csv] ___

dataset = 'edge/x_time_ampm.csv', change_hour = True
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fdf4c74e2a0>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-8/test_time_only_profiles_on_a_d3')
capsys = <_pytest.capture.CaptureFixture object at 0x7fdf4c74e000>

    @pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
    @pytest.mark.parametrize("change_hour", [False, True])
    def test_time_only_profiles_on_a_different_weekday(dataset, change_hour, monkeypatch, tmp_path, capsys):
        import datetime as dt
    
        from shape.profile.reference import dtparse
    
        v = verifier()
        baseline = v.sd.restore_order(v.load("profile", dataset)["profile"])
        # Recordings date time-only values to Tuesday in October 2026 (AM/PM
        # also contains year-1 values). Pin a Thursday in November of another year.
        today = dt.datetime(2027, 11, 4)
        assert baseline["columns"]["t"]["dow_histogram"][today.weekday()] == 0.0
        monkeypatch.setattr(dtparse, "_midnight", lambda supplied_today: today)
        assert dtparse.parse_mixed("09:00:00").date() == today.date()
    
        times = (
            [f"{h:02d}:{m:02d}:00" for h in (9, 10, 13) for m in (0, 30)]
            if dataset == "edge/x_time_only.csv"
            else ["3pm", "10am", "11:45 PM", "7:05 am"]
        )
        path = tmp_path / dataset
        path.parent.mkdir()
        path.write_text("id,t\n" + "".join(f"{i},{times[i % len(times)]}\n" for i in range(60)))
        monkeypatch.setattr(v, "DATA", tmp_path)
        candidate = v.shape_profile(dataset)
        assert candidate["columns"]["t"]["dow_histogram"] != baseline["columns"]["t"]["dow_histogram"]
        assert candidate["columns"]["t"]["temporal_histogram"] != baseline["columns"]["t"]["temporal_histogram"]
        if change_hour:
            candidate["columns"]["t"]["hour_histogram"][0] += 2e-9
        monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
        assert v.main([dataset]) == int(change_hour)
        output = capsys.readouterr().out
        if change_hour:
>           assert "1 mismatches" in output
E           AssertionError: assert '1 mismatches' in 'edge/x_time_ampm.csv: 3 mismatches\n  table.row_count: 1/1\n  table.primary_key: 1/1\n  table.detected_fks: 1/1\n  ta... 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0\n3 mismatches\n'

tests/benchmarks/test_profile_verify_recorded.py:167: AssertionError
=========================== short test summary info ============================
FAILED tests/benchmarks/test_profile_verify_recorded.py::test_time_only_profiles_on_a_different_weekday[False-edge/x_time_only.csv]
FAILED tests/benchmarks/test_profile_verify_recorded.py::test_time_only_profiles_on_a_different_weekday[False-edge/x_time_ampm.csv]
FAILED tests/benchmarks/test_profile_verify_recorded.py::test_time_only_profiles_on_a_different_weekday[True-edge/x_time_only.csv]
FAILED tests/benchmarks/test_profile_verify_recorded.py::test_time_only_profiles_on_a_different_weekday[True-edge/x_time_ampm.csv]
4 failed, 17 passed in 3.48s
```

# W-gameday-2 lane status

Status: complete; focused and neighbouring suites pass, full check bounded below.

Base: `origin/lane/W-gameday-1`; no plan file merged. Read CLAUDE.md,
CONTRIBUTING.md, the promotion plan Decisions and 4.2, and docs/GAMEDAY.md.
No AGENTS.md exists in this checkout. Origin is sqllocks/shape.

## Change

Tests only. Two real game-day runs cover successful and mixed detected/missed
Markdown reports: recorded plan/data/seed, verdict, round order, injected rows,
expectations and command exit codes. Five direct run-check cases verify the
exact parser-specific JSON flags for diff/check/verify, exclusion of
profile/fidelity, argument preservation, and returned stdout/stderr/exit/time.
No formatting contract or fixed timings are promised; production is unchanged.

## Red evidence

The first focused run exposed an error in the new test's import: the CLI package
exports a function named main, so importing it as a module gave AttributeError
(5 failed, 76 passed; pytest exit 1). Corrected the test to import the module
using importlib. This was a test-authoring issue, not a product defect.

Because this lane tests existing behavior, no product fix was necessary.
Proved the tests detect missing behavior by temporarily replacing `_json_flag`
with an empty list and `Report.markdown` with a heading-only report in the test
process, without changing source files. Command:

```python
import pytest
from shape.scenario import gameday
gameday._json_flag = lambda command: []
gameday.Report.markdown = lambda self: '# Game day report\n'
raise SystemExit(pytest.main(['-q', 'tests/scenario/test_gameday.py', '-k',
                             'markdown_report_shows or run_check_adds']))
```

Result (exit 1):

```text
5 failed, 2 passed, 74 deselected in 2.27s
```

Failed node IDs:

- `test_markdown_report_shows_the_recorded_run_and_each_round[detected]`
- `test_markdown_report_shows_the_recorded_run_and_each_round[mixed]`
- `test_run_check_adds_json_for_detection_commands_only[diff-flags0]`
- `test_run_check_adds_json_for_detection_commands_only[check-flags1]`
- `test_run_check_adds_json_for_detection_commands_only[verify-flags2]`

All are in `tests/scenario/test_gameday.py`. Both command exclusion cases passed. Restoring normal imports required no source edit.
Focused normal run: 81 passed in 23.15s (exit 0).

## Setup findings

Recovered the existing checkout and venv instead of recreating the branch.
The previous bootstrap log reports success (exit 0); required docs/plugin
installation had been interrupted. The existing venv runs Python 3.12, which
satisfies CONTRIBUTING.md's Python 3.11-or-newer requirement.
An attempted private Rust cache copy under /tmp failed with ENOSPC (exit 1).
The /tmp mount was full while /workspace had free space. Removed only that
lane-owned partial copy and stopped only this lane's installation. Resumed
with a complete copy of the original target under
/workspace/prompt-runs/W-gameday-2-target and a private TMPDIR under /workspace.
No shared dependency or Rust target mutation.
The recovered venv contained packages but lacked console entry points: bare
pytest exited 127, and python -m ruff exited 1 (missing binary). Restored local
console scripts from installed package entry-point metadata and copied the
original ruff executable into this venv. Neither affected tracked source.
An attempted neighbours command with nonexistent tests/cli/test_gameday*
exited 4; corrected to tests/scenario. A later focused command also named a
nonexistent tests/scenario/test_results.py (exit 4); corrected to the existing
tests/scenario/test_gameday.py. No tests were skipped or marked xfail.

## Checks

- `python -m pytest -q tests/scenario/test_gameday.py`: exit 0, 81 passed.
- `ruff check src tests plugins benchmarks/vs_refengine scripts`: exit 0.
- Initial `ruff format --check src tests plugins benchmarks/vs_refengine scripts`:
  exit 1, one new assertion needed formatting. `ruff format tests/scenario/test_gameday.py`:
  exit 0. Repeated format check: exit 0, 2139 files already formatted.
- `python -m mypy`: exit 0, 698 source files clean.
- `git diff --check`: exit 0.

## Questions

None. The tests confirm documented behavior and require no product change.

Setup command resumed successfully (exit 0):

```bash
export CARGO_TARGET_DIR=/workspace/prompt-runs/W-gameday-2-target
export TMPDIR=/workspace/prompt-runs/W-gameday-2-tmp
. .venv/bin/activate
pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka
```

Additional verification: `python scripts/check_secrets.py` exit 0; final Ruff
lint across the same required paths exit 0. Both git fetch commands in the
prompt completed with exit 0; `git merge-base --is-ancestor
origin/lane/W-gameday-1 HEAD` exit 0.

- `python -m pytest -q tests/scenario`: exit 0, 670 passed, 7 warnings in
  270.66s. This covers all scenario neighbours, including canary, failure
  catalog, detection, and reproducibility.
- After required plugin installation and test formatting,
  `python -m pytest -q tests/scenario/test_gameday.py`: exit 0, 81 passed in 32.88s.

The full check uses `timeout --signal=TERM --kill-after=10s 180s make check`
with this lane's TMPDIR, CARGO_TARGET_DIR and activated venv. No full-suite
pass is claimed unless the command finishes successfully.

Full-check result: **exit 124**, terminated after 180 seconds while pytest
was still collecting/importing and rewriting assertions for the full core
suite. A pre-timeout stack sample confirmed assertion rewriting in the
collection path; this is not evidence of a product test failure or a
coverage deadlock. Ruff lint/format, mypy, compileall, Vulture, import contracts,
and every repository script before the pytest stage completed successfully
(exit 0). The coverage and later heavy/Python-kernel/Rust stages did not
complete, so the full check is incomplete and requires CI validation.
No gate was changed and no test was skipped to obtain the targeted passes.

Final `git diff --check` and focused Ruff format check: exit 0.
Diagnostics (`ps`, `df`, log reads, py-spy stack samples) were read-only;
py-spy samples completed with exit 0. The initial status command from
/workspace reported no Git repository (git exit 128); all lane operations
subsequently used the existing lane checkout.

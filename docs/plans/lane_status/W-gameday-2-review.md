# W-gameday-2 review

Reviewer: independent review session (did not build this lane).
Head reviewed: `19a9c8f2802558017c5aa6076c4f043e36c34f35` (expected prefix `19a9c8f`: matches).
Base: `origin/lane/W-gameday-1` (`10e5f80`). Only what is new on top of that lane was reviewed.
Plan: `origin/plan/promotion-093`, section 4.2.

## Verdict: APPROVE

Tests only: `tests/scenario/test_gameday.py` gains a Markdown-report test (one all-detected run
and one mixed run, compared to the JSON report of the same run) and a `run_check` test. The
`run_check` test checks that `--json -` is added for `diff`/`check`, `--json` for `verify`, nothing
for `profile`/`fidelity`, and that the caller's argv is not mutated. That is exactly the
W-gameday-2 task. The checks match `docs/GAMEDAY.md:68-69` ("Shape adds `--json` to your `diff`,
`check` and `verify`") and `:85-89` ("The Markdown report shows the same"). Production is
unchanged: no fix was needed. There is no promotion, STABLE entry, snapshot write or CHANGELOG
change, and no skip, xfail or lowered gate.

## Commands (Python 3.11 venv, `make bootstrap` + docs/plugin installs: exit 0)

| Command | Exit | Result |
|---|---:|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | 698 source files, no issues |
| `pytest -p no:cov -q tests/scenario/test_gameday.py` | 0 | 81 passed |

## Mutation checks on `src/shape/scenario/gameday.py` (local only, reverted)

| Mutation | New tests |
|---|---|
| `_json_flag` always returns `["--json"]` | 3 failed |
| `run_check` adds `--json` to every command | 2 failed |
| Markdown drops the injected lines | 2 failed |
| Markdown writes every expectation as `detected` | 1 failed (mixed) |
| Markdown rounds in reverse order | 1 failed (mixed) |
| Markdown seed hard-coded to 42 (run uses `--seed 7`) | 2 failed |
| Markdown keeps only the first check row | 2 failed |
| Markdown keeps only the first expectation row | **0 failed** |
| Markdown keeps only the first injection row | **0 failed** |

## Findings

Blocking: none.

Non-blocking:

1. `tests/scenario/test_gameday.py:119-152`: every round in the test has one expectation
   (`a_round`, line 61), and `null-flood` on one table plants one injection. So dropping all but
   the first expectation or injection row goes unnoticed (table above). A round with two `expect`
   entries, or a two-table scenario, would close the gap.
2. Plan 4.2 says to document the Markdown report as "not a contract". The new test asserts exact
   line formats (`Result: **...**`, the `| \`expect\` | detected |` rows,
   `- planted ... (N rows)`), which in practice makes that layout a test contract. The docs say the
   Markdown "shows the same" as the JSON, so the content checks are justified. The exact wording
   checks will need updating whenever the layout changes. Keep that in mind for W-gameday-3/4,
   or loosen the checks to content (names, counts, verdicts) if the lead wants the layout free.
3. `test_run_check_adds_json_for_detection_commands_only` replaces `shape.cli.main.main`, so it
   tests `run_check` directly and not through `gameday run`. The end-to-end path is covered
   indirectly, because detections in the existing run tests depend on the `--json` results.

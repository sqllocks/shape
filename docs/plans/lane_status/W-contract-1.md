# W-contract-1 (0.9.4)

Origin verified: https://github.com/sqllocks/shape.git.
Base: origin/main a8e476c. Branch: lane/W-contract-1.
AGENTS.md is absent in this checkout and its parent directories. Read CLAUDE.md,
CONTRIBUTING.md, the requested contract/CLI/reference/exit-code documentation,
the v1 loader, registry and evaluator, CLI dispatch and dry-run planner, and existing tests.
W-uv-1 is not on main; use the existing virtual environment and Makefile workflow.

Compatibility review: STATE_AND_COMPATIBILITY.md section 5 explicitly preserves
hand-authored contracts' typo protection. CONTRACTS.md and CLI.md already promise
unknown rules are errors. Top-level x_ extensions remain accepted. No blocking question.
Column min/max values remain unrestricted, as documented in the existing validator
and its regression tests; incompatible bounds continue to produce violations.

## Tests first: red on origin/main

Before implementation changes:
`.venv/bin/pytest -q tests/contracts/test_w_contract_1.py`
Exit **1**: **32 failed, 7 passed**. The failures reproduce v1 validate rejection,
dry-run bypass of rule validation, missing scalar type checks and spelling hints,
structural error dispatch, and the docs registry mismatch (`valid_as`, `strength`).
The seven passing cases confirm existing real-check refusals and valid dry-run behavior.
An initial run also exposed an incorrect test assertion about existing dry-run text;
that assertion was corrected before this recorded baseline. No implementation was changed.

## Implementation

- Recognise v1 contracts (empty, single-table, stamped, dataset and x_ extensions)
  while preserving generation-schema and contract-model dispatch.
- Share the existing v1 validator with validate and profile-check dry runs, including
  contracts resolved from shape.yml. Preserve the captured-evidence contract vocabulary.
- Distinguish structural contract errors (validate exit 1) from malformed rule inputs
  (exit 2). ContractRuleError remains a ContractError for existing callers.
- Validate label types, preserve null pattern/distribution labels, handle malformed
  valid_as.kind without a TypeError, and suggest close rule spellings with the
  repository's difflib cutoff. Evaluation of valid rules is unchanged.
- Test both documented contract keys and column rules against the source registries.
  Add valid_as, strength, tables, timeseries and reconcile to the overview.
- Update the generated exit-code documentation, CLI guidance and required changelog.
  Only the invalid-contract golden exception name changes; valid-result goldens stay intact.

## Final regression baseline and green run

The expanded suite was also run against a detached checkout of the original base,
with its own source selected via PYTHONPATH (not the lane's editable installation):

```text
cd /tmp/shape-W-contract-1-baseline
PYTHONPATH=/tmp/shape-W-contract-1-baseline/src /workspace/shape/.venv/bin/pytest -q tests/contracts/test_w_contract_1.py
37 failed, 12 passed in 3.02s
exit 1
```

The suite's extra malformed table/data value cases were observed red before their
fixes as well. Two compatibility tests caught null labels and evidence-contract
vocabulary regressions during implementation; both were corrected without weakening
those tests. The project fixture initially omitted its required format marker; that
fixture was corrected before the final baseline replay.

On the lane:

```text
.venv/bin/pytest -q tests/contracts/test_w_contract_1.py
49 passed in 8.34s
exit 0
```

## Checks

| Check | Exit | Result |
|---|---|---|
| New tests on origin/main | 1 | 37 failed, 12 passed (initial red: 32 failed, 7 passed) |
| New tests on lane | 0 | 49 passed |
| Final contract/CLI rerun after reconnection; `-m "not emulator and not live"` | 0 | 1326 passed, 19 existing optional-dependency skips, 2 live cases deselected; 157.32s |
| All contract and CLI modules, bridge catalog and project CLI; `-m "not emulator and not live"` | 0 | 1397 passed; 19 existing optional-dependency skips, 2 live cases deselected |
| `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` | 0 | built successfully |
| `python scripts/check_doc_links.py --site site` | 0 | 0 broken links |
| `python scripts/check_user_facing.py --site site` | 0 | clean |
| Ruff lint / formatting on changed Python files | 0 / 0 | clean |
| `mypy` | 0 | 701 source files |
| `git diff --check` | 0 | clean |
| Full static section of `make check` (all commands before pytest) | 0 | all 18 commands exited 0 on final source |
| `make check` attempted during implementation | 2 | interrupted obsolete run; details below |
| All bridge tests plus new regression suite | 0 | 1251 passed |
| Regression, bridge catalog, time-series and reconciliation tests | 0 | 201 passed |

No skip or xfail was added, and no gate or threshold was changed.

The initial unfiltered contract/CLI run exited 1 (1315 passed, 3 failed, 19 existing
optional-dependency skips): two implementation failures were corrected (the invalid
exception-name golden and incomplete-generation-schema dispatch). The remaining
`tests/cli/test_doctor_checks.py::test_live_eventhubs_namespace_dns` fails TLS
connectivity to management.azure.com:443. The identical test on untouched origin/main
also exits 1 (1 failed in 5.25s), confirming the environment limitation.

The task permits fast checks. The full make attempt started before source was
frozen and held earlier modules in memory. It was interrupted after 643.24s:
4427 passed, 5 failed, 20 existing skips and 69 deselected; make exited **2**.
Four failures were the stored bridge dispatch and three malformed table-value cases
that now pass on final source. The fifth was
`tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left`:
the sandbox's umask is 0077 and creates a CSV with mode 0600 where the test requires another mode.
That exact test fails on both the final lane and untouched origin/main (exit **1**
each). It was not changed or bypassed. The full make run, coverage floor, heavy,
pure-Python kernel and Rust stages are **not claimed to have passed**.
Final verification uses the permitted fast path: every static command from the
Makefile and the affected test suites listed above.

A later combined final test run lost its executor connection at 37% without a
verdict when the managed environment restarted (runtime spec revision 2 to 3).
Workspace changes persisted. The final contract/CLI rerun completed successfully:
1326 passed, 19 existing optional-dependency skips, 2 live cases deselected in 157.32s,
exit **0**. Strict docs, link and wording checks also completed again after reconnection,
each with exit **0**. No interrupted run is counted as green.

## Changed files

- `src/shape/cli/validate.py`: v1 contract dispatch and validation.
- `src/shape/cli/machine.py`: contract validation while planning profile checks.
- `src/shape/cli/main.py`: annotate the reused profile-routing helper.
- `src/shape/cli/exitcodes.py`: clarify structural versus rule-input failures.
- `src/shape/contracts/v1.py`: rule-error category, spelling hints and value validation.
- `tests/contracts/test_w_contract_1.py`: 49 regression and registry/documentation checks.
- `tests/fixtures/contracts/golden/fixtures__contract_invalid.txt`: malformed-rule exception name.
- `docs/CONTRACTS.md`: complete registry overview and CLI validation behavior.
- `docs/CLI.md`: v1 contract validation and dry-run behavior.
- `docs/EXIT_CODES.md`: regenerated exit-code reference.
- `CHANGELOG.md`: requested Unreleased entry.
- `docs/plans/lane_status/W-contract-1.md`: this evidence record.

Open questions: none.

Publication target: `lane/W-contract-1`, with a signed-off `W-contract-1:` commit.
No PR, tag, force push or push to another branch is part of this lane.


## Main integration follow-up (2026-10-15)

Merged main after W-play-1 and W-anon-1. The lane started at
`92c44ebedbe68cce73bbca4bbd4a9da021c424b5`; the fetched main / merge parent is
`2d4e86e06f1f3ff90aae65aeefa54820a3f5bd3e` (PR #23).
The existing lane PR is #27. No new PR is created.

`git fetch origin && git merge --no-ff origin/main` exited **1** because
CHANGELOG.md was the only conflict. Resolved it by retaining main's Playground
and integrations entries verbatim, then the lane's contract fix under the single
`## Unreleased` heading. Every one of the other 13 main-changed files matches
origin/main exactly. No lane implementation or test changed during resolution.
The merge is being completed with a signed-off `W-contract-1:` commit, without
rebase or force-push, and will be pushed only to `lane/W-contract-1`.

Checks below use `.venv/bin` tools and the same permitted fast-check scope as the
original lane. `PATH="$PWD/.venv/bin:$PATH"` supplies the existing environment
where commands or tests start subprocesses. W-uv-1 is still absent from main.

| Command | Exit | Result |
|---|---:|---|
| `git remote get-url origin` | 0 | Required sqllocks/shape remote confirmed |
| `git fetch origin && git merge --no-ff origin/main` | 1 | Fetched main successfully; expected CHANGELOG.md conflict resolved |
| `git add CHANGELOG.md` | 0 | Resolved conflict staged |
| Main-preservation comparison of all main-changed files except CHANGELOG.md | 0 | All 13 files match main exactly |
| `git diff --cached origin/main --name-only` | 0 | Only the original 12 lane files differ from main |
| `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` | 0 | Built successfully in 14.94s |
| `python scripts/check_doc_links.py --site site` | 0 | 0 broken links |
| `python scripts/check_user_facing.py --site site` | 0 | Clean |
| `python scripts/check_docs_language.py --site site` | 0 | 0 wording hits |
| `git diff --check` and `git diff --cached --check` | 0 / 0 | Clean |

Final lane-suite and docs/release results are listed below.

Static fast checks on the merged tree (commands from `make -n check`, up to pytest):

| Command | Exit |
|---|---:|
| `ruff check src tests plugins benchmarks/vs_refengine` | 0 |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 |
| `mypy` | 0 |
| `python -m compileall -q src/shape` | 0 |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80` | 0 |
| `lint-imports` | 0 |
| `python scripts/check_requirements.py` | 0 |
| `python scripts/check_secrets.py` | 0 |
| `python scripts/check_user_facing.py` | 0 |
| `python scripts/gen_exit_codes.py --check` | 0 |
| `python scripts/gen_failure_modes.py --check` | 0 |
| `python scripts/check_shipped_data.py` | 0 |
| `python scripts/check_plugin_skeletons.py` | 0 |
| `python scripts/check_versions.py` | 0 |
| `python scripts/check_release_workflows.py` | 0 |
| `python scripts/check_conformance_coverage.py` | 0 |
| `python scripts/cli_surface.py --check` | 0 |
| `python scripts/check_v1_done.py` | 0 |


| Post-merge test command | Exit | Result |
|---|---:|---|
| `pytest -q -m 'not emulator and not live' tests/contracts tests/cli tests/bridge tests/project/test_cli.py tests/quality/test_timeseries.py tests/quality/test_reconcile.py` | 0 | 2700 passed, 19 existing optional-dependency skips, 2 live cases deselected in 228.97s |
| Untouched main: `pytest -q tests/docs/test_brand.py` | 1 | Missing Playwright; 1 failed in 18.10s |
| Untouched main: `pytest -q tests/release/test_packaging.py` | 1 | Cargo unavailable; 32 passed, 3 sdist-fixture errors in 11.57s |

Baseline comparisons use a detached worktree of the fetched main at
`/tmp/shape-W-contract-1-merge-main`, the lane's existing `.venv/bin` tools on PATH,
and `PYTHONPATH=/tmp/shape-W-contract-1-merge-main/src` to load main's core source.
The Playwright import and Cargo failures are environment prerequisites, not merge
conflicts. Source, tests and gates outside this lane remain main's versions.


| Additional baseline comparison | Exit | Result |
|---|---:|---|
| Untouched main: `pytest -q tests/release` | 1 | 391 passed, 3 errors in 44.96s; all three are the core sdist fixture requiring Cargo |


| Required additional command | Exit | Result |
|---|---:|---|
| `pytest -q tests/docs tests/release` | 1 | 540 passed, 17 failed, 3 errors in 525.09s; complete unfiltered run, no skips added |

The release errors are the same three Cargo-dependent sdist fixtures that fail on
untouched main. All 17 failing docs cases also reproduce on that same main
worktree (17 failed in 84.15s, exit 1). No test, gate, transcript, dependency or source outside
this lane is changed to make these checks pass.


| Final comparison / verification | Exit | Result |
|---|---:|---|
| Untouched main: `pytest -q` with all 17 failing docs node IDs below | 1 | The identical 17 tests failed in 84.15s |
| Failure-ID comparison between merged tree and untouched main | 0 | All 17 docs failures and all 3 release errors match |
| `git ls-remote --heads origin refs/heads/main refs/heads/lane/W-contract-1` | 0 | Main still 2d4e86e; remote lane still the original 92c44eb before publication |

Exact docs baseline selection (run in the main worktree with the PATH/PYTHONPATH
settings described above; exit **1**):

```sh
pytest -q \
  tests/docs/test_brand.py::test_branded_site \
  'tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]' \
  'tests/docs/test_reference_examples.py::test_reference_page[CLI]' \
  'tests/docs/test_reference_examples.py::test_reference_page[CONTAINER]' \
  'tests/docs/test_reference_examples.py::test_reference_page[CONTRIBUTING]' \
  'tests/docs/test_reference_examples.py::test_reference_page[DBT]' \
  'tests/docs/test_reference_examples.py::test_reference_page[IMPORTERS]' \
  'tests/docs/test_reference_examples.py::test_reference_page[INSTALL]' \
  'tests/docs/test_reference_examples.py::test_reference_page[PROJECT]' \
  'tests/docs/test_reference_examples.py::test_reference_page[SIGNING]' \
  'tests/docs/test_reference_examples.py::test_reference_page[SIMULATION_FILES_EVENTS]' \
  'tests/docs/test_reference_examples.py::test_reference_page[SINKS]' \
  'tests/docs/test_reference_examples.py::test_reference_page[TESTING_WITH_SHAPE]' \
  'tests/docs/test_reference_examples.py::test_reference_page[authoring]' \
  'tests/docs/test_reference_examples.py::test_reference_page[behavior]' \
  'tests/docs/test_reference_examples.py::test_reference_page[healthcare-codes]' \
  'tests/docs/test_reference_examples.py::test_reference_page[simulation]'
```

The combined docs/release check is not claimed as green. Pre-existing failures
include missing Playwright and optional plugin/dependency/service prerequisites,
recorded-transcript mismatches, and missing Cargo for sdist generation. They were
not hidden, skipped, xfailed or fixed outside the lane's scope. The complete
post-merge lane suite and all 18 static checks pass. No conflict markers remain.

Open questions: none. The merge commit uses the lane prefix and is published to
`lane/W-contract-1` with a normal push; no rebase, force-push, tag or new PR.

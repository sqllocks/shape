# W0-available status

Implementation complete. All 73 task fixtures and both checker modes pass.
The full Make gate remains failing; its baseline and final results are recorded below.
The earlier questions are resolved.

## Lead answer 1: P-BAR 5

Read this release from pyproject.toml at check time; do not hard-code a release.
Use its `## <version>` CHANGELOG section, falling back to `## Unreleased` only
when the version heading is absent. Rule 5 applies only when the page section's
`since:` equals that version. Cover numbered-section success, Unreleased fallback,
missing Promoted failure, and a different since version being exempt.

## Lead answer 2: shrink-only ratchet

Generate tests/docs/available_legacy.txt once, sorted, from today's available
public docs. Every entry must still exist and say available; otherwise report
`AVAILABLE: <page>: ratchet: no longer available; remove it from tests/docs/available_legacy.txt`.
Unlisted available pages must pass rules 1 to 5. Compare against the same file
at `git merge-base HEAD origin/main`, read using git show. Additions fail with
`AVAILABLE: <page>: ratchet: the legacy list may only shrink`. No file at the
base means no growth check. Without git, origin/main or a merge base, print
one stderr notice `AVAILABLE: ratchet growth not checked: no baseline (<reason>)`;
this alone passes locally, but --require-base exits 2. Make uses no flag;
only the CI test job uses --require-base, with full history and a main fetch.
Tests cover exactness, unlisted pages, growth, shrinking, introduction of the
file, and unavailable baselines in both modes.

## Lead answer 3: AVAILABLE.md layout

Each page is one section headed `## <docs path>`, followed by a plain
`since: <version>` line and five-column claim-to-proof rows of the form
`| AV-CANARIES-01 | claim (short) | proof | evidence | status |`.
Malformed sections name the page and fail. The new ledger has no page sections;
fixtures exercise the layout.

## Choices made

- The empty public ledger is experimental until promotion sections exist; it is linked from the Project navigation.
- Use Claim/Statement/Proof/Evidence/Status table headers and the since line as the first nonblank line after each page heading.
- JSON evidence uses shape (or shape_version), command and ISO date alongside the stated transcript fields; Owner receipts record their command in surrounding text and use comma-separated paths, while JSON uses a list of existing repository-relative source files.
- Experimental blocks render from standalone delimiter lines; inline delimiter examples remain literal. Owner inventory still checks the whole page, as rule 4 requires.
- Commandless pages omit Covers; command pages declare comma-separated top-level shape commands, including optional plugin dictionary keys.
- Malformed sections report P-BAR 2; demotions and malformed fences report P-BAR 1; other message wording names the claim and failed input.

## Baseline

`python -m venv .venv` and activated `make bootstrap`: exit 0, native kernel built.
The toolchain installed by the build is Rust stable 1.99.0; subsequent Make
commands select its existing cache through RUSTUP_HOME, CARGO_HOME and PATH.
Initial `make check`: exit 2 before any repository changes. Pytest reported
14 failed, 18,390 passed, 25 skipped, 69 deselected, 21,277 warnings in 4713.08s.
Coverage reported 47% against the unchanged 86% floor. Failures comprise the
file-sink umask case and 13 documentation cases (missing Playwright, plugin
sinks, dbt fixture packages, container setup and transcript mismatches).
Heavy, pure-kernel and Rust targets were not reached after the core-test failure.
Full local log: /tmp/W0-baseline-check.log.

## Red evidence

Added tests before adding scripts/check_available.py or the warning hook.
`pytest -q --tb=no tests/release/test_check_available.py`: exit 1.
The checker was absent and the existing hook did not render experimental fences.
The complete emitted output follows:

```text
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF [100%]
=========================== short test summary info ============================
FAILED tests/release/test_check_available.py::test_clean - AssertionError: /w...
FAILED tests/release/test_check_available.py::test_missing_proof[] - Assertio...
FAILED tests/release/test_check_available.py::test_missing_proof[`tests/missing.py::test_missing`]
FAILED tests/release/test_check_available.py::test_missing_proof[`tests/test_proof.py::test_gone`]
FAILED tests/release/test_check_available.py::test_missing_proof[`ci:ci.yml/unknown`]
FAILED tests/release/test_check_available.py::test_missing_proof[`ci:missing.yml/test`]
FAILED tests/release/test_check_available.py::test_missing_proof[`ci:test`]
FAILED tests/release/test_check_available.py::test_existing_job_parameter_family_and_class[`ci:ci.yml/test`]
FAILED tests/release/test_check_available.py::test_existing_job_parameter_family_and_class[`tests/test_proof.py::test_param`]
FAILED tests/release/test_check_available.py::test_existing_job_parameter_family_and_class[`tests/test_proof.py::test_param[1]`]
FAILED tests/release/test_check_available.py::test_existing_job_parameter_family_and_class[`tests/test_proof.py::TestProof`]
FAILED tests/release/test_check_available.py::test_partial_collection_error_fails_closed
FAILED tests/release/test_check_available.py::test_experimental_command - Ass...
FAILED tests/release/test_check_available.py::test_undeclared_stable_command
FAILED tests/release/test_check_available.py::test_declared_unstable_command_without_mention
FAILED tests/release/test_check_available.py::test_optional_plugin_stability
FAILED tests/release/test_check_available.py::test_experimental_fences_are_skipped
FAILED tests/release/test_check_available.py::test_owner_note - AssertionErro...
FAILED tests/release/test_check_available.py::test_claim_bijection_and_done[missing_row]
FAILED tests/release/test_check_available.py::test_claim_bijection_and_done[missing_anchor]
FAILED tests/release/test_check_available.py::test_claim_bijection_and_done[duplicate_row]
FAILED tests/release/test_check_available.py::test_claim_bijection_and_done[open]
FAILED tests/release/test_check_available.py::test_rule_five_numbered_and_unreleased[2.4.6]
FAILED tests/release/test_check_available.py::test_rule_five_numbered_and_unreleased[Unreleased]
FAILED tests/release/test_check_available.py::test_rule_five_missing - Assert...
FAILED tests/release/test_check_available.py::test_rule_five_another_release_is_exempt
FAILED tests/release/test_check_available.py::test_rule_five_numbered_section_prevents_fallback
FAILED tests/release/test_check_available.py::test_demotion - AssertionError:...
FAILED tests/release/test_check_available.py::test_malformed_section[## docs/FEATURE.md\n 2.4.6\n\n| Claim | Statement | Proof | Evidence | Status |\n|---|---|---|---|---|\n| AV-FEATURE-01 | Works | `tests/test_proof.py::test_proof` | none | done |\n]
FAILED tests/release/test_check_available.py::test_malformed_section[## docs/FEATURE.md\n\n\n\n| Claim | Statement | Proof | Evidence | Status |\n|---|---|---|---|---|\n| AV-FEATURE-01 | Works | `tests/test_proof.py::test_proof` | none | done |\n]
FAILED tests/release/test_check_available.py::test_malformed_section[## docs/FEATURE.md\n\nsince: 2.4.6\n\n| Claim | Statement | Proof | Evidence | Status |\n|---|---|---|---|---|\n| AV-FEATURE-01 | Works | `tests/test_proof.py::test_proof` | done |\n]
FAILED tests/release/test_check_available.py::test_malformed_section[## docs/FEATURE.md\nsince: 2.4.6\n]
FAILED tests/release/test_check_available.py::test_malformed_section[## docs/FEATURE.md\n\nsince: 2.4.6\n\n| Claim | Statement | Proof | Evidence | Status |\n|---|---|---|---|---|\n| AV-FEATURE-01 | Works | `tests/test_proof.py::test_proof` | none | done |\n## docs/FEATURE.md\n\nsince: 2.4.6\n\n| Claim | Statement | Proof | Evidence | Status |\n|---|---|---|---|---|\n| AV-FEATURE-01 | Works | `tests/test_proof.py::test_proof` | none | done |\n]
FAILED tests/release/test_check_available.py::test_service_with_fresh_real_run
FAILED tests/release/test_check_available.py::test_owner_receipt - assert 2 == 0
FAILED tests/release/test_check_available.py::test_bad_service_evidence[missing]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[failed]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[stale]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[emulator]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[outside]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[no_version]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[no_date]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[no_command]
FAILED tests/release/test_check_available.py::test_bad_service_evidence[not_passed]
FAILED tests/release/test_check_available.py::test_emulator_test_alone_is_not_evidence
FAILED tests/release/test_check_available.py::test_stale_legacy_entry[False]
FAILED tests/release/test_check_available.py::test_stale_legacy_entry[True]
FAILED tests/release/test_check_available.py::test_unlisted_available_page_needs_rules
FAILED tests/release/test_check_available.py::test_legacy_exemption - assert ...
FAILED tests/release/test_check_available.py::test_ratchet_growth - assert 2 ...
FAILED tests/release/test_check_available.py::test_ratchet_shrinks - assert 2...
FAILED tests/release/test_check_available.py::test_introducing_list_has_no_growth_check
FAILED tests/release/test_check_available.py::test_unavailable_baseline[no_git]
FAILED tests/release/test_check_available.py::test_unavailable_baseline[no_main]
FAILED tests/release/test_check_available.py::test_unavailable_baseline[no_merge_base]
FAILED tests/release/test_check_available.py::test_old_receipt_requires_unchanged_source[False]
FAILED tests/release/test_check_available.py::test_old_receipt_requires_unchanged_source[True]
FAILED tests/release/test_check_available.py::test_usage_and_unreadable_input
FAILED tests/release/test_check_available.py::test_experimental_fences_render_as_a_warning
FAILED tests/release/test_check_available.py::test_unbalanced_experimental_fences_fail[<!-- experimental:start -->\nshape mask\n]
FAILED tests/release/test_check_available.py::test_unbalanced_experimental_fences_fail[<!-- experimental:end -->\n]
FAILED tests/release/test_check_available.py::test_unbalanced_experimental_fences_fail[<!-- experimental:start --><!-- experimental:start --><!-- experimental:end -->]
FAILED tests/release/test_check_available.py::test_missing_git_executable - A...
FAILED tests/release/test_check_available.py::test_job_name_outside_jobs_is_not_a_job
FAILED tests/release/test_check_available.py::test_rule_five_does_not_match_a_page_name_prefix
FAILED tests/release/test_check_available.py::test_plans_and_talks_are_outside_the_page_inventory
FAILED tests/release/test_check_available.py::test_since_line_must_precede_the_table
FAILED tests/release/test_check_available.py::test_owner_note_inside_experimental_block_still_blocks_promotion
FAILED tests/release/test_check_available.py::test_warning_hook_preserves_inline_fence_documentation
FAILED tests/release/test_check_available.py::test_checker_loads_like_the_existing_repository_checks
FAILED tests/release/test_check_available.py::test_commandless_page_can_be_available
FAILED tests/release/test_check_available.py::test_command_page_needs_a_covers_declaration
72 failed in 3.80s
```

## Additional red evidence: unrendered inline delimiters

Diff review found that inline delimiters could exempt a command without the
site rendering a warning. Added and ran the regression before the fix:
`pytest -q --tb=short tests/release/test_check_available.py::test_inline_delimiters_cannot_exempt_an_unrendered_command`: exit 1.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
__________ test_inline_delimiters_cannot_exempt_an_unrendered_command __________
tests/release/test_check_available.py:581: in test_inline_delimiters_cannot_exempt_an_unrendered_command
    fail(tree, 1, "malformed experimental fences")
tests/release/test_check_available.py:89: in fail
    assert result.returncode == 1, result.stdout + result.stderr
E   AssertionError: AVAILABLE: 0 problem(s)
E     AVAILABLE: ratchet growth not checked: no baseline (git, origin/main, or merge base unavailable)
E
E   assert 0 == 1
E    +  where 0 = CompletedProcess(args=['/workspace/shape/.venv/bin/python', '/tmp/pytest-of-agent/pytest-7/test_inline_delimiters_cann...lem(s)\n', stderr='AVAILABLE: ratchet growth not checked: no baseline (git, origin/main, or merge base unavailable)\n').returncode
=========================== short test summary info ============================
FAILED tests/release/test_check_available.py::test_inline_delimiters_cannot_exempt_an_unrendered_command
1 failed in 0.98s
```

## What changed

- Added the stdlib P-BAR checker, canonical page-section parser, proof collection, service evidence freshness, owner inventory, release-note lookup, demotion guard and both ratchet checks.
- Generated the sorted 71-page legacy list once. Added the public empty ledger, navigation entry and Unreleased changelog entry.
- Wired Make directly after check_v1_done; changed only the CI test job, giving it full checkout history, an explicit main fetch and --require-base.
- Added the documentation warning hook and 73 fixture cases, including actual temporary Git histories.
- The initial post-implementation fixture run had two setup errors (orphan checkout shorthand and missing mock page title); fixed their setup without changing assertions. That run was exit 1 with 70 passed and 2 failed; the next run was exit 0 with all 72 passed.
- Diff review added the inline-delimiter regression. Its red run exited 1, then the full fixture suite exited 0 with all 73 passed in 39.84s after the checker fix.
- Red outputs retain their emitted text; trailing whitespace was trimmed for the Git whitespace check.

## Verification so far

- scripts/check_available.py: exit 0, zero problems, both default and --require-base (run before and after the fixture corrections).
- Ruff check on checker, hook and test file: exit 0. Ruff format check on the new checker and test file: exit 0.
- NO_MKDOCS_2_WARNING=1 mkdocs build --strict: exit 0, built in 16.20s.
- scripts/check_doc_links.py --site site: exit 0, zero broken links.
- scripts/check_user_facing.py --site site: exit 0, clean.
- git diff --check initially exited 0, then caught trailing whitespace in the newly appended red traceback (exit 2); only that status formatting was corrected.
- Final make check: exit 2; all 73 new fixtures passed. Full-suite details below.

## Open questions

None. All three lead answers were applied.

## Commands and observed exit codes in this session

All repository commands ran with cwd /workspace/shape. Workspace startup and
attachment retrieval preceded reading the task. Shell invocations joined with
`&&` stop at the reported failure; later parts of those invocations did not run.
Repeated progress-only commands are listed once below with their observed codes.
Tool file patches succeeded; no plan file was copied into the checkout.

### Startup, branch and required reads

- Attachment download initially failed because the workspace was starting; wait_for_environment returned ready, and the second download succeeded.
- Read the cloud runtime skill and networking reference; environment_status returned successfully. Read the Docker reference before further Docker diagnostics.
- `pwd && ls -la && cat /etc/codex/network-policy.json` from the startup workspace: 0.
- Read the uploaded task; `git status --short --branch` returned clean branch work; `rg --files` for AGENTS/task filenames found none: combined invocation 1.
- `git remote get-url origin`, file discovery, `cat CLAUDE.md CONTRIBUTING.md`, `git status --porcelain`: 0; origin was https://github.com/sqllocks/shape.git.
- Retry-capable Python wrapper running `git fetch origin`: 0 on the initial attempt, no network retry needed.
- Initial lane checkout/merge/plan-read chain: 128 at checkout because the default fetch refspec contained only main; subsequent operations in that chain did not run.
- `rg --files -g AGENTS.md /workspace /tmp` (with head): 0; none applied to this repository.
- Read origin fetch config, branches and `git ls-remote --heads origin lane/W0-available plan/promotion-093`, then explicitly fetched both lane and plan refs with the same 2/4/8/16-second retry wrapper: 0, first attempt.
- `git checkout -B lane/W0-available origin/lane/W0-available`: 0, head 5886ca5. `git merge -m 'W0-available: merge main into task lane' origin/main`: 0, already up to date.
- `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md` and full V1_DONE/check_v1_done/cli_surface/stable_api_compat reads: 0.
- Bulk plan/inventory/stability/Make/CI/earlier-status read: 2 because the final search named absent tests/release/test_check_v1_done.py. The plan opening, Decisions and section 1.3 were visible in full.
- Focused inventory, stability, Make, CI, pytest/nav/version configuration and actual v1 test-file reads; identity reads; `python -m venv .venv`; activated `make bootstrap`: 0.
- Read bootstrap log, check_secrets test model, tests/cli/test_v1_done.py and evidence paths: 2 at the absent scripts/docs_hooks.py; the actual hook was then found as scripts/mkdocs_hooks.py.
- Hook/evidence/pytest-config reads and toolchain discovery: 1 because cargo was not on the inherited PATH. Bootstrap itself continued and succeeded using its automatically installed Rust toolchain.
- Read the real hook, conftest and toolchain-cache bin listing: 0.
- Bare cached rustc invocation outside its RUSTUP_HOME: 1 (no default configured). Selecting the existing cache with RUSTUP_HOME/CARGO_HOME gave `rustc --version` 0 (stable 1.99.0); `.venv/bin/python -m pip --version`: 0.
- Documentation-style, wording-check and docs-test reads, process inspection, bootstrap-log reads: 0.
- Activated Make baseline with existing Rust cache selectors: 2; complete test summary and 47% coverage recorded above. It ran to completion before any repository edit.
- Read mkdocs config and CHANGELOG, and searched the plan for the plugin dictionary shape: 0.
- Managed Docker socket check with endpoint/context/TLS selectors cleared and `info --format` limited to version/driver: 0, Docker 28.4.0/vfs.
- Process cwd inspection for nested pytest: 0, confirmed the CONTRIBUTING example's isolated test run.
- Progress reads (`tail` on bootstrap/baseline/final/fixture/docs logs), process listings (`ps` plus rg), `df -h /workspace`, available-page count search: 0 on every invocation.
- A baseline-summary search before the suite had completed returned 1 because FAILED/TOTAL lines did not exist yet; its chained final status read did not run.

### Tests, implementation and documentation verification

- Baseline failure detail search, clean-tree status, `cp /tmp/W0-test_check_available.py tests/release/test_check_available.py`, activated red pytest: 1 from pytest, all 72 cases failed as recorded above.
- Baseline/red summary reads and status: 0.
- Copied the checker and ledger drafts into the checkout; one-time Python generation of tests/docs/available_legacy.txt: 0, exactly 71 sorted paths.
- `ruff check --fix scripts/check_available.py tests/release/test_check_available.py`: 1 (one import fix applied, remaining line-length and loop-binding errors). Its chained format/pytest commands did not run. Recorded all lead answers, baseline and full red output: 0.
- Bound the section page in the parse diagnostic helper; `ruff format`, `ruff check` on the two new Python files: 0; first implemented fixture pytest: 1 (70 passed, two fixture setup errors).
- Default and --require-base checker, `git diff --check` and diff summary: 0.
- First `NO_MKDOCS_2_WARNING=1 mkdocs build --strict`: 0, 16.20s. `check_doc_links.py --site site` and `check_user_facing.py --site site`: 0 each.
- Read the fixture traceback, checker source and docs log: 0.
- Fixed the disconnected-history fixture to detach at its saved original SHA and supplied the page mock's required title; format/check: 0; full fixture pytest: 0, 72 passed in 38.96s. Assertions were not weakened.
- Reviewed the entire CI/Make/changelog/hook/nav diff and checker implementation; searched the saved SHA fixture references; `git diff --check`: 0.
- `ruff check scripts/check_available.py scripts/mkdocs_hooks.py tests/release/test_check_available.py`: 0. `ruff format --check` on the two new Python files: 0. Status inspection: 0.
- Added the unrendered inline-delimiter regression and ran its exact node with `pytest -q --tb=short`: 1 (the checker incorrectly returned 0); full red output recorded above.
- Fixed standalone-delimiter validation; format and Ruff check: 0; full fixture pytest: 0, 73 passed in 39.84s. Appended the additional red output: 0.
- Added the ledger sentence requiring each delimiter to occupy its own line. A subsequent `git diff --check`: 2, caught trailing whitespace in the newly appended red traceback; the checker commands chained after it did not run.
- Trimmed only status-file trailing whitespace, appended results and implementation summary, ran default checker and --require-base checker and `git diff --check`: 0 each.
- Final activated `make check` with the same Rust cache selectors: exit 2; every preflight including check_available.py passed. Pytest reported 15 failed, 18,463 passed, 25 skipped, 69 deselected and 21,277 warnings in 5119.32s; coverage was 47.43% against the unchanged 86% floor.
- Rebuilt the final docs with `NO_MKDOCS_2_WARNING=1 mkdocs build --strict`: 0, 21.86s; `check_doc_links.py --site site`: 0; `check_user_facing.py --site site`: 0.
- Read final preflight/docs logs, `git diff --numstat` and line counts: 0.

This status records commands through final verification. Commit and push outcomes
are reported in the final chat response, because recording their own results here
before execution would claim future results. Delivery targets only lane/W0-available.

## Final full-check result

`make check`: exit 2. All 14 baseline failures remain; the only additional
failure is the unchanged streaming realtime-rate test. Its maximum lag was
0.08587119500043627 seconds against the existing 0.05-second limit. No streaming
implementation or test was changed. All 73 new P-BAR cases passed in the full run.
Coverage is exactly the baseline value, 47.43%, below the unchanged 86% gate.
Heavy, pure-kernel and Rust targets were not reached after the core-test failure.

The final failure list is:

```text
FAILED tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left
FAILED tests/docs/test_brand.py::test_branded_site - ModuleNotFoundError: No ...
FAILED tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]
FAILED tests/docs/test_reference_examples.py::test_reference_page[CLI] - Fail...
FAILED tests/docs/test_reference_examples.py::test_reference_page[CONTAINER]
FAILED tests/docs/test_reference_examples.py::test_reference_page[DBT] - Asse...
FAILED tests/docs/test_reference_examples.py::test_reference_page[IMPORTERS]
FAILED tests/docs/test_reference_examples.py::test_reference_page[INSTALL] - ...
FAILED tests/docs/test_reference_examples.py::test_reference_page[SIMULATION_FILES_EVENTS]
FAILED tests/docs/test_reference_examples.py::test_reference_page[SINKS] - As...
FAILED tests/docs/test_reference_examples.py::test_reference_page[authoring]
FAILED tests/docs/test_reference_examples.py::test_reference_page[behavior]
FAILED tests/docs/test_reference_examples.py::test_reference_page[healthcare-codes]
FAILED tests/docs/test_reference_examples.py::test_reference_page[simulation]
FAILED tests/streaming/emit/test_runtime.py::test_realtime_rate_within_five_percent
15 failed, 18463 passed, 25 skipped, 69 deselected, 21277 warnings in 5119.32s
```

Full local logs: /tmp/W0-baseline-check.log and /tmp/W0-final-check.log.

## Remaining command results

- Staged exactly the ten task files; `git diff --cached --check`, cached diff summary, status and pyproject change-name inspection: exit 0. No pyproject change was present.
- Every subsequent final-progress log read: exit 0; the Make process itself completed with exit 2.
- Inspected the additional failure and compared baseline/final failure sets with Python: exit 0; no baseline failure disappeared, and only the timing test was added.
- An isolated timing recheck was mistakenly launched with `--cov-fail-under=0`: exit 0, one timing assertion passed. That invocation conflicted with the requested coverage policy and is discarded as gate verification. It changed no repository configuration or assertion. The mistake was acknowledged in chat.
- Corrected isolated command: `pytest -q tests/streaming/emit/test_runtime.py::test_realtime_rate_within_five_percent --cov=shape --cov-fail-under=86`: exit 1. The unchanged timing assertion passed; isolated coverage was 9.69%, so the retained 86% coverage gate failed. This does not make the full Make result pass.
- Final default checker and --require-base checker: exit 0 each, zero problems.
- Final Ruff check on checker, hook and test file: exit 0. Final Ruff format check on the new checker and test file: exit 0.
- Final status rewrite/failure-set comparison and whitespace checks are verified before the delivery commit; their observed result is reported in the final chat alongside commit/push results.

No open question remains. No release version, existing test assertion, recorded
regression store or golden corpus was changed. CI edits affect only the test job.

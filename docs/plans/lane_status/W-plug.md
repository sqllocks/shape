# W-plug lane status

Target: 0.9.4. No commands promoted.

Status: blocked by a plan/code contradiction. Partial implementation, not ready for integration.

## Pre-change check

`python -m venv .venv; . .venv/bin/activate; make bootstrap`: exit 0.

`make check`: exit 2. Core pytest: 17 failed, 18371 passed, 25 existing skips, 69 deselected, 16 errors. Coverage 47.43%, below the unchanged 86% gate. Full log: `/tmp/W-plug-baseline-check.log`.

## Red evidence (before implementation)

`pytest -q tests/cli/test_plugin_surface.py tests/plugins/test_stability_promise.py`: exit 1.

```text
=========================== short test summary info ============================
FAILED tests/cli/test_plugin_surface.py::test_plugin_registry_and_live_baseline
FAILED tests/cli/test_plugin_surface.py::test_listed_names_and_fabric_aliases_have_the_same_level
FAILED tests/cli/test_plugin_surface.py::test_stubs_and_loaded_help_mark_experimental_commands
FAILED tests/cli/test_plugin_surface.py::test_listed_help_has_no_marker_and_only_its_fabric_alias_is_stable
FAILED tests/cli/test_plugin_surface.py::test_loader_refuses_another_distribution_before_calling_its_factory
FAILED tests/cli/test_plugin_surface.py::test_alias_group_cannot_come_from_another_distribution
FAILED tests/cli/test_plugin_surface.py::test_snapshot_loads_both_fabric_spellings_and_setup_rename
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[flag-publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[flag-fabric publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[choice-publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[choice-fabric publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[value-publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[value-fabric publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[positional-publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[positional-fabric publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[command-publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[command-fabric publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[demotion-publish]
FAILED tests/cli/test_plugin_surface.py::test_each_spelling_rejects_breaking_changes[demotion-fabric publish]
FAILED tests/cli/test_plugin_surface.py::test_distribution_removal_or_transfer_breaks
FAILED tests/cli/test_plugin_surface.py::test_plugin_writer_accepts_additions_and_refuses_breaks
FAILED tests/cli/test_plugin_surface.py::test_a_missing_listed_plugin_fails_snapshot_and_check
FAILED tests/cli/test_plugin_surface.py::test_missing_listed_plugin_is_one_line_exit_2[argv0]
FAILED tests/cli/test_plugin_surface.py::test_missing_listed_plugin_is_one_line_exit_2[argv1]
FAILED tests/cli/test_plugin_surface.py::test_listed_bad_inputs_are_one_line_exit_2[publish-argv0]
FAILED tests/cli/test_plugin_surface.py::test_listed_bad_inputs_are_one_line_exit_2[publish-argv1]
FAILED tests/cli/test_plugin_surface.py::test_listed_bad_inputs_are_one_line_exit_2[fabric-argv2]
FAILED tests/cli/test_plugin_surface.py::test_listed_bad_inputs_are_one_line_exit_2[fabric-argv3]
FAILED tests/cli/test_plugin_surface.py::test_every_listed_command_and_alias_has_a_bad_input_exit_class
FAILED tests/cli/test_plugin_surface.py::test_listed_distributions_are_in_the_existing_lockstep_check
FAILED tests/cli/test_plugin_surface.py::test_exit_code_generator_includes_both_spellings_of_listed_commands
FAILED tests/cli/test_plugin_surface.py::test_plugin_surface_is_checked_in_make_and_ci
FAILED tests/plugins/test_stability_promise.py::test_stable_plugin_commands_are_under_the_cli_promise
33 failed, 29 passed in 1.82s
```


## Exact question

Section 6.2 says that reusing `compare()` rejects a renamed positional. The existing
`_compare_positionals()` compares only `nargs` and choices, ignoring positional names and
order. May W-plug extend the shared comparator to enforce positional names/order for both
core and plugin commands, or should that correction be assigned to a separate task?

Reproduction (exit 0): load the core baseline, copy it, rename the first stable `check`
positional from `PROFILE.shape` to `RENAMED`, then call `compare(baseline, live)`:

```text
Renamed stable positional: PROFILE.shape -> RENAMED
Compatibility problems: []
Compatibility notes: []
```

Stopped under the task's explicit rule for a plan/code contradiction. No correction to the
shared comparator has been guessed. The final `make check` has not been run because work
stopped at this blocker.

## Partial changes

- Empty `STABLE_PLUGIN_COMMANDS`, stability resolution, and ownership refusal before loading
  a listed command from the wrong distribution. The Fabric dispatcher also needs the listed
  command's owner.
- `snapshot_plugins()`, an empty second baseline, `--plugins` for checking/writing, and wiring
  in `make check` and CI. Per-distribution snapshots cover both documented Fabric spellings,
  including the `setup-fabric` / `fabric setup` rename. The existing comparator is reused.
- Experimental markers in plugin stubs and loaded parsers, including Fabric child help.
- A listed-command bad-input harness and one-line exit-2 handling, missing-plugin diagnostics,
  generator rows for listed commands, and a test of the existing lockstep coverage.
- Same-release and both-spelling promises in both stability pages; an Unreleased changelog
  entry. Plugin Protocol/kit and its API baseline are unchanged. No plugin is promoted, so
  there are no listed `--json` commands whose keys need pinning in this task.
- An existing plugin help assertion now requires the new experimental marker.

## Checks actually run

| Command/check | Exit | Result |
|---|---:|---|
| `python -m venv .venv` and `make bootstrap` | 0 | Native wheel built; the build backend installed Rust automatically. |
| Pre-change `make check` | 2 | 17 failures, 16 errors; 18371 passed. Coverage 47.43%, unchanged gate 86%. |
| Red focused tests, before implementation | 1 | 33 failed, 29 passed; summary above. |
| First focused tests after implementation | 1 | 111 passed; the old plugin stub assertion still expected no marker. |
| Focused tests after requiring the new marker | 0 | 112 passed in 9.20s. |
| First strict `mypy` | 1 | Parser `error()` override needed `NoReturn`. |
| Final strict `mypy` | 0 | No issues in 698 source files. |
| Final `ruff check src tests plugins benchmarks/vs_refengine` | 0 | Passed. |
| Final `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 | 2080 files formatted. |
| `python scripts/cli_surface.py --check` | 0 | 0 breaking changes, 0 notes. |
| `python scripts/cli_surface.py --check --plugins` | 0 | 0 breaking changes, 0 notes; list remains empty. |
| `python scripts/cli_surface.py --write --plugins` | 0 | Created only the new, empty plugin baseline. |
| `python scripts/gen_exit_codes.py --check` | 0 | Existing generated document unchanged with an empty list. |
| `python scripts/check_versions.py` | 0 | 58 locations agree on the checkout's version. No release version changed. |
| `python scripts/check_user_facing.py` | 0 | Clean. |
| `git diff --check` | 0 | Clean. |
| Positional-rename reproduction | 0 | No problems reported, contradicting section 6.2. |

Focused pytest command:

```bash
pytest -q tests/cli/test_plugin_surface.py tests/plugins/test_stability_promise.py tests/cli/test_cli_surface.py tests/plugins/test_host.py tests/plugins/test_plugin_cli.py tests/plugins/test_plugin_cli_inprocess.py
```

The pre-change failures include missing documentation/plugin prerequisites, packaging setup
without Rust on the shell path, a file-permission expectation, and documentation examples.
The complete original log is `/tmp/W-plug-baseline-check.log`. The baseline failed before
reaching the later heavy/Python-kernel/Rust targets; those targets are not reported as passed.
The baseline's 25 existing skips were not introduced by this lane.

## Command ledger

All shell commands ran from `/workspace/shape`, except the initial command which first did
`cd /workspace/shape` from `/workspace`. Read-only monitor commands were repeated as necessary;
every invocation is covered by its row below. Tool polling is not a shell command.

| Command(s) | Exit |
|---|---:|
| `cd /workspace/shape && git remote get-url origin` | 0 |
| `cat /etc/codex/network-policy.json` | 0 |
| `git status --short && git fetch origin` | 0 |
| Conditional branch setup: `git show-ref --verify --quiet refs/remotes/origin/lane/W-plug`, then `git checkout -b lane/W-plug origin/main` | 0 overall; branch-existence predicate 1 |
| `for f in CLAUDE.md AGENTS.md CONTRIBUTING.md; do if test -f "$f"; then cat "$f"; fi; done` | 0 |
| `git fetch origin plan/promotion-093 && git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md` | 128: fetch succeeded; remote-tracking ref absent under the main-only fetch refspec |
| `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093` and `git show ... > /tmp/PROMOTION_PLAN.md`, then Python printed the plan | 0 |
| `sed -n '/^## 6\./,/^## 7\./p' /tmp/PROMOTION_PLAN.md`, `rg --files -g AGENTS.md -g CLAUDE.md`, `cat src/shape/cli/stability.py scripts/cli_surface.py` | 0 |
| `python -m venv .venv && . .venv/bin/activate && make bootstrap` | 0 |
| Source reads: `cat src/shape/plugins/cli.py`; `rg -n 'command\|distribution\|load' src/shape/plugins/host.py`; `rg -n 'plugin\|annotate\|run_command' src/shape/cli/main.py`; `cat scripts/gen_exit_codes.py scripts/check_versions.py` | 0 |
| Reads of `tests/cli/test_cli_surface.py`, `tests/plugins/test_stability_promise.py`, both stability pages, `Makefile`, and CI plugin/surface/version lines | 0 |
| Reads of host discovery/loading/registration, CLI dispatch, exit codes, CLI errors and Fabric command implementations/entry points using `sed`, `cat`, `rg` | 0 |
| `git config user.name && git config user.email && command -v cargo && rustc --version` | 1: cargo not yet on PATH |
| `rg -n '^\[\|build\|maturin\|rust' pyproject.toml \| head -60 && ls /root/.cargo/bin/cargo` | 2: inaccessible path; no contents read |
| Read mypy/CI settings and search for CLI tests including absent `tests/plugins/test_cli.py` | 2: absent path in exploratory search |
| `ps -eo pid,etime,args \| rg 'pip\|maturin\|rust\|bootstrap' \| head -20` | 0 |
| Search exit-code definitions, list `tests/plugins` files, and read absent `tests/plugins/test_plugin_commands.py` | 2: absent file |
| `cat tests/plugins/test_plugin_cli_inprocess.py` | 0 |
| `ps -eo pid,etime,args \| rg 'rustup-init\|pip install\|prepare_metadata'` | 0 |
| `ls -la /home/agent/.cache/puccinialin && command -v python && python --version` | 0 |
| `ls /home/agent/.cache/puccinialin/rustup/toolchains && git status --short` | 0 |
| `ls /home/agent/.cache/puccinialin/rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin` | 0 |
| `git config --get-all remote.origin.fetch && git ls-remote --heads origin lane/W-plug`, then the toolchain-bin listing | 0; no remote lane existed |
| `du -sh` on the backend's Rust downloads/toolchains, then `ps` on Rust/Python setup | 0 |
| `. .venv/bin/activate && make check > /tmp/W-plug-baseline-check.log 2>&1`, capture code, `tail -80`, print and propagate code | 2 |
| Repeated `tail -12`, `tail -15`, `tail -10`, `tail -5`, or `tail -4 /tmp/W-plug-baseline-check.log`; occasional `git status --short` | 0 each |
| Read `docs/contributing/DOCS_STYLE.md`, `CHANGELOG.md` and `tests/regressions/test_iss_cli_errors.py` | 0 |
| `ps` filtered for pytest, docs examples, mkdocs, pip, native builds or `make check` | 0 each |
| Read `src/shape/plugins/host.py` error classes and tail baseline progress | 0 |
| Python/heredoc writes of new tests; red pytest to `/tmp/W-plug-red.log`, tail and propagate result | 1 (pytest); writes 0 |
| `mkdir -p docs/plans/lane_status` and Python write of red evidence before implementation | 0 |
| Python/heredoc implementation writes to stability, CLI dispatcher, plugin loader/dispatcher | 0 |
| Python/heredoc writes to surface/generator/Makefile/CI, then `cli_surface.py --write --plugins` | 0 |
| Python/heredoc docs/changelog writes, then changed-file `ruff check --fix` | 1: nine long lines; pytest was not reached; tail found no focused log |
| Python wrap/simplification, changed-file `ruff format`, changed-file `ruff check`, first focused pytest | Formatting/lint 0; pytest 1 |
| `git diff --stat` and `sed` reads of changed plugin CLI/stability | 0 |
| Python update of old help assertion, then focused pytest | 0 |
| First `mypy` to `/tmp/W-plug-mypy.log` | 1 |
| `git diff --check`, reads of surface/new baseline, and baseline error search | 0 |
| Python `NoReturn`/docstring edit, then full lint command | 1: missing colon from edit; fixed immediately |
| `ls` of backend-installed Cargo/toolchain bins | 0 |
| Python correction of colon; full Ruff checks, mypy, both CLI checks, exit-code/version/wording checks and `git diff --check` | 0 |
| Export backend-installed `CARGO_HOME`, `RUSTUP_HOME`, `PATH`; `rustc --version && rustup component add rustfmt clippy` | 0; Rust 1.99.0 |
| Python positional-rename reproduction using `cli_surface.compare()` | 0 |
| Python write of this blocker/status/ledger | 0 |

The commit and push below are solely to `lane/W-plug`. No PR, rebase, tag, force-push,
release version change, recorded regression-store change, or golden-corpus change is made.

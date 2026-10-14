# W-mask-1 — blocked by the unchanged baseline check

Scope: 0.9.4, promotion-plan section 4.3, Changes before freezing items 1–3 only.
No implementation, tests, public docs, release files, dependency files, lockfiles,
regression store, or golden corpus were edited. This status file replaces the
previous blocker notes.

The earlier questions are resolved: setup follows CONTRIBUTING.md using venv and
make bootstrap; --dry-run is absent from mask and remains W-mask-2 work. Item 4
is excluded. The plan was read from its own branch into /tmp/PROMOTION_PLAN.md;
that branch and its file were neither merged nor committed.

## Exact question for the lead

The required unchanged baseline `make check` deadlocks in
`tests/profile/test_audit_profile.py::test_concurrent_profiles_on_the_fork_pool_keep_their_own_columns`:
a fork worker is blocked in `coverage/collector.py:239` (`lock_data`) with
coverage 7.16.2. Which environment or upstream fix should W-mask-1 use to complete
the required baseline without changing dependencies, skipping tests, or lowering
gates?

## Baseline evidence

`make bootstrap` exited 0 and built the native Rust kernel. Its build backend
supplied Rust stable 1.99.0, meeting the required minimum. No separate toolchain
installation or kernel workaround was used.

`make check` was run before any task edits. Ruff lint and formatting, mypy
(698 source files), compileall, Vulture, import contracts, requirements, secrets,
user-facing wording, exit-code and failure-mode generation checks, shipped data,
plugin skeletons, versions, release workflows, conformance coverage, CLI surface,
and the v1 definition-of-done check all completed successfully in this session.

The core pytest stage ran with the unchanged 86% coverage gate. Failure, error,
and existing skip markers appeared before it stalled around 57%; there was no
completed pytest summary or coverage result. The later heavy, Python-kernel,
and Rust stages of make check were not reached.

Diagnostic stack output from `py-spy dump --pid 2341`:

```text
Thread 2341 (idle): MainThread
    _wait_for_tstate_lock (threading.py:1169)
    join (threading.py:1149)
    test_concurrent_profiles_on_the_fork_pool_keep_their_own_columns (test_audit_profile.py:157)
Thread 37439 (idle): Thread-863 (run)
    wait (threading.py:355)
    next (multiprocessing/pool.py:861)
    _profile_cols (shape/profile/reference/table.py:372)
```

Diagnostic stack output from `py-spy dump --pid 37442`:

```text
Thread 37442 (idle): Thread-863 (run)
    lock_data (coverage/collector.py:239)
    _fork_task (shape/profile/reference/table.py:304)
    worker (multiprocessing/pool.py:125)
```

The other fork worker was idle waiting for multiprocessing queue input. Reading
the test confirmed its `t.join()` has no timeout. The blocked pytest processes
were terminated after collecting this evidence. `make check` then exited 2:

```text
Terminated
make: *** [Makefile:25: check] Error 143
```

This is an interrupted, blocked baseline, not a passed check. No test was
modified, weakened, marked skip/xfail, or removed. Work stopped under the user's
ambiguity rule rather than selecting an unapproved environment adjustment or
changing unrelated profiling code.

## Red evidence

None: the required baseline could not complete, so STEP 4 was not started.
No new failing tests or implementation exist in this session.

## Command log

All shell commands ran from /workspace/shape. Inspection batches are grouped;
exit codes below distinguish failed subcommands where the shell continued.
Tool polling is not a separate shell command. Reads are listed without their
contents to avoid reproducing prohibited historical terminology.

| Command / inspection | Exit / result |
| --- | --- |
| `cd /workspace/shape`; `git remote get-url origin`; `git status --short`; `cat /etc/codex/network-policy.json` | 0 each; correct remote, clean checkout |
| `git fetch origin` | 0, first attempt |
| Initial `git checkout -B lane/W-mask-1 origin/lane/W-mask-1` | 128; checkout fetched only main by default |
| Initial `git merge origin/main -m 'W-mask-1: merge main into task lane'` | 0; already up to date, no merge commit created |
| `cat CLAUDE.md`; conditional `cat AGENTS.md`; `cat CONTRIBUTING.md` | 0; no local AGENTS.md present |
| `git fetch origin plan/promotion-093` | 0; FETCH_HEAD only |
| Initial `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md` | 128; tracking ref absent |
| `git config --get-all remote.origin.fetch`; `git branch --show-current`; `git status --short`; `git ls-remote --heads origin lane/W-mask-1 plan/promotion-093`; `git log -1 --format='%h %s'` | 0 each; confirmed both remote branches exist and main-only fetch configuration |
| `git fetch origin refs/heads/lane/W-mask-1:refs/remotes/origin/lane/W-mask-1 refs/heads/plan/promotion-093:refs/remotes/origin/plan/promotion-093` | 0, first attempt |
| `git checkout -B lane/W-mask-1 origin/lane/W-mask-1` | 0 |
| `git merge origin/main -m 'W-mask-1: merge main into task lane'` | 0; already up to date, no merge commit created |
| `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md`; Python read of opening paragraph, Decisions, and full section 4.3 | 0 each |
| `python -m venv .venv`; `. .venv/bin/activate`; `make bootstrap` | 0 each |
| File discovery for mask tests and lane status; `cat Makefile`; `cat src/shape/cli/mask.py`; `cat docs/MASK.md` | 0 batch |
| Read keyed mask CLI tests and prior lane status; search seed, output, warning, profiling, dispatch, and argv handling; `git config user.name`; `git config user.email`; discover documentation example scripts | 0 batch |
| Search CSV warning and profile reader interfaces; read profile package entry point and docs style; inspect MASK fixture setup | 0 batch; searches of nonexistent CSV glob and scenario package paths returned 2, corrected in subsequent reads |
| Read profile signature, CsvFormat, reader warning, CLI snapshot implementation, empty-output guards, reference-example harness, fixture setup, transcript comparison, and explicit-option action precedent | 0 batches |
| Discover AGENTS.md with `find /workspace -maxdepth 2 -name AGENTS.md -print` | 0; none applies to this repository |
| Read canary and gameday output guards, suite conftest, pytest configuration, pyproject.toml, existing built-in mask CLI tests, and CLI error policy | 0 reads |
| `rustc --version`; `cargo --version` before activating supplied toolchain paths | 127 each; executables absent from shell PATH |
| `test -f /workspace/AGENTS.md`; `test -f /AGENTS.md` | 1 each; absent |
| `ls /root/.cargo/bin/rustc` | 2; inaccessible path; no contents read |
| `readlink /proc/977/exe` | 0; located build backend's supplied cargo |
| `ls .venv/bin/cargo .venv/bin/rustc` | 2; not installed at those paths |
| List supplied stable toolchain bin and cargo bin; `ls /home/agent/.cache/puccinialin/cargo/env`; list supplied rustup directory | 0 each |
| Repeated `git status --short`; `. .venv/bin/activate`; `command -v python`; supplied stable `rustc --version` | 0 each; no task changes; Rust 1.99.0 |
| Repeated `ps -eo pid,etime,comm`, `ps -eo pid,etime,pcpu,comm` with tail | 0 each; confirmed native build and nested test activity, then idle blocked workers |
| `. .venv/bin/activate`; baseline `make check` | 0 activation; check exited 2 after diagnostic termination (pytest signal exit 143) |
| Python inspection of only PYTEST_CURRENT_TEST entries for the three pytest processes | 0; no entries available; no other environment values printed |
| `. .venv/bin/activate`; `py-spy dump --pid 2341` | 0 each; parent blocked in unbounded thread join |
| `sed -n '120,180p' tests/profile/test_audit_profile.py`; `. .venv/bin/activate`; `py-spy dump --pid 37441`; `py-spy dump --pid 37442` | 0 each; confirmed queue wait and coverage lock wait |
| `kill -TERM 2341 37441 37442` | 0; stopped the deadlocked test processes |
| `. .venv/bin/activate`; `python -m pip show coverage` filtered to package name/version; `git status --short`; `git branch --show-current`; `git remote get-url origin`; `git rev-parse HEAD` | 0 each; coverage 7.16.2, clean lane, base 9cb33b17245e05ff36db025bf2dfed6353eb72c6 |
| Python replacement and publication-log refinement of this status record; `git diff --check`; `git diff --stat` | 0 each |

Publication follows this report: stage this file, create a signed-off commit
whose message starts W-mask-1:, and push only lane/W-mask-1. Actual publication
exit codes and commit hash are reported in the final response because they occur
after writing this record. No pull request, tag, force-push, or rebase is used.

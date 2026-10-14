# W-gameday-1 — action-list question

Scope: 0.9.4. Setup is resolved using the lead-approved virtual environment and
`make bootstrap`. No source, tests, dependencies, release files, regression data,
golden corpus, gates or tolerances were changed. Only this lane-status document
is updated. No plan content was committed or merged.

## Exact question

For `gameday run --dry-run --json`, what exact entries must the generic
`shape-dry-run` document's `actions` list contain: only the output directory,
or also the report files, round directories, copied tables and check outputs?

Section 4.2 specifies the generic document but not the action-list contents.
`machine.dry_run_document` supplies `format`, `version`, `command` and `actions`;
it accepts the actions from its caller. `gameday run` has a native dry run but
no entry in `machine.SPECS` or existing action planner. The scenario runner's
dry-run report contains injection previews, while the actual run writes two
reports, round directories, copied tables and outputs from the supplied checks.
Choosing the action granularity would set a new contract without a stated rule.
Per the task's ambiguity rule, implementation stops here pending the answer.

## Tests-first state

No tests or implementation have been written, so there is no red evidence.
The unresolved action list prevents writing the requested shape-and-keys test
without guessing. The existing tests and all gates remain unchanged.

## Setup and baseline

- `python -m venv .venv`: exit 0.
- `. .venv/bin/activate` followed by `make bootstrap`: exit 0; native Rust core
  and domains plugin built and installed. No toolchain workaround was needed.
- Baseline `make check`: 2. Full output was captured in
  `/tmp/W-gameday-1-baseline-check.log` and its exit code in
  `/tmp/W-gameday-1-baseline-check.exit`.
  Core pytest: 17 failed, 18,371 passed, 25 skipped, 69 deselected,
  21,277 warnings and 16 errors in 4,516.73 seconds. The existing skips were
  not added or changed. Make stopped at the core suite (underlying pytest
  exit 1), so heavy tests, pure-Python kernel tests and Rust checks did not run.
  Example baseline failures: the file-sink permission test observed mode 0600;
  the branded-site test could not import `playwright`; release packaging
  setup reported `Cargo metadata failed. Do you have cargo in your PATH?`
  followed by `No such file or directory (os error 2)`.
  These results precede any repository edit.
- Checks completed before pytest: Ruff lint, Ruff formatting, mypy, compileall,
  Vulture, import contracts, requirements, secrets, user-facing wording,
  generated exit codes, generated failure modes, shipped data, plugin skeletons,
  versions, release workflows, conformance coverage, CLI surface and v1 checklist.
  Each exited 0 in this session.

## Session command journal

All shell invocations ran from `/workspace/shape`, except the first invocation
which began with `cd /workspace/shape` as requested. Inspection commands are
grouped below; every invocation listed in a group exited 0 unless stated.
Polling tool calls read process output and are not additional shell commands.

| Command or invocation | Exit / result |
| --- | --- |
| `cd /workspace/shape`; `git remote get-url origin`; `git status --short`; `cat /etc/codex/network-policy.json` | 0; origin is `https://github.com/sqllocks/shape.git`, clean tree |
| `git fetch origin` (retry loop installed, no retry needed) | 0 |
| `git checkout -B lane/W-gameday-1 origin/lane/W-gameday-1`; `git merge -m 'W-gameday-1: merge main into task lane' origin/main` | overall 0; checkout 128 because the remote fetch configuration tracked only main; merge 0, already up to date, no commit |
| `git branch --show-current`; `git config --get-all remote.origin.fetch`; `git ls-remote --heads origin lane/W-gameday-1` | 0; remote lane exists |
| `git fetch origin refs/heads/lane/W-gameday-1:refs/remotes/origin/lane/W-gameday-1` (retry loop installed, no retry needed) | 0 |
| `git checkout -B lane/W-gameday-1 origin/lane/W-gameday-1 && git merge -m 'W-gameday-1: merge main into task lane' origin/main` | 0; already up to date, no merge commit needed |
| `cat CLAUDE.md`; conditional `cat AGENTS.md`; `cat CONTRIBUTING.md` | 0; no repository AGENTS.md |
| `git fetch origin plan/promotion-093` (retry loop installed, no retry needed) | 0; fetched to FETCH_HEAD only |
| `git fetch origin refs/heads/plan/promotion-093:refs/remotes/origin/plan/promotion-093` (retry loop installed, no retry needed) | 0 |
| `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md`; Python extract of introduction, Decisions and 4.2; `cat src/shape/cli/machine.py`; instruction/gameday/Makefile file search; read earlier lane-status file | 0 |
| `python -m venv .venv`; activation; `make bootstrap > /tmp/W-gameday-1-bootstrap.log 2>&1`; log tail; exit with bootstrap code | 0 |
| `sed` full 4.2; `cat` CLI gameday module, scenario tests and Makefile; `rg` dry-run/report handling in gameday docs, scenario runner and machine module; workspace AGENTS.md file search | 0 |
| `sed` machine lines 312–404, scenario runner lines 478–520 and gameday docs lines 70–100; `rg` dry_run_document callers; conditional reads of `/workspace/AGENTS.md` and `/AGENTS.md` | 0; ancestor instruction files absent |
| `sed` emit lines 715–755; `git config user.name`; `git config user.email` | 0 |
| Bootstrap-log tails; `ps -eo pid,etime,comm` filtered for build processes | 0 |
| activation; `make check > /tmp/W-gameday-1-baseline-check.log 2>&1`; Python exit-code capture; log tail; exit with check code | 2 |
| Baseline-log tails; `git status --short` | 0 |
| Python creation of this status draft in `/tmp/W-gameday-1-status-draft.md` | 0 |
| Post-baseline `git status --short`; read exit-code file; Python extraction of representative failures | 0; working tree still clean |
| `sed -n '1,60p' /tmp/W-gameday-1-baseline-check.log`; Python update and copy of status draft | 0 |

The draft was copied into the lane-status path. `git diff --check` and
`git diff --stat` exited 0; only this file changed. A final Python journal edit
exited 0. Publication runs `git diff --check` once more, stages only this path,
and commits with DCO sign-off and the required task prefix.
Only `git push -u origin lane/W-gameday-1` will be used; its network-failure retry
schedule is 2, 4, 8 and 16 seconds. Publication outcomes are reported in the final
response. No pull request, rebase, tag or force push is used.

# W0-available status

Blocked before setup or implementation under the task instruction to stop on ambiguity.

## Exact question

Section 1.3 requires P-BAR 5 to inspect the CHANGELOG release section named by
`pyproject.toml`, but this checkout has `version = "0.9.2"`. This task targets
0.9.4 and permits CHANGELOG lines only under `## Unreleased`. Should P-BAR 5
validate `since: 0.9.4` against `## Unreleased` while the package version remains
0.9.2, or should it strictly use the current package version and its numbered
release section until the lead updates the version?

## Work and command results

No implementation, tests, dependency files, regression stores, golden corpus,
release files, or plan-branch contents were changed. No setup or checks were run.

All repository commands ran from `/workspace/shape` after the initial directory
change. Commands grouped in a single shell invocation are recorded together with
the invocation exit code.

- `cd /workspace/shape; git remote get-url origin; git status --short; cat /etc/codex/network-policy.json`: exit 0. Origin is `https://github.com/sqllocks/shape.git`; working tree was clean.
- `git fetch origin` (with retry loop prepared): exit 0; no retries needed.
- Conditional branch setup (`git show-ref --verify --quiet refs/remotes/origin/lane/W0-available`, then `git checkout -b lane/W0-available origin/main`), followed by reading `CLAUDE.md`, optional `AGENTS.md`, and `CONTRIBUTING.md`: exit 0. Remote lane did not exist; AGENTS.md was absent.
- `git fetch origin plan/promotion-093`: exit 0; no retries needed. Subsequent `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md`: exit 128 because the remote-tracking ref had not been created.
- `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`, then the required `git show` to `/tmp/PROMOTION_PLAN.md` and a Python read of the plan: exit 0; no retries needed. Plan was neither merged nor committed.
- Python read of the plan opening, Decisions and full section 1.3; `cat src/shape/cli/stability.py`; `rg -n '^version|^## |^### Promoted' pyproject.toml CHANGELOG.md`; `cat scripts/check_v1_done.py`; `cat docs/V1_DONE.md`: exit 0. Package version and release-target ambiguity established.
- `mkdir -p docs/plans/lane_status` and Python write of this status file: exit 0.

## Checks

- Baseline `make check`: not run (stopped on ambiguity).
- Red tests: not written or run (stopped on ambiguity).
- Final `make check`: not run (stopped on ambiguity).

## Status-file verification and delivery

- `git diff --check`: exit 0.
- `git add docs/plans/lane_status/W0-available.md`: exit 0.
- Signed-off blocker commit and `git rev-parse HEAD`: exit 0.
- The status-only amendment and lane push are reported in the final chat response with their observed results.

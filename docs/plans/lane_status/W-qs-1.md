# W-qs-1 lane status

Status: blocked pending clarification. Base: origin/main at `a8e476ca910c40e3d8e7dd0dace9c80699237e82`.

## Exact question

The prompt requires deleting the `shape query` line in QUICKSTART example 2, its output and its manifest entry together. `tests/docs/reference_examples.json` records complete examples, not individual commands: `{ "page": "docs/QUICKSTART.md", "index": 1, "kind": "local" }` registers both `shape show` and `shape query`. Should we retain this entry for the remaining `shape show` example, or remove it and update the hardcoded 206-entry assertion in `test_restored_example_inventory`? The latter unregisters an example that remains runnable on the page. No dependent edits made while awaiting the answer.

## Setup and inspection

Origin verified as `https://github.com/sqllocks/shape.git`. Created an independent clone under `/workspace/prompt-runs/checkouts/W-qs-1`, set the original origin and Git identity, fetched origin and the plan branch, and created `lane/W-qs-1` from origin/main. No AGENTS.md present. Read CLAUDE.md, CONTRIBUTING.md, plan Decisions and section 4.4; plan not copied into tracked files.

Python 3.11 is unavailable; used supported Python 3.12 with a private venv. Reused third-party dependency links from the initial checkout, excluding core/plugin/editable mappings. Rust uses the preinstalled onboarding toolchain and the reusable compiled target specified by the lead. Bootstrap is running; final setup result will be recorded when it completes.

## Commands and exit codes

- `cat /workspace/prompt-runs/W-qs-1.md; git -C /workspace/shape remote -v`: 0.
- `cd /workspace/shape && git remote get-url origin && git config user.name && git config user.email && git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-qs-1`: 0.
- `git remote set-url origin https://github.com/sqllocks/shape.git`; original `git config user.name` and `user.email`; `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`; `git checkout -B lane/W-qs-1 origin/main`: all 0.
- `cat CLAUDE.md CONTRIBUTING.md`: 0; `rg --files -g AGENTS.md`: 1 (none found).
- `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/qs-plan.txt`; `rg -n 'Decisions|4.4|QUICKSTART' /tmp/qs-plan.txt`: 0.
- `sed` reads of plan Decisions/4.4 and QUICKSTART; `cat docs/QUICKSTART.md`; `head` reads of tutorial/reference tests; Makefile and requirements-docs reads: 0.
- `rg -n 'QUICKSTART|shape query|Models examples' ... docs/examples* ...`: 2 (unmatched docs/examples glob; relevant matches printed). Repository filename searches: 0.
- `python3.12 -m venv .venv`; Python helper linking third-party dependencies: 0.
- Python helper inspecting QUICKSTART manifest entries; `rg` manifest search; `sed` reference-test read: 0.
- `tail -12 /tmp/qs-bootstrap.log`; `git status --short`; `git rev-parse HEAD`: 0.

No behavior changed, so red evidence is not applicable yet. Checks deferred while setup runs. Open question above is the sole blocker.

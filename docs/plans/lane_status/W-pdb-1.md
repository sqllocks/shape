# W-pdb-1: blocked before implementation

Scope: 0.9.4 only. No code, tests, release files, regression data or golden
corpus changed.

Question for the owner: Where is the approved 0.9.4 promotion plan? After fetching
origin, `origin/main` at `c10923be9` does not contain
`docs/plans/PROMOTION_PLAN_0.9.3.md`. Please make the plan containing Decisions
13 and section 4.11 row profile-db (a) available on main, or identify the approved
replacement. Implementation is stopped rather than guessing those decisions.

No acceptance tests were written or run because the required plan is unavailable;
there is no red evidence yet. This is a blocker report, not completed W-pdb-1 work.

## Command record

Commands ran from `/workspace/shape` unless stated otherwise. Read-only inspection
batches list their component commands and individual outcomes below.

- `/workspace`: `pwd` (0); `rg --files -g AGENTS.md -g CLAUDE.md -g CONTRIBUTING.md -g PROMOTION_PLAN_0.9.3.md -g network-policy.json /workspace /etc/codex` (0).
- `cat /etc/codex/network-policy.json` (0).
- Initial `git fetch origin` (128): could not connect to proxy port 8080.
- Initial `git checkout -b lane/W-pdb-1 origin/main` (0), based on the previously cached remote ref.
- Initial `cat CLAUDE.md CONTRIBUTING.md` (0); `rg --files -g AGENTS.md` (1, no file); plan section search (2, missing file); `git status --short` (0); client-secret/resolver search in src, tests and SQL Server docs (0); `cat Makefile` (0).
- Retried `git fetch origin` with network permission (0): origin/main updated to c10923be9.
- `cat docs/CONTRIBUTING.md` (0); `rg --files docs/plans | head -30` (0); `sed -n '1,220p' docs/plans/COMPLETION_PLAN.md` (0), inspected before detecting the updated checkout's simpler instructions.
- `git checkout --detach origin/main` (0); `git branch -d lane/W-pdb-1` (1, branch not merged); `git checkout -b lane/W-pdb-1 origin/main` (128, branch already exists).
- Updated checkout: `cat CLAUDE.md CONTRIBUTING.md` (0); `rg --files -g AGENTS.md` (1, no file); plan section search (2, missing file).
- `git checkout -B lane/W-pdb-1 origin/main` (0): corrected the local lane base to the freshly fetched main; no force-push.
- `git status --short` (0, clean); `git ls-tree -r --name-only origin/main docs/plans` (0, required plan absent).
- `make check` (2): fails immediately because Ruff is not installed (`Error 127`); no implementation changes to check.
- `git config user.name` (0); `git config user.email` (0); `git status --short` (0); `test -f docs/plans/PROMOTION_PLAN_0.9.3.md` (1).

Only this blocker report is to be committed and pushed to lane/W-pdb-1.

Final delivery commands: `git diff --check` (0),
`git add docs/plans/lane_status/W-pdb-1.md` (0),
`git commit -s -m "W-pdb-1: record missing promotion plan blocker"` (0),
`git push origin HEAD:refs/heads/lane/W-pdb-1` (0).

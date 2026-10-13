# W-pdb-1: blocked before implementation

Scope: 0.9.4 only. No code, tests, release files, regression data or golden
corpus changed.

The missing-plan blocker is resolved. Read the approved plan from
`origin/plan/promotion-093` into `/tmp/PROMOTION_PLAN.md`; it is not merged or
committed. Decision 13 and section 4.11(a) confirm the original reference-only
scope. `git merge origin/main` reports already up to date.

Current question for the lead: this checkout has no `uv.lock`, so the required
`uv sync --frozen --group dev` cannot run. Should the lockfile come from another
approved branch, or should I use the repository's documented development setup?
No lockfile was generated and no dependency policy was changed.

No acceptance tests were written or run yet: tool setup must precede checks per
the follow-up. No red evidence is claimed. Implementation remains pending.

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


## Follow-up command record

- `git fetch origin` (0), with network permission; no retries needed.
- `git fetch origin plan/promotion-093` (0); subsequent
  `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md`
  (128): narrow fetch updated FETCH_HEAD only; dependent commands did not run.
- `git fetch origin refs/heads/plan/promotion-093:refs/remotes/origin/plan/promotion-093`
  (0); `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md`
  (0); `git merge origin/main` (0); `cat /tmp/PROMOTION_PLAN.md` (0).
- `pip install uv` (0, already installed); `uv sync --frozen --group dev`
  (2): default cache directory is read-only.
- `UV_CACHE_DIR=/tmp/shape-uv-cache uv sync --frozen --group dev` (1):
  required `uv.lock` absent. Activation and checks not run.
- Read-only inspections: client-secret/credrefs search in SQL Server and Fabric
  plugins (0); `cat src/shape/security/credrefs.py` (0); SQL Server file inventory
  (0); plan section read (0); command source read (0); attempted
  `cat plugins/shape-sqlserver/tests/test_command.py` (1, absent); publish resolver
  read (0); plugin CLI source read (0); docs/example client-secret search (0);
  changelog header read (0).
- Python update of this status file (0).
- `git diff --check` (0); `git add docs/plans/lane_status/W-pdb-1.md` (0);
  `git commit -s -m "W-pdb-1: record frozen environment setup blocker"` (0);
  `git push origin HEAD:refs/heads/lane/W-pdb-1` (0).

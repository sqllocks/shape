# Command ledger

Initial read-only setup (before ledger runner): attachment download 0 after workspace-ready; cloud skill/status/network reads 0. Repository required-document reads 0 (one combined output truncated, reread relevant sections). `git ls-remote origin refs/heads/main` 0; clean working tree. Initial zombie count 0 (grep count exit 1 means no matches). Issue #20/comments/artifacts API each 0. `git fetch origin` 0; scratch branch creation 0. Artifact download exit 1 (proxy CONNECT 403); subsequent missing-directory rg exit 2. API user/jobs and scratch branch push each 0. Diagnostic gh artifact API exit 1; urllib redirected download exit 1 (proxy CONNECT 403). No artifact was downloaded and no values are claimed. Read assigned eight issues exit 0 each; issue #10 comments exit 0. Wrapper creation exit 0.

Sub-agent command ledgers live in their lane records/worktrees; their commands are not implied to pass by this summary.

- `git add docs/plans/lane_status/public-b-2026-10-18 && git commit -s -m "Public B: record lane assignments and artifact access blocker" && git push origin HEAD` — exit 0.

- `gh issue comment 20 --body-file docs/plans/lane_status/public-b-2026-10-18/questions.md` — exit 0.

- `rg -n "class .*Source|read_tables|sample_rows" plugins/shape-databases/src/shape_databases; gh api repos/sqllocks/shape/issues/13/comments --jq ".[ ].body"; python -c "import importlib.util; print(importlib.util.find_spec(\"sempy\"))"` — exit 0.

- `gh api --method PATCH repos/sqllocks/shape/issues/comments/6252264567 -F body=@docs/plans/lane_status/public-b-2026-10-18/questions.md --jq .html_url` — exit 0.

- `rg --files plugins/shape-databases/src/shape_databases; rg -n "TOM|connect_semantic|roles|sempy" plugins/shape-fabric/src/shape_fabric/semantic_source.py plugins/shape-fabric/src/shape_fabric/semantic_model.py; gh api repos/sqllocks/shape/issues/13/comments --jq ".[].body"` — exit 0.

- `gh issue comment 14 --body-file /tmp/public-b-14-comment.md` — exit 0.

- `git add docs/plans/lane_status/public-b-2026-10-18 && git commit -s -m "Public B: park BigQuery on source dependency"` — exit 0.

- `git push origin HEAD` — exit 0.

- `git worktree add -b lane/W9-12 /workspace/shape-w9-12 origin/main` — exit 0.

- `PATH=/tmp/w9-10-bin:$PATH uv run --no-project --with semantic-link-sempy python -c "import importlib.metadata; print(importlib.metadata.version(\"semantic-link-sempy\")); import sempy.fabric as f; import inspect; print([(n,str(inspect.signature(getattr(f,n)))) for n in dir(f) if \"tom\" in n.lower()])"` — exit 0.

- `PATH=/tmp/w9-10-bin:$PATH uv run --no-project --with semantic-link-sempy python -c "import sempy.fabric as f,inspect; print(inspect.signature(f.connect_semantic_model)); print(inspect.signature(f.evaluate_dax)); print(inspect.signature(f.list_measures))"` — exit 0.

- `git ls-remote --heads origin lane/W9-05 lane/W9-08 lane/W9-09 lane/W9-10 lane/W9-11 lane/W9-12 lane/W9-13 lane/W9-16 lane/FRZ-01-finish` — exit 0.

- `gh pr list --state open --json number,headRefName,title,url --jq ".[] | select(.headRefName|test(\"^lane/(W9-(05|08|09|10|11|12|13|16)|FRZ-01-finish)$\"))"` — exit 0.

- `git fetch origin refs/heads/lane/W9-12:refs/remotes/origin/lane/W9-12 refs/heads/lane/W9-13:refs/remotes/origin/lane/W9-13` — exit 0.

- `git show origin/lane/W9-12:docs/plans/lane_status/W9-12.md` — exit 0.

- `git show origin/lane/W9-13:docs/plans/lane_status/W9-13.md` — exit 0.

- `git diff --check` — exit 0.

- `git status --short` — exit 0.

- `PATH=/tmp/w9-10-bin:$PATH make bootstrap` — exit 0.

- `git ls-remote origin refs/heads/main` — exit 0.

- `gh run download 39110102198 -n parity-profile -n parity-generate-1 -D /tmp/public-b-nightly` — exit 1.

- `PATH=/tmp/w9-10-bin:$PATH make bootstrap` — exit 0.

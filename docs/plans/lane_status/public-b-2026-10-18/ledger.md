# Command ledger

Initial read-only setup (before ledger runner): attachment download 0 after workspace-ready; cloud skill/status/network reads 0. Repository required-document reads 0 (one combined output truncated, reread relevant sections). `git ls-remote origin refs/heads/main` 0; clean working tree. Initial zombie count 0 (grep count exit 1 means no matches). Issue #20/comments/artifacts API each 0. `git fetch origin` 0; scratch branch creation 0. Artifact download exit 1 (proxy CONNECT 403); subsequent missing-directory rg exit 2. API user/jobs and scratch branch push each 0. Diagnostic gh artifact API exit 1; urllib redirected download exit 1 (proxy CONNECT 403). No artifact was downloaded and no values are claimed. Read assigned eight issues exit 0 each; issue #10 comments exit 0. Wrapper creation exit 0.

Sub-agent command ledgers live in their lane records/worktrees; their commands are not implied to pass by this summary.

- `git add docs/plans/lane_status/public-b-2026-10-18 && git commit -s -m "Public B: record lane assignments and artifact access blocker" && git push origin HEAD` — exit 0.

- `gh issue comment 20 --body-file docs/plans/lane_status/public-b-2026-10-18/questions.md` — exit 0.

- `rg -n "class .*Source|read_tables|sample_rows" plugins/shape-databases/src/shape_databases; gh api repos/sqllocks/shape/issues/13/comments --jq ".[ ].body"; python -c "import importlib.util; print(importlib.util.find_spec(\"sempy\"))"` — exit 0.

- `gh api --method PATCH repos/sqllocks/shape/issues/comments/6252264567 -F body=@docs/plans/lane_status/public-b-2026-10-18/questions.md --jq .html_url` — exit 0.

- `rg --files plugins/shape-databases/src/shape_databases; rg -n "TOM|connect_semantic|roles|sempy" plugins/shape-fabric/src/shape_fabric/semantic_source.py plugins/shape-fabric/src/shape_fabric/semantic_model.py; gh api repos/sqllocks/shape/issues/13/comments --jq ".[].body"` — exit 0.

- `gh issue comment 14 --body-file /tmp/public-b-14-comment.md` — exit 0.

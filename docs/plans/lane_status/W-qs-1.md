# W-qs-1 lane status

Status: complete; focused and documentation checks passed; full check bounded and incomplete. Base: origin/main at `a8e476ca910c40e3d8e7dd0dace9c80699237e82`.

## Exact question

The prompt requires deleting the `shape query` line in QUICKSTART example 2, its output and its manifest entry together. `tests/docs/reference_examples.json` records complete examples, not individual commands: `{ "page": "docs/QUICKSTART.md", "index": 1, "kind": "local" }` registers both `shape show` and `shape query`. Should we retain this entry for the remaining `shape show` example, or remove it and update the hardcoded 206-entry assertion in `test_restored_example_inventory`? The latter unregisters an example that remains runnable on the page. Dependent edits waited for the owner answer.

## Setup and inspection

Origin verified as `https://github.com/sqllocks/shape.git`. Created an independent clone under `/workspace/prompt-runs/checkouts/W-qs-1`, set the original origin and Git identity, fetched origin and the plan branch, and created `lane/W-qs-1` from origin/main. No AGENTS.md present. Read CLAUDE.md, CONTRIBUTING.md, plan Decisions and section 4.4; plan not copied into tracked files.

Python 3.11 is unavailable; used supported Python 3.12 with a private venv. Reused third-party dependency links from the initial checkout, excluding core/plugin/editable mappings. Rust uses the preinstalled onboarding toolchain and the reusable compiled target specified by the lead. Bootstrap passed (0). Reinstalling core with docs extras failed (1) because simultaneous maturin builds raced when staging the shared lib_kernel.so; bootstrap already installed the private editable core successfully. Installed required first-party plugins independently (0). Additional databases and integrations plugins provide the DuckDB tutorial sink/source. Pinned numpy 2.5.3 and pyarrow 25.0.1 (0), unlinking only private-venv symlinks beforehand so shared dependencies remain untouched. Copied tool wrappers with the private Python shebang; Ruff binary is read-only-linked.

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

## Owner decision and implementation

The owner directed retaining the `shape show` coverage and existing manifest entry while removing the query command/output. The manifest has only `page`, `index`, `kind`; it stores no command or output. The unchanged `{ "page": "docs/QUICKSTART.md", "index": 1, "kind": "local" }` now registers only `shape show customers.shape`, because the harness extracts commands and expected output from the edited page. No new manifest fields or inventory-count change is needed.

Removed the query line plus its unsigned-artifact note and result from example 2. Renamed the section to "Capture and compatibility examples" and removed its experimental label. Kept the explanatory Models link and page-level available label. No claim table or promotion release note added (these belong to their designated work packages).

This changes documentation only; no runtime behavior or new test added, so failing-test red evidence does not apply. Existing executable reference/inventory/tutorial tests verify the actual commands and exact output.

## Verification and further command exits

- First clarification status commit `006d507` and `git push -u origin lane/W-qs-1`: 0.
- Python documentation-edit helper: 0.
- Dependency/tool-wrapper helper and Ruff binary symlink: 0.
- Initial Ruff commands: 127 (tool executable not yet copied); reruns `ruff check src tests plugins benchmarks/vs_refengine scripts` and `ruff format --check src tests plugins benchmarks/vs_refengine scripts`: 0, 0.
- `mypy`: 0.
- Local plugin-only editable install: 0; numpy/pyarrow pin install: 0.
- `pytest -q 'tests/docs/test_reference_examples.py::test_reference_page[QUICKSTART]' tests/docs/test_reference_examples.py::test_restored_example_inventory tests/docs/test_launch_tutorials.py`: first run 1, 9 passed/1 failed (missing DuckDB sink); second run 1, 9 passed/1 failed (missing DuckDB source). QUICKSTART and inventory passed both times. Installed `plugins/shape-databases` (0), then `plugins/shape-integrations` (0) to satisfy tutorial dependencies. Final rerun: 0, **10 passed in 70.92s**, including QUICKSTART, manifest inventory and every launch tutorial.
- `NO_MKDOCS_2_WARNING=1 mkdocs build --strict`: 0.
- `python scripts/check_doc_links.py --site site`: 0, zero broken links.
- `python scripts/check_user_facing.py --site site`: 0, clean.
- `git diff --check`: 0. Python helper importing the harness BLOCK and selecting manifest index 1: 0; confirmed exactly `shape show customers.shape`, query absent.
- Diagnostic `tail`, `ls`, `ps` and successful `rg`/`sed` reads: 0; `tail` of not-yet-created pin log and `rg` nonexistent `plugins/shape-sources/pyproject.toml`: 1/2 respectively, harmless discovery failures.
- `shape plugins doctor`: 0, all present plugins load; `shape plugins list`: 0.

Open questions: none; owner resolved the manifest representation above.

## Focused test evidence

```text
..........                                                               [100%]
10 passed in 70.92s (0:01:10)
```

The two prior failures were setup gaps only: DuckDB sink and source plugin availability. Neither page nor test was altered to conceal them. Final check uses the pinned documentation versions and independently installed first-party plugins from this lane.

## Full check result and delivery

`timeout 300 make check`: **124**, terminated at the 300-second bound during core-stage pytest. Before that point, lint/format, mypy, compileall, Vulture, import-linter and every repository script in make check passed. Core pytest had begun reporting progress but produced no completed result. This is not a full-suite pass, coverage pass, or proof of any individual full-suite failure. The known sandbox full-suite limitations are documented by the lead; the explicit focused examples above completed successfully.

Final status/source edit helpers and `git diff --check`: 0. Final delivery uses `git add docs/QUICKSTART.md docs/plans/lane_status/W-qs-1.md`, a signed commit with the required `W-qs-1:` prefix, and `git push -u origin lane/W-qs-1`. No PR, merge, tag, other branch push or archived-engine wording added. The final commit/push outcome and remote SHA are reported to the lead after verification.

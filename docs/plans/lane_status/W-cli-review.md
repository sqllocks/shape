# W-cli review

Reviewer: independent; built none of this lane. Head reviewed: `e2cfe28b647ab36f5326376b167d40c6d45ce90b`
(expected prefix `e2cfe28`: matches). Base: the lane forks from `c10923b`, an ancestor of
`origin/main` (`a8e476c`, 36 commits ahead); `git diff origin/main...HEAD` was reviewed. The only
`main` commit touching the same files is the 0.9.3 version bump of the doctor transcript in
`docs/CLI.md`, and the lane merges with `main` (and with the other four lanes) without conflict.
Plan: section 3 ("Not in 0.9.3": `docs/CLI.md`) and 4.10.

## Verdict: REJECT

The split itself is good: every experimental command's documentation is on
`docs/CLI_EXPERIMENTAL.md` (`Status: experimental.`), `docs/CLI.md` names only commands in
`STABLE` (guarded by the new `tests/docs/test_cli_split.py`, whose red run is recorded), both
moved manifest entries point at the new page, the nav entry is added, the only incoming anchor link
(`CLI_STABILITY.md` -> `#cloud-and-fabric-options`) still resolves, `CLI.md` keeps
`Status: experimental.` (no promotion), and there is no CHANGELOG, STABLE or snapshot change and
no skip/xfail. I compared every `shape ...` command mention on the old page with the two new pages:
none is lost.

It is rejected on the one sentence the task asked to make true.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `git checkout -B review-W-cli origin/lane/W-cli` | 0 | head `e2cfe28` |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | no issues in 698 files |
| `pytest -q -p no:cov tests/docs/test_cli_split.py tests/docs/test_reference_examples.py::test_restored_example_inventory 'test_reference_page[CLI_EXPERIMENTAL]' 'test_reference_page[CLI]' tests/cli/test_ci_docs.py` | 1 | 10 passed, 1 failed: `[CLI]`, the Python 3.11 traceback diff that also fails on `origin/main` here (sandbox) |
| `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` | 0 | |
| `python scripts/check_doc_links.py --site SITE` | 0 | 0 broken links in sources and built site |
| `python scripts/check_user_facing.py --site SITE` | 0 | clean |
| `pytest -p no:cov tests/docs tests/docs_site -k "not test_reference_page and not test_tutorial"` | 1 | 111 passed, 1 failed: `test_brand.py` needs playwright (sandbox) |
| Walk of `_build_parser()` for leaf commands without `--json` | 0 | `version` only (a STABLE command) |
| `shape version --json` | 2 | `shape: error: unrecognized arguments: --json` |
| Walk of `_build_parser()` for leaf commands with `-o/--output/--out` but no `--dry-run` | 0 | none |
| `git diff --check origin/main...HEAD` | 2 | trailing whitespace in the lane status file only (finding 2) |

## Findings

Blocking:

1. `docs/CLI.md:47`: "Core commands support `--json`" is still a universal claim, and it is false
   for a stable command documented by this page's own scope: `shape version` has no `--json`
   (`shape version --json` exits 2, "unrecognized arguments"). The task was to replace the
   "on every command" sentence with the truth, on the page meant for promotion. Fix: say which
   commands take it, for example "Every core command except `shape version` takes `--json`", and
   add a test to `tests/docs/test_cli_split.py` (or a CLI test) that walks `_build_parser()` and
   asserts the set of leaf commands without `--json` equals the exceptions the page names, so the
   sentence cannot drift again.

Non-blocking:

2. `docs/plans/lane_status/W-cli.md:38-60`: trailing whitespace in the pasted pytest output makes
   `git diff --check` exit 2.
3. `docs/CLI.md:53`: "Core writing commands expose `--dry-run` where their parser supports it" is
   true but says nothing. The parser walk shows every core leaf command with `-o/--output/--out`
   has `--dry-run`; state that (and pin it in the same parser test) if it is meant as a promise.
   The old text's `format: "shape-dry-run"` detail with `--json` was dropped; restore it if it is
   still true.
4. `docs/CLI.md:50` and `:56` drop `design` from the "`--json` takes a file" list and `fidelity`
   from the `--junit`/`--sarif` list. Both are experimental and documented in `docs/DESIGN.md` and
   `docs/CI.md:28`, so nothing is lost overall, but `CLI_EXPERIMENTAL.md` could carry the two
   exceptions so the split pages stay complete.
5. `docs/CONTRACT_EMIT.md:57-58` still says "the `shape-result` document every command prints with
   `--json` (`docs/CLI.md`)". Out of this lane's files, but the same false universal; worth fixing
   with finding 1.
6. The lane forks from `c10923b`; merge `main` before landing so the 0.9.3 doctor transcript and any
   later docs changes are on the branch CI tests.

## Environment

Fresh reviewer, Python 3.11.x venv, `make bootstrap` (exit 0), then
`pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka`
(exit 0), and `pip install -e .` again after each lane switch (exit 0). `plugins/shape-databases`
and `plugins/shape-integrations` were added (exit 0) because launch tutorial 06 needs the DuckDB
sink and source. Pins from `requirements-docs.txt` (numpy 2.5.3) are not on this sandbox's index
(exit 1), so numpy 2.4.6 / pyarrow 26.0.0 were used. All five lanes were also merged together onto
`origin/main` locally (no conflicts) and the scope, inventory, CLI_EXPERIMENTAL, MIGRATION and
SIGNING tests plus both link and wording checks passed on the merge (8 passed; 0; 0).

Sandbox artefacts, not attributed to any lane: `test_reference_page[CLI]` fails identically on
`origin/main` here (the Python 3.11 traceback names `zipfile.py` where the 3.12 transcript says
`__init__.py`); `tests/docs/test_brand.py::test_branded_site` needs playwright.

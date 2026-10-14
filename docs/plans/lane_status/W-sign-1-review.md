# W-sign-1 review

Reviewer: independent; built none of this lane. Head reviewed: `111586f7275d3675a19c1da9b214bd481b4b75bf`
(expected prefix `111586f`: matches). Base: `origin/main` (`a8e476c`). Plan: section 4.5 (SIGNING).

## Verdict: APPROVE

The page now reads as section 4.5 states:

- "Signed artifacts and migration" and example 4 (`shape migrate ... --verify old.pub --sign-key
  release.key`, transcript byte-identical) moved to a new experimental `docs/MIGRATION.md`; the
  manifest entry moved from `docs/SIGNING.md` index 4 to `docs/MIGRATION.md` index 4 (inventory
  count unchanged); `scripts/docs_example_setup.py` now prepares `old.shape` and the `release` key
  for `MIGRATION` instead of `SIGNING`. SIGNING's remaining examples use neither file.
- The vault paragraph moved verbatim to `docs/VAULT.md` ("Signed profiles"); SIGNING links both pages.
- `query` dropped from the `--verify` list and from the example comment.
- `kv://` reads "a secret store, through a resolver the host registers" (plan wording).
- New nav entry in `mkdocs.yml`; CHANGELOG line only under `## Unreleased`.
- No claim table (W-sign-2), no Python API stance (W-sign-3), no STABLE/snapshot change, no
  skip/xfail, no golden data touched. The one new sentence in MIGRATION ("migration reports a
  no-op and writes no artifact or receipt") matches the transcript (`"noop": true`,
  `"receipt": null`, `"written": false`).

Red evidence: the new `tests/docs/test_signing_page_scope.py` failed (2 failed) before the edit,
recorded in the status file, and passes now.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `git checkout -B review-W-sign-1 origin/lane/W-sign-1` | 0 | head `111586f` |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | no issues in 701 files |
| first pytest run including `test_reference_page[VAULT]` | 4 | that id does not exist (VAULT has no runnable examples); rerun without it |
| `pytest -q -p no:cov tests/docs/test_signing_page_scope.py tests/docs/test_reference_examples.py::test_restored_example_inventory 'test_reference_page[SIGNING]' 'test_reference_page[MIGRATION]' 'test_reference_page[CLI]' 'test_reference_page[PRIVACY_MODEL]' tests/artifact/test_signing.py tests/artifact/test_signing_keys.py tests/cli/test_bugs_cli_cat_verify.py tests/vault` | 1 | 221 passed, 1 failed: `[CLI]`, which fails the same way on `origin/main` here (sandbox, see Environment) |
| `pytest -q -p no:cov 'test_reference_page[CLI]' 'test_reference_page[SIGNING]'` on `origin/main` | 1 | CLI fails identically on main; SIGNING passes |
| `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` | 0 | |
| `python scripts/check_doc_links.py --site SITE` | 0 | 0 broken links in sources and built site |
| `python scripts/check_user_facing.py` | 0 | clean |
| `pytest -p no:cov tests/docs tests/docs_site -k "not test_reference_page and not test_tutorial"` | 1 | 112 passed, 1 failed: `test_brand.py` needs playwright (sandbox) |
| `git diff --check origin/main...HEAD` | 2 | trailing whitespace in the lane status file only (finding 1) |

## Findings

Blocking: none.

Non-blocking:

1. `docs/plans/lane_status/W-sign-1.md:50-75`: trailing whitespace in the pasted pytest output makes
   `git diff --check` exit 2. The status file says `git diff --check` was 0; that was before the
   file was written. Strip it when the lane is next touched.
2. `docs/MIGRATION.md:47-49`: the page's only example is headed "Example 1" but carries
   `<!-- example: 4 -->` and anchor `local-example-4`, kept so the manifest index did not change.
   Harmless; renumbering to 0 with the manifest entry would read more naturally.
3. `tests/docs/test_signing_page_scope.py` pins exact prose strings; it guards the move but will need
   editing whenever the wording changes. Acceptable for a scope guard.

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

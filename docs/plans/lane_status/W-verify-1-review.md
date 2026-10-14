# W-verify-1 review

Reviewer: independent; built none of this lane. Head reviewed: `fa1122e785ed77c3c0ad8dddfd98432ba54be8a6`
(expected prefix `fa1122e`: matches). Base: `origin/main` (`a8e476c`). Plan: section 4.6 (VERIFY).

## Verdict: APPROVE

- "Scores by dimension and failing rows" left `docs/VERIFY.md` and is now in experimental
  `docs/SCORECARD.md:34`, with its self-link removed and "these gates" made "the verification gates".
- The `privacy` row of the config table (`docs/VERIFY.md:84`) no longer names `report-card` or
  describes the membership-inference test. Its new text is supported by the code:
  `src/shape/quality/verifyconfig.py:236-247` validates `privacy` (an object; keys
  `max_membership_auc` 0 to 1, `seed` whole number) and no verify gate reads it. The keys stay
  documented in `docs/REPORT_CARD.md` ("privacy").
- No incoming links to the removed anchor; no runnable example or manifest entry touched; no claim
  table (W-verify-2), no Python API marker (W-verify-3), no CHANGELOG, STABLE or snapshot change,
  no skip/xfail. The second commit only edits the lane status file.

Red evidence is not applicable: documentation move and rewording only.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `git checkout -B review-W-verify-1 origin/lane/W-verify-1` | 0 | head `fa1122e` |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | no issues in 701 files |
| `pytest -q -p no:cov 'tests/docs/test_reference_examples.py::test_reference_page[VERIFY]' 'tests/docs/test_reference_examples.py::test_reference_page[SCORECARD]' tests/docs/test_reference_examples.py::test_restored_example_inventory tests/quality/test_verify_config.py` | 0 | 21 passed |
| `python scripts/check_doc_links.py` | 0 | 0 broken links |
| `python scripts/check_user_facing.py` | 0 | clean |
| `git diff --check origin/main...HEAD` | 0 | |
| `grep -rn '"privacy"' tests` | 0 | hits only report-card, scorecard and bridge tests |

## Findings

Blocking: none.

Non-blocking:

1. `docs/VERIFY.md:84`: "accepted and validated ... ignored by `shape verify`" has no test in
   `tests/quality/test_verify_config.py` or `test_verify.py`; only report-card tests use `privacy`.
   The claim predates this lane, but W-verify-2 must add or cite a test (a config with `privacy`
   that `shape verify` accepts, a bad `max_membership_auc` that exits 2, and no `privacy` gate in
   the report) before the page is labelled.
2. `docs/SCORECARD.md:34-39`: the moved section largely repeats the page's opening paragraph
   (`docs/SCORECARD.md:22-24`); consider folding the dimension list and `--slice-by` sentence into
   the opening when SCORECARD is next edited.
3. VERIFY.md now has no pointer to SCORECARD.md at all; a "Related" link would help readers who
   arrive looking for per-dimension scores. Optional.

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

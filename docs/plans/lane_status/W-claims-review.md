# W-claims review

Verdict: **REJECT** (2 blocking findings: two `done` rows claim more than their tests prove).

Head reviewed: `de4da9c720d24dcf1dc41061e8f647ca9acdec89` (expected prefix `de4da9c`: matches).
Base: `origin/lane/W0-available` (`83fd504`). It is an ancestor of this head. Diff reviewed:
`git diff origin/lane/W0-available...HEAD`, which touches only
`docs/plans/lane_status/W-claims.md` and `docs/plans/lane_status/W-claims-drafts.md`. Plan sections
read: 1.3, 4.1, 4.4, 4.5, 4.6 of `origin/plan/promotion-093`; CLAUDE.md, CONTRIBUTING.md,
docs/AVAILABLE.md, the six pages. lane/W0-available's own open REJECT is not this lane's and is
not judged here.

## Scope judgement

- It follows the lead's governing answer: the six tables are drafts in
  `docs/plans/lane_status/W-claims-drafts.md`, in the AVAILABLE.md format. The checker,
  AVAILABLE.md, the six pages, CHANGELOG and `src` are unchanged
  (`git diff --exit-code origin/lane/W0-available HEAD -- docs/AVAILABLE.md scripts/check_available.py docs/{CANARIES,REPRODUCIBILITY,DETERMINISM,QUICKSTART,SIGNING,VERIFY}.md CHANGELOG.md src`
  exit 0). No page is marked available, nothing is promoted, and no test, fixture, gate or tolerance
  changed. Each page lists its uncovered sentences separately.
- Format: the six draft sections, fed to the unchanged `check_available.parse`, give no parse
  errors and 195 rows: CANARIES 20, REPRODUCIBILITY 37, DETERMINISM 2, QUICKSTART 7, SIGNING 36,
  VERIFY 93. All are `done`.
- Ran-and-passed claims: I ran every one of the 198 distinct cited node ids, not a sample.
  All pass with no skips (370 parametrized cases). The "Ran and passed?" columns are accurate.
- Claim accuracy: I read 40 cited test bodies against their claim sentences, covering all 6
  pages (DETERMINISM completely; the QUICKSTART rows are whole-tutorial and page transcript runs,
  which I checked as runs). Most are proven as written. The two below are
  not.

## Commands (this sandbox, Python 3.11 venv)

| Command | Exit |
|---|---|
| `make bootstrap`; `pip install -e '.[docs]' -e plugins/shape-{domains,sqlserver,eventhubs,fabric,kafka}`; `pip install -e .` on this lane | 0 |
| `pip install 'scikit-learn>=1.5.1,<2' -e plugins/shape-databases -e plugins/shape-integrations` (utility tests; tutorial 06 prerequisites) | 0 |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` (2141 files) | 0 |
| `mypy` (698 files) | 0 |
| `python scripts/check_available.py` (`AVAILABLE: 0 problem(s)`) | 0 |
| `pytest -p no:cov -q -rs <all 198 cited node ids>`, including `test_reference_page[CANARIES/REPRODUCIBILITY/QUICKSTART/SIGNING/VERIFY]` and all `test_tutorial[...]` cases (370 passed, 0 skipped) | 0 |
| draft-table parse with `check_available.parse` (0 errors, 195 rows) | 0 |

Not run here: `make check` (CI is the full-suite proof).

## Findings

1. **Blocking. `docs/plans/lane_status/W-claims-drafts.md:186` (AV-DETERMINISM-01) claims
   more than its test proves.** The claim says a row is identified "independently of
   partition boundaries **or execution order**". The cited
   `tests/generation/test_random_access.py::test_random_access_partition_determinism` compares
   `rows(1000)` with three contiguous `rows_at` ranges read in ascending order, so it proves
   partition boundaries only. No order is reversed or shuffled. Fix: cut the row's claim to
   partition boundaries and list "or execution order" (for stateless strategies) under
   "Sentences without complete proving tests" for W-det-2. Alternatively, cite a test that really
   requests stateless rows out of order, if one exists. Do not write a new test in this lane.
2. **Blocking. `docs/plans/lane_status/W-claims-drafts.md:101` (AV-REPRODUCIBILITY-23) claims
   more than its test proves.** The page says the id is taken "over every generated table after
   chaos was applied, not only the files that were written" (REPRODUCIBILITY.md:54-55). The
   cited `tests/scenario/test_reproducibility.py::test_a_run_with_chaos_replays_to_the_same_id`
   only shows that a chaos run replays to the same id (exit 0). Chaos is deterministic, so an id
   taken before chaos would also replay and the test would still pass. It does not show the id
   covers post-chaos tables. Fix: change the row to what the test proves ("A run with chaos
   replays to the same dataset id") and move the post-chaos and not-only-written-files sentence
   to the uncovered list.
3. Non-blocking. `:90` (AV-REPRODUCIBILITY-12) "Both kernels produce the same dataset id" cites a
   test that compares `SHAPE_KERNEL=python` with `auto`. That proves the claim only where the
   native kernel is built, as it is in CI and was here. Also citing
   `tests/generation/test_w1_15_pinned_fixtures.py::test_the_kernels_agree_on_every_id`, which
   names `rust` explicitly, would make the row stronger.
4. Non-blocking. `:288` (AV-SIGNING-33) "Keygen creates private key files with POSIX mode 0600"
   cites `test_key_files`, which calls `write_keypair(..., unencrypted=True)`, not the default
   keygen path. `test_keygen_writes_an_encrypted_pkcs8_key_by_default` (already cited by
   AV-SIGNING-18) also asserts 0600 for the default encrypted key. Cite both.
5. Non-blocking. DETERMINISM uses none of the W-det-1-named `tests/scenario/test_reproducibility.py`
   tests. That is acceptable because the page's remaining sentences are listed as uncovered,
   but say so in the section.
6. Non-blocking. `docs/plans/lane_status/W-claims.md:36-38` attributes the governing
   resolution to "the Owner". The answer that governs this lane was the lead's. Correct the
   attribution so a later reader does not take it for an Owner decision.

After the fix, re-check the remaining rows the same way: a `done` row must say no more than its
cited assertions show.

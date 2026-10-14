# W-pub-1 review

Verdict: **REJECT** (1 blocking finding; a one-line test update).

Head reviewed: `e3d3ff8b3b04636810669e2bad5f5314e01d67fb` (expected prefix `e3d3ff8`: matches).
Base: `origin/main`. Diff reviewed: `git diff origin/main...HEAD`
(docs/plugins/fabric-commands.md, tests/docs/fabric_examples.json,
tests/docs/reference_examples.json, tests/docs/test_fabric_transcripts.py, lane status).
Plan section read: 4.11, row `publish`, note (c), and 4.9 of `origin/plan/promotion-093`;
CLAUDE.md, CONTRIBUTING.md, docs/plans/lane_status/W-pub-1.md.

## Scope judgement

- Example 0 is split into lakehouse (0), sql-database (8), warehouse (9) and eventhouse (10).
  Each has one command, its own `needs`/`env`/`rewrite`, and its own reference-manifest row.
  The four output blocks together are byte for byte the stdout of the existing receipt; the new
  test asserts this. No transcript was re-recorded, nothing live was run, and evidence files are
  unchanged. Nothing from W-pub-2 or W-pub-3 is included. There is no promotion, STABLE entry,
  surface snapshot write or CHANGELOG change.
- Re-runnable flags (note (c)): `--write-mode replace` on the three database targets is a real
  choice (`plugins/shape-fabric/src/shape_fabric/commands.py:636-641`) and is passed to each sink
  (`:825,835,843`). It does not change the printed output (`_run_publish`/`_publish_database`
  print nothing mode-specific), so the recorded segments are still the expected output. Lakehouse
  has no flag because `--write-mode` applies only to database and eventhouse targets. That is
  correct.
- Red evidence: the new test failed on the old manifest (`{'publish-targets'}`), as the lane
  status records. That is genuine.

## Commands (this sandbox, Python 3.11 venv)

| Command | Exit |
|---|---|
| `make bootstrap`; `pip install -e '.[docs]' -e plugins/shape-{domains,sqlserver,eventhubs,fabric,kafka}` | 0 |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` (2150 files) | 0 |
| `mypy` (701 files) | 0 |
| `pytest -q -p no:cov tests/docs/test_fabric_transcripts.py tests/docs/test_owner_transcripts.py tests/docs/test_transcripts.py 'tests/docs/test_reference_examples.py::test_reference_page[fabric-commands]'` (28 passed) | 0 |
| `pytest -q -p no:cov tests/docs/test_reference_examples.py -k "not test_reference_page"` | **1** (`test_restored_example_inventory` failed) |
| same node on `origin/main` | 0 |
| `python scripts/check_doc_links.py` | 0 |
| `python scripts/check_user_facing.py` | 0 |

Not run here: mkdocs strict build and `make check` (CI is the full-suite proof).

## Findings

1. **Blocking. `tests/docs/test_reference_examples.py:131`
   (`test_restored_example_inventory`) fails: `assert len(items) == 206` sees 209.** This lane
   adds three rows to `tests/docs/reference_examples.json`, so the pinned inventory count
   moves by three. The test passes on `origin/main` and fails on this head. The task said that a
   test expecting the old single example must be updated. Fix: change `206` to `209` in that
   test, and run the whole `tests/docs/test_reference_examples.py` module (non-page tests at
   least), not only the `fabric-commands` page case. The lane's local runs missed it because
   they selected only `test_reference_page[fabric-commands]`.
2. Non-blocking, for the lead. Plan 4.9 still gives the live dispatch as
   `examples=setup-fabric,deploy-notebook,publish-targets`. `scripts/docs_fabric_transcripts.py:158`
   skips ids it does not know without an error, so that dispatch would now run no publish
   example at all and still look clean. When the Owner re-records, use
   `publish-lakehouse,publish-sql-database,publish-warehouse,publish-eventhouse`, and update
   the plan's row. The plan branch is not this lane's to edit.
3. Non-blocking. `docs/plugins/fabric-commands.md:106,155,204` put a maintainer to-do on a user
   page ("The Owner must re-record this command on the release head"). The disclosure that the
   shown output came from a run without `--write-mode replace` is honest and should stay. Track
   the to-do in the plan or lane status rather than in reader-facing text, or word it for
   readers (for example, "Recorded before `--write-mode replace` was added").
4. Non-blocking. The split dropped the old sentence that explains the angle-bracket values
   (`<workspace-id>`, `<lakehouse-id>`, `<query-uri-host>`) in these four outputs. Examples 1 and 2
   still carry it (`:275,319`). Consider restoring one such sentence above the four target
   sections.
5. Non-blocking, a known limit. `scripts/docs_fabric_transcripts.py:26-31` (`BLOCK`) only
   matches an *unresolved* example: the intro line followed directly by the command and an
   `<!-- owner: -->` note. The four resolved blocks, with their extra paragraph, cannot be
   re-recorded by the script until they are put back in that form. Example 0 on main had the
   same limit, so this is not a regression, but the re-record step will have to do it.

# W-qs-1 review

Reviewer: independent; built none of this lane. Head reviewed: `5c0690000b470e94ef2f6e4f909aa08fcf484a26`
(expected prefix `5c06900`: matches). Base: `origin/main` (`a8e476c`). Plan: section 4.4 (QUICKSTART).

## Verdict: APPROVE

The diff does the task and nothing else: in `docs/QUICKSTART.md` example 2 loses the
`shape query` line, its unsigned-artifact note and its `{"result": 30.5}` output in one commit
(`5c06900`); the section "Models examples" is renamed "Capture and compatibility examples" and its
`Status: experimental.` line is gone. The manifest entry `{"page": "docs/QUICKSTART.md", "index": 1}`
is kept unchanged, per the lead's answer: the manifest stores no commands, so the harness now runs
only `shape show customers.shape` for that entry, and `shape show` coverage is not reduced. Every
command left in the section (`capture`, `show`, `compatibility`) is in `STABLE`. No claim table, no
CHANGELOG, no STABLE or surface-snapshot change, no skip/xfail, no test or golden data touched.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `git checkout -B review-W-qs-1 origin/lane/W-qs-1` | 0 | head `5c06900` |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | no issues in 701 files |
| `pytest -q -p no:cov 'tests/docs/test_reference_examples.py::test_reference_page[QUICKSTART]' tests/docs/test_reference_examples.py::test_restored_example_inventory tests/docs/test_launch_tutorials.py` | 1 | 9 passed, 1 failed: tutorial 06 needs the DuckDB sink plugin (setup) |
| `pip install -e plugins/shape-databases -e plugins/shape-integrations` | 0 | |
| `pytest -q -p no:cov tests/docs/test_launch_tutorials.py` | 0 | 8 passed |
| `python scripts/check_doc_links.py` | 0 | 0 broken links |
| `python scripts/check_user_facing.py` | 0 | clean |
| `git diff --check origin/main...HEAD` | 0 | |
| `grep -rn models-examples docs mkdocs.yml README.md` | 1 | no incoming links to the renamed anchor |

## Findings

Blocking: none.

Non-blocking:

1. `docs/QUICKSTART.md:48`: the unlabelled section now says its commands are "described in
   [Models](MODELS.md)", an experimental page. The commands are stable, so this is not wrong, but
   W-qs-3 (claim table) should decide whether the pointer should be the stable command guide
   instead, so the available page does not lean on an experimental page for its claims.
2. The status file records Python 3.12 rather than 3.11; CONTRIBUTING allows 3.11 or newer.

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

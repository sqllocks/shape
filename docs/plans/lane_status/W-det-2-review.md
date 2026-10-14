# W-det-2 review

Reviewer: independent; built none of this lane. Head reviewed: `d5768dd20773e5333e9854131b85f7ca638bb48a`
(expected prefix `d5768dd`: matches). Base: `origin/main` (`a8e476c`). Plan: section 4.4 (DETERMINISM).

## Verdict: APPROVE

- Scan-cost sentence: cut. The builder's reason holds. In
  `src/shape/generation/strategies.py:146-153` the scan for a parent starts at the cached `done`
  offset, so after row `j > i` has been answered, the first answer for row `i` scans nothing. "A scan
  of rows `0..i` the first time a plan answers for row `i`" is therefore false in that order, and no
  exact test of it as written is possible. Cutting is the right call; the order-independence claim
  that remains is untouched.
- Idempotent-sink sentence: cut, as the task allows; core cannot test a guarantee of an arbitrary
  external sink.
- Streaming paragraph: its remaining sentence moved verbatim to `docs/EMIT.md:173`, under
  "Delivery, backpressure and checkpoints", where at-least-once delivery and replay are already
  described.
- The `GENERATION_ENGINE.md` link (W-det-3) and the page status are unchanged. No claim table, no
  CHANGELOG, no test or gate change, no skip/xfail.

Red evidence is not applicable: no behaviour changed and no test was added.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `git checkout -B review-W-det-2 origin/lane/W-det-2` | 0 | head `d5768dd` |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | no issues in 701 files |
| first pytest run including `test_reference_page[DETERMINISM]` | 4 | that id does not exist (DETERMINISM has no runnable examples); rerun without it |
| `pytest -q -p no:cov tests/generation/test_first_per_parent_g2.py tests/generation/test_random_access.py tests/scenario/test_reproducibility.py tests/streaming/test_failure_replay.py tests/streaming/test_checkpoint_store.py tests/streaming/emit/test_runtime.py 'tests/docs/test_reference_examples.py::test_reference_page[EMIT]' -m 'not emulator and not live and not heavy'` | 0 | 76 passed |
| `python scripts/check_doc_links.py` | 0 | 0 broken links |
| `python scripts/check_user_facing.py` | 0 | clean |
| `git diff --check origin/main...HEAD` | 0 | |

## Findings

Blocking: none.

Non-blocking:

1. `docs/EMIT.md:173`: "deterministic handlers/sources" uses terms the EMIT page does not define
   (it talks about the generator, the sink and the checkpoint fingerprint). W-det-1 or the EMIT
   claim work could reword it in the page's own terms, e.g. "the same command resumes the same
   sequence from a checkpoint", and cite `tests/streaming/test_failure_replay.py`.
2. The task preferred a test where cheap; the status file records why neither sentence could be
   tested as written, which I confirmed for the scan-cost sentence above.

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

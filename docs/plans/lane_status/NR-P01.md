# Eventstream ingestion guidance

Status: documentation and four offline regression tests complete; hosted CI is
the merge gate. No live Fabric work was performed.

## Changes and review

- `integrations/fabric/RUNBOOK.md`: added subsection 1.2 only, covering custom
  endpoint setup, activation before sending, early destination-delivery loss,
  verification, manual Spark Notebook destination setup after Git deployment,
  and the existing Python Eventstream/Eventhouse interfaces.
- `tests/docs/test_fabric_eventstream_guidance.py`: exactly four offline tests.
- `docs/plans/lane_status/NR-P01.md`: this record.
- Guidance/test commit: `d1ca36574eda3cd599a322ec68115d1c1de8acb4`.
- One independent review by a smaller model checked every platform statement
  against retrieved Learn text and every Python claim against the emitter/writer
  implementations. Result: no actionable factual issues.

## Sources

Retrieved Microsoft Learn on 2026-11-14. The former Eventstream Git integration
URL returned 404; the current page is `event-streams/eventstream-cicd`. The custom
source page requires publishing. The Eventhouse destination page explains a
publish-time ingestion/routing race and delayed activation with Custom time for
earlier events. Accordingly the warning concerns destination delivery rather than
asserting permanent endpoint loss. All four consulted Learn links and their
retrieval date appear in the subsection.

## Validation recorded before opening the pull request

| Command | Result |
|---|---|
| `make bootstrap` | Exit 0; locked workspace synchronized. |
| `uv run --locked pytest -q tests/docs/test_fabric_eventstream_guidance.py` before adding the subsection | Exit 1; `4 failed in 0.61s`, each because the subsection was absent. |
| Same command after adding the subsection | Exit 0; `4 passed in 0.18s`. |
| `uv run --locked pytest -q tests/docs/test_fabric*.py` | Exit 0; `57 passed in 10.36s`. |
| `uv run --locked ruff check tests/docs/test_fabric_eventstream_guidance.py` | Exit 0; `All checks passed!` |
| `uv run --locked ruff format --check tests/docs/test_fabric_eventstream_guidance.py` | Exit 0; `1 file already formatted`. |
| `uv run --locked --group docs python scripts/check_user_facing.py` | Exit 0; `check_user_facing: clean`. |
| `uv run --locked --group docs mkdocs build --strict` | Exit 0; documentation built. |
| `uv run --locked --group docs python scripts/check_doc_links.py --site site` | Exit 0; `0 broken link(s)`. |
| `uv run --locked --group docs python scripts/check_user_facing.py --site site` | Exit 0; `check_user_facing: clean`. |
| `make check` | Started; repository Ruff lint/format, mypy, compile, Vulture, import contracts and static repository checks passed; broad tests still running at this record. |
| `SHAPE_KERNEL=python uv run --locked pytest -m "not emulator and not live"` | Exit 2 during collection: `ModuleNotFoundError: No module named 'nbformat'` under `tests/demo/fabric`; `23 deselected, 1 error`. |
| Same Python-mode command with `--ignore=tests/demo/fabric --ignore=tests/demo/content` (the demo exclusions used by `make check`) | Started; broad tests still running at this record. |

The full-suite collection limitation is recorded, not treated as a pass. Hosted
checks and subsequent command results belong in the pull request's check results
and follow-up validation comment. No gates or dependencies were changed.

No tenant connection, activation, ingestion, destination creation, notebook
execution, credential use, or live verification was performed for this change.
Offline documentation assertions cannot establish Fabric runtime behavior.

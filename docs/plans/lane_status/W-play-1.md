# W-play-1 — Playground links

Status: complete for 0.9.4; ready for integration.

Branch: `lane/W-play-1`, based on `origin/main` at
`a8e476ca910c40e3d8e7dd0dace9c80699237e82`.

README, Quickstart and the existing getting-started area on the docs home page
link to the Shape Playground. Each describes a recorded Shape 0.9.3 session in
the browser. The Playground remains externally maintained; no copy was added.
The requested Unreleased changelog line is included.

The contributor workflow on this main uses pip, a virtual environment and Make;
W-uv-1 is not present. Checks used the existing `.venv` with
`. .venv/bin/activate`. No dependencies or workflow files were changed.
The link checker accepts external HTTPS links directly; there is no external-link
allowlist to update. The existing external-link workflow will discover the new
link in the rendered pages. A direct HTTP check verified the new URL separately.

## Checks

| Command | Exit code | Result |
| --- | --- | --- |
| `git remote get-url origin` | 0 | `https://github.com/sqllocks/shape.git`; required repository confirmed before fetch/checkout. |
| `git fetch origin` | 0 | Fetched main. |
| `git checkout -B lane/W-play-1 origin/main` | 0 | Lane created from main. |
| `mkdocs build --strict` | 0 | Site built successfully. |
| `pytest -q tests/docs_site tests/release/test_docs.py tests/release/test_check_user_facing.py tests/docs/test_launch_site.py tests/docs/test_launch_api.py tests/docs/test_owner_notes.py tests/docs/test_transcripts.py tests/docs/test_fabric_platform.py` | 0 | 289 passed; one existing unsigned-profile warning. |
| `pytest -q 'tests/docs/test_reference_examples.py::test_reference_page[README]' 'tests/docs/test_reference_examples.py::test_reference_page[QUICKSTART]' tests/docs/test_reference_examples.py::test_restored_example_inventory` | 0 | 3 passed; both changed pages' runnable examples and inventory checked. |
| `python scripts/check_doc_links.py --site site` | 0 | 0 broken links in sources and built site. |
| `python scripts/check_user_facing.py --site site` | 0 | Clean. |
| `python scripts/check_docs_language.py --site site` | 0 | 0 wording hits. |
| `curl --fail --silent --show-error --location --output /dev/null --write-out 'HTTP %{http_code}\n' https://sqlbites.net/shape/playground/` | 0 | HTTP 200. |
| `git diff --check` | 0 | No whitespace errors. |
| `git diff --stat origin/main` | 0 | Only README.md, docs/QUICKSTART.md, docs/index.md, CHANGELOG.md and this lane status file; staged status file included. |

## Commit

The signed-off commit containing this file uses the prefix `W-play-1:`.

## Open questions

None.

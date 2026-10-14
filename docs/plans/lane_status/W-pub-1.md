# W-pub-1 — independent publish examples

Base: origin/main at `a8e476ca910c40e3d8e7dd0dace9c80699237e82`.
Scope: split the publish example and its two example manifests; no service runs,
transcript recordings, promotion, surface changes, W-pub-2 or W-pub-3 work.

## Changes and findings

- Split example 0 into lakehouse (0), sql-database (8), warehouse (9), and
  eventhouse (10). Existing examples retain their indices.
- Each Fabric manifest entry has its own target's prerequisites, environment
  references, and rewrites. The reference-example manifest registers all four.
- SQL Database, Warehouse and Eventhouse use `--write-mode replace`, which resets
  existing example rows/schema and also permits a fresh run. Lakehouse already
  overwrites its fixed landing filenames; the database-only flag would have no
  effect there (`commands.py` parser help and `_publish_database`).
- The four displayed output segments concatenate byte-for-byte to the original
  receipt's stdout. Its file and commit attribution remain unchanged. The page
  explicitly says these are portions of the original run and that commands with
  newly added flags await the Owner's release-head re-recording.
- Searched active workflows, scripts, tests, docs/contributing and docs/plugins
  for the old `publish-targets` ID. Only immutable evidence filenames/links and
  the output-preservation test retain it. Workflow example selection reads the
  manifest dynamically; pre-clean has no hardcoded example selection.

## Red evidence

Before changing the page or manifests:

`python -m pytest -q tests/docs/test_fabric_transcripts.py::test_publish_targets_are_independent_rerunnable_examples`

Exit 1: `1 failed in 1.00s`; the target-ID assertion found only
`{'publish-targets'}` instead of the four separate target entries.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
___________ test_publish_targets_are_independent_rerunnable_examples ___________

    def test_publish_targets_are_independent_rerunnable_examples():
        """Each target has its own prerequisites and keeps only its original recorded output."""
        manifest = json.loads((ROOT / "tests/docs/fabric_examples.json").read_text())
        entries = [entry for entry in manifest if entry["id"].startswith("publish-")]
        targets = ("lakehouse", "sql-database", "warehouse", "eventhouse")
>       assert {entry["id"] for entry in entries} == {f"publish-{target}" for target in targets}
E       AssertionError: assert {'publish-targets'} == {'publish-eve...sh-warehouse'}
E         
E         Extra items in the left set:
E         'publish-targets'
E         Extra items in the right set:
E         'publish-eventhouse'
E         'publish-sql-database'
E         'publish-lakehouse'
E         'publish-warehouse'
E         Use -v to get more diff

tests/docs/test_fabric_transcripts.py:183: AssertionError
=========================== short test summary info ============================
FAILED tests/docs/test_fabric_transcripts.py::test_publish_targets_are_independent_rerunnable_examples
1 failed in 1.00s
```

The new test also proves one command/output per target, replacement mode for the
three database targets, matched rewrites, independent prerequisites, reference
manifest registrations, and preservation of the original receipt stdout.

## Command and verification record

Setup and initial inspections:

- `cd /workspace/shape && git remote get-url origin`: exit 0, verified
  `https://github.com/sqllocks/shape.git` before cloning.
- `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-pub-1`:
  exit 0; configured the verified GitHub origin and copied the existing identity.
- `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`;
  `git checkout -B lane/W-pub-1 origin/main`: each exit 0.
- Read CLAUDE.md, CONTRIBUTING.md, the plan's Decisions and 4.11 from
  `git show`; exit 0. No AGENTS.md found (`rg --files -g AGENTS.md`: exit 1).
- Created a Python 3.12 environment, copied third-party dependencies excluding
  first-party packages/editable mappings, and copied the native build target
  with `cp -a --reflink=auto` into this checkout. Exit 0. Used lane-local TMPDIR
  and the configured Rust home; no shared build target.
- Edited tests before docs/manifests; `ruff format tests/docs/test_fabric_transcripts.py`:
  exit 0. An initial attempt before executable reuse returned 127 (ruff absent);
  corrected by copying third-party entry points with this environment's shebang.
- An early neighbour-test run during bootstrap returned 1 (14 failed, 13 passed),
  because the local core was not installed yet. The new docs-only test passed;
  a post-setup run is recorded below.
- `git diff --check`: exit 0.

Final checks, setup completion and push verification are recorded below.

- `PIP_NO_DEPS=1 make bootstrap`: exit 0.
- `pip install --no-deps -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka`: exit 0.
- Python import/path verification: exit 0; `shape`, `shape_domains`,
  `shape_fabric`, `shape_sqlserver`, `shape_eventhubs`, `shape_kafka` all resolve
  to this checkout's sources.
- `ruff check src tests plugins benchmarks/vs_refengine scripts`: exit 0.
- `ruff format --check src tests plugins benchmarks/vs_refengine scripts`: exit 0,
  2150 files already formatted.
- `mypy`: exit 0, 701 source files.
- `pytest -q tests/docs/test_fabric_transcripts.py tests/docs/test_owner_transcripts.py tests/docs/test_transcripts.py`:
  exit 0, 27 passed in 2.80s after bootstrap.
- The same three modules plus
  `tests/docs/test_reference_examples.py::test_reference_page[fabric-commands]`:
  exit 0, 28 passed in 11.24s after all plugins were installed.
- `NO_MKDOCS_2_WARNING=1 mkdocs build --strict`: exit 0.
- `python scripts/check_doc_links.py`; `python scripts/check_user_facing.py`:
  each exit 0.
- `python scripts/check_doc_links.py --site site`;
  `python scripts/check_user_facing.py --site site`: each exit 0.
- `timeout --signal=TERM 300s make check`: exit 124 (single invocation,
  completed setup beforehand). Ruff, mypy, compileall, Vulture, import contracts,
  requirements, secrets, wording, generated exit/failure docs, shipped data,
  plugin skeletons, versions, release workflows, conformance coverage, CLI
  surface, and V1 definition checks all passed. Core pytest reached 7% with no
  reported failure before the time limit. The full suite, its coverage gate,
  later heavy/pure-Python tests and Rust checks did not complete in this run.
  The lead's GitHub CI remains the full-suite proof; no gate was altered.

## Open questions

None. Owner release-head re-recording remains separate follow-up work, as the
prompt requires. Existing live evidence was neither executed nor changed.

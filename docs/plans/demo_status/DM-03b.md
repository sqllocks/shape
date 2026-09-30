# DM-03b — PyPI early-access release (L1)

- Status: **workflow committed and tested locally; publishing not triggered** (owner action, O-01).
- Commit: `f17fd143f36b99d90b18a1b3c071e60fba699b9d` (code); this status file is committed right after it.
- Branch: `demo/l1-core`
- Plan dependency not verifiable from this lane: "§12.7 checks 1–3 passing on the merged branch". The lead must
  confirm them after merging before the owner publishes.

## What was built

`.github/workflows/publish.yml` (name `Publish`):

- Triggers: `workflow_dispatch` with input `repository` (`testpypi` | `pypi`, default `testpypi`) and tags `v*`.
- Job `build` (permissions: `contents: read` only), Python 3.11:
  - a tag must equal the package version (`v0.9.0` for `0.9.0`);
  - `grep -qi "early access" README.md` — the build fails until the README says it. The README is L3's
    DM-00 deliverable ("Early access: profiling is available now; …") and is **not** changed by this lane;
  - `python scripts/build_pure_wheel.py --verify --python python3.11`: builds the DM-03 pure wheel, installs it in a
    clean venv with only numpy, pyarrow, pandas and deltalake, and runs `tests/demo/core` against the installed
    wheel;
  - `python -m build --sdist`; `python -m twine check dist/*`; uploads `dist/*` as the artifact `dist`.
- Job `publish` (`needs: build`): `environment` is `testpypi` when dispatched with `repository=testpypi`, otherwise
  `pypi`; `permissions: {id-token: write, contents: read}`; downloads `dist`; publishes with
  `pypa/gh-action-pypi-publish@release/v1` (TestPyPI adds `repository-url: https://test.pypi.org/legacy/`).
  No API tokens, no passwords, no `secrets.*`.
- Version is `0.9.0` (not a pre-release) in `pyproject.toml` and `shape.__version__`.
- `tests/demo/core/test_publish_workflow.py` checks the file name, triggers, environments, permissions,
  the publish action, the absence of secrets, the build/test/twine steps on Python 3.11, and that the version is not
  a pre-release and equals `shape.__version__`.

## Acceptance commands and results (all run in this session)

1. `actionlint .github/workflows/publish.yml` (actionlint 1.7.12): no findings, exit 0.
2. `~/.venvs/shape/bin/python scripts/build_pure_wheel.py --verify --python python3.11`: **exit 0**.
   `sqllocks_shape-0.9.0-py3-none-any.whl`, **1,357,626 bytes** (1.36 MB); fresh Python 3.11 venv with only
   numpy, pyarrow, pandas, deltalake, pytest; `pip install <wheel>` ok; `import shape` gives `0.9.0` from site-packages;
   `tests/demo/core` against the installed wheel: **90 passed**; `shape --help` ok.
3. `python -m twine check dist/*.whl`: PASSED.
4. `python -m build --sdist --sdist` produced `sqllocks_shape-0.9.0.tar.gz` (1.07 MB); `twine check`: PASSED.
   The sdist also builds after the `rq/`, `docs/qualification/`, `docs/audit/` and root evidence files that DM-00
   deletes are gone (checked on a copy of the tree without them).
5. `~/.venvs/shape/bin/python -m pytest tests -q -p no:cacheprovider` (whole repo suite): **683 passed**.
   `ruff check src tests/demo scripts/build_pure_wheel.py`: passed.

## Not done here, on purpose

- **No publish was triggered.** No TestPyPI dry run was made (needs O-01 on test.pypi.org and the GitHub
  environments), and `pip install sqllocks-shape==0.9.0` from the index was not tested.
- If O-01 is not done, the runbook's manual wheel-upload path stays the demo's route: build the wheel with
  `python scripts/build_pure_wheel.py` and upload `dist/sqllocks_shape-0.9.0-py3-none-any.whl`.

## Owner checklist (O-01)

1. PyPI (and TestPyPI): add a pending publisher for project `sqllocks-shape`: owner `sqllocks`, repository `shape`,
   workflow `publish.yml`, environment `pypi` (TestPyPI: `testpypi`).
2. GitHub: create the environments `pypi` and `testpypi` (required reviewer: the owner).
3. After the lane merge and §12.7 checks 1–3: Actions → Publish → Run workflow with `repository = testpypi`; then in a
   clean Python 3.11 venv `pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ sqllocks-shape==0.9.0`
   and `python -c "import shape"`; then run it again with `repository = pypi`.
4. The `build` job fails while `README.md` lacks "early access": merge L3's DM-00 first (the plan's merge order does).

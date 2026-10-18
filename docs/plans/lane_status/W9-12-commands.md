
- `PYTHONPATH=src:plugins/shape-fabric/src PATH=/tmp/w9-10-bin:$PATH UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync pytest -q plugins/shape-fabric/tests/test_w9_12.py plugins/shape-fabric/tests/test_semantic_source.py plugins/shape-fabric/tests/test_profile_model.py` — exit 0; zombies 18 → 18.

- `PATH=/tmp/w9-10-bin:$PATH UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync ruff check src tests plugins benchmarks/vs_refengine` — exit 0; zombies 18 → 18.

- `PATH=/tmp/w9-10-bin:$PATH UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync ruff format --check src tests plugins benchmarks/vs_refengine` — exit 0; zombies 18 → 18.

- `git diff --exit-code origin/main -- src/shape/profile/reference` — exit 0; zombies 22 → 22.

- `git diff --check` — exit 2; zombies 22 → 22.

- `git diff origin/main -- src/shape/drift/policy.py src/shape/api.py` — exit 0; zombies 22 → 22.

- `git status --short` — exit 0; zombies 22 → 22.

- `git diff --check` — exit 2; zombies 23 → 23.

- `PYTHONPATH=src:plugins/shape-fabric/src PATH=/tmp/w9-10-bin:$PATH UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync python scripts/check_requirements.py` — exit 0; zombies 23 → 23.

- `PYTHONPATH=src:plugins/shape-fabric/src PATH=/tmp/w9-10-bin:$PATH UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync python scripts/check_conformance_coverage.py` — exit 0; zombies 23 → 23.

- `PYTHONPATH=src:plugins/shape-fabric/src PATH=/tmp/w9-10-bin:$PATH UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync python scripts/check_user_facing.py` — exit 0; zombies 23 → 23.

- `git diff --check` — exit 0; zombies 23 → 23.

- `git add CHANGELOG.md docs plugins/shape-fabric src/shape/api.py src/shape/drift && git commit -s -m "W9-12: preserve semantic measures roles and native role reads"` — exit 0; zombies 24 → 25.

- `git push origin HEAD` — exit 0; zombies 25 → 25.

- `PATH=/tmp/w9-10-bin:$PATH make bootstrap` — exit 0; zombies 20 → 27.

- `PATH=/tmp/w9-10-bin:$PATH PYTHONPATH=src:plugins/shape-fabric/src UV_PROJECT_ENVIRONMENT=/workspace/shape/.venv uv run --locked --no-sync python scripts/stable_api_compat.py --check` — exit 0; zombies 27 → 27.

- `git diff --stat origin/main` — exit 0; zombies 27 → 27.

- `umask 022; PATH=/tmp/w9-10-bin:$PATH uv run --locked --no-sync pytest -q plugins/shape-fabric/tests/test_w9_12.py plugins/shape-fabric/tests/test_semantic_source.py plugins/shape-fabric/tests/test_profile_model.py` — exit 0; zombies 27 → 27.

- `umask 022; PATH=/tmp/w9-10-bin:$PATH SHAPE_KERNEL=python uv run --locked --no-sync pytest -q plugins/shape-fabric/tests/test_w9_12.py plugins/shape-fabric/tests/test_semantic_source.py plugins/shape-fabric/tests/test_profile_model.py` — exit 0; zombies 27 → 27.

- `umask 022; PATH=/tmp/w9-10-bin:$PATH NO_MKDOCS_2_WARNING=1 uv run --locked --group docs mkdocs build --strict > /tmp/w9-12-docs.log 2>&1` — exit 0; zombies 27 → 27.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked --no-sync mypy` — exit 0; zombies 27 → 27.

- `umask 022; PATH=/tmp/w9-10-bin:$PATH PYTHONPATH=/workspace/shape/src:/workspace/shape/plugins/shape-fabric/src uv run --locked --no-sync pytest -q plugins/shape-fabric/tests/test_w9_12.py > /tmp/w9-12-current-tests-main-red.log 2>&1` — exit 1; zombies 27 → 27.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked --with nbformat python -c "import nbformat; print(nbformat.__version__)"` — exit 0; zombies 29 → 29.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked --group docs python scripts/check_doc_links.py --site site` — exit 0; zombies 29 → 29.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked --group docs python scripts/check_user_facing.py --site site` — exit 0; zombies 29 → 29.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked --no-sync python scripts/check_dco.py origin/main HEAD` — exit 0; zombies 29 → 29.

- `umask 022; PATH=/tmp/w9-10-bin:$PATH uv run --locked --with-requirements tests/demo/fabric/requirements.txt pytest --collect-only -q -m "not emulator and not live" > /tmp/w9-12-collection-prepared.log 2>&1` — exit 0; zombies 30 → 30.

- `git add docs/plans/lane_status/W9-12.md docs/plans/lane_status/W9-12-commands.md && git commit -s -m "W9-12: record current kernel and independent review evidence"` — exit 0; zombies 31 → 32.

- `git push origin HEAD` — exit 0; zombies 32 → 32.

- `gh pr create --draft --base main --head lane/W9-12-finish --title "Preserve semantic-model measures and roles across profile and export" --body-file /tmp/w9-12-pr.md` — exit 0; zombies 32 → 32.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked --with-requirements tests/demo/fabric/requirements.txt python -c "import pyarrow,numpy,pandas; print(pyarrow.__version__,numpy.__version__,pandas.__version__)"` — exit 0; zombies 33 → 33.

- `PATH=/tmp/w9-10-bin:$PATH uv run --locked python -c "import pyarrow,numpy,pandas; print(pyarrow.__version__,numpy.__version__,pandas.__version__)"` — exit 0; zombies 33 → 33.

- `. /tmp/public-b-test-env.sh; uv run --locked --no-sync pytest -q "tests/docs/test_reference_examples.py::test_reference_page[CONTAINER]" > /tmp/public-b-container-prepared.log 2>&1` — exit 0; zombies 40 → 45.

- `. /tmp/public-b-test-env.sh; uv run --locked pytest -q tests/docs_site/test_docs_site.py::test_every_site_page_is_in_the_nav_exactly_once tests/project/test_semver_cli.py::test_project_schema_lists_every_change_kind > /tmp/w9-12-ci-integration-red.log 2>&1` — exit 1; zombies 85 → 85.

- `. /tmp/public-b-test-env.sh; uv run --locked pytest -q tests/docs_site tests/project/test_semver_cli.py tests/project/test_compat.py tests/editors/test_vscode_extension.py > /tmp/w9-12-ci-integration-green.log 2>&1` — exit 0; zombies 85 → 85.

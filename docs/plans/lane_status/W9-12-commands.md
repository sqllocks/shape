
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

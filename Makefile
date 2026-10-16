PYTHON ?= python
UV ?= uv
.PHONY: bootstrap bootstrap-pip lock lock-check docs-export-check check test security zero-network
bootstrap:
	$(UV) sync --locked --group docs --group dbt --group benchmark
bootstrap-pip:
	"$(PYTHON)" -m pip install -e ".[dev]" -e plugins/shape-domains
lock:
	$(UV) lock
lock-check:
	$(UV) lock --check
docs-export-check:
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_docs_requirements.py
# Same commands as the `test` job in .github/workflows/ci.yml (T-27 scope: only paths that exist).
check: lock-check docs-export-check
	$(UV) run --locked --group docs --group dbt --group benchmark ruff check src tests plugins benchmarks/vs_refengine
	$(UV) run --locked --group docs --group dbt --group benchmark ruff format --check src tests plugins benchmarks/vs_refengine
	$(UV) run --locked --group docs --group dbt --group benchmark mypy
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" -m compileall -q src/shape
	$(UV) run --locked --group docs --group dbt --group benchmark vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
	$(UV) run --locked --group docs --group dbt --group benchmark lint-imports
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_requirements.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_secrets.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_user_facing.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/gen_exit_codes.py --check
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/gen_failure_modes.py --check
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_shipped_data.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_plugin_skeletons.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_versions.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_release_workflows.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_conformance_coverage.py
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/cli_surface.py --check
	$(UV) run --locked --group docs --group dbt --group benchmark "$(PYTHON)" scripts/check_v1_done.py
	$(UV) run --locked --group docs --group dbt --group benchmark pytest -q -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86
	$(UV) run --locked --group docs --group dbt --group benchmark pytest -q -m heavy tests/kernel tests/profile tests/streaming
	SHAPE_KERNEL=python $(UV) run --locked --group docs --group dbt --group benchmark pytest -q tests/kernel
	$(UV) run --locked --group docs --group dbt --group benchmark cargo fmt --manifest-path rust/shape-kernel/Cargo.toml --check
	$(UV) run --locked --group docs --group dbt --group benchmark cargo clippy --manifest-path rust/shape-kernel/Cargo.toml --all-targets -- -D warnings
	$(UV) run --locked --group docs --group dbt --group benchmark cargo test --manifest-path rust/shape-kernel/Cargo.toml
test:
	$(UV) run --locked --group docs --group dbt --group benchmark pytest -m "not emulator and not live"
security:
	$(UV) run --locked --group docs --group dbt --group benchmark pytest -m security
zero-network:
	$(UV) run --locked --group docs --group dbt --group benchmark unshare --net -- sh -c 'ip link set lo up; $(UV) run --locked --group docs --group dbt --group benchmark pytest -q -m "zero_network and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content'

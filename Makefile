PYTHON ?= python
.PHONY: bootstrap check test security
bootstrap:
	$(PYTHON) -m pip install -e ".[dev]"
# Same commands as the `test` job in .github/workflows/ci.yml (T-27 scope: only paths that exist).
check:
	ruff check src tests plugins benchmarks/vs_spindle
	ruff format --check src tests plugins benchmarks/vs_spindle
	mypy
	$(PYTHON) -m compileall -q src/shape
	vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
	lint-imports
	$(PYTHON) scripts/check_requirements.py
	$(PYTHON) scripts/check_secrets.py
	$(PYTHON) scripts/check_user_facing.py
	$(PYTHON) scripts/check_plugin_skeletons.py
	$(PYTHON) scripts/check_conformance_coverage.py
	pytest -q -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86
	pytest -q -m heavy tests/kernel tests/profile
	SHAPE_KERNEL=python pytest -q tests/kernel
	cargo fmt --manifest-path rust/shape-kernel/Cargo.toml --check
	cargo clippy --manifest-path rust/shape-kernel/Cargo.toml --all-targets -- -D warnings
	cargo test --manifest-path rust/shape-kernel/Cargo.toml
test:
	pytest -m "not emulator and not live"
security:
	pytest -m security

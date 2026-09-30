PYTHON ?= python
.PHONY: bootstrap check test security
bootstrap:
	$(PYTHON) -m pip install -e ".[dev]"
# Same commands as the `test` job in .github/workflows/ci.yml (T-27 scope: only paths that exist).
check:
	ruff check src tests
	ruff format --check src tests
	mypy
	$(PYTHON) -m compileall -q src/shape
	$(PYTHON) scripts/check_requirements.py
	$(PYTHON) scripts/check_secrets.py
	pytest -q -m "not emulator and not live" --ignore=tests/demo/fabric --ignore=tests/demo/content --cov=shape --cov-fail-under=86
test:
	pytest -m "not emulator and not live"
security:
	pytest -m security

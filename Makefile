PYTHON ?= python
.PHONY: bootstrap check test security
bootstrap:
	$(PYTHON) -m pip install -e ".[dev]"
check:
	ruff check .
	mypy src/shape
	$(PYTHON) scripts/check_requirements.py
	$(PYTHON) scripts/check_secrets.py
test:
	pytest
security:
	pytest -m security

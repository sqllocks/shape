"""Makes ``consumer_helpers`` importable (the suite runs with ``--import-mode=importlib``) and
provides the producer repository fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = str(Path(__file__).parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from consumer_helpers import FINANCE, MARKETING, contract  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A producer repository: shape.yml with column owners, and contracts/consumers/ holding the
    finance and marketing contracts. Returns the repository folder."""
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nname: producer\nsources:\n  orders:\n"
        "    path: data/orders\n    columns:\n      orders.amount:\n"
        "        owner: finance-data@example.com\n      customer.age:\n"
        "        owner: crm-data@example.com\n",
        encoding="utf-8",
    )
    d = tmp_path / "contracts" / "consumers"
    d.mkdir(parents=True)
    (d / "finance.json").write_text(json.dumps(contract("finance", FINANCE)), encoding="utf-8")
    (d / "marketing.json").write_text(
        json.dumps(contract("marketing", MARKETING)), encoding="utf-8"
    )
    return tmp_path

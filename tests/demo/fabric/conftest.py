"""Fixtures for the Fabric lane tests; helpers live in fabric_helpers.py."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests use importlib import mode

import fabric_helpers  # noqa: E402
from fabric_helpers import CONTRACT, make_orders, report_header  # noqa: E402


def pytest_report_header(config: pytest.Config) -> str:
    return report_header()


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    terminalreporter.write_line(f"fabric lane: shape API under test = {fabric_helpers.SHAPE_API}")


@pytest.fixture(scope="module", autouse=True)
def _shape_api():
    with fabric_helpers.shape_api() as mode:
        yield mode


@pytest.fixture()
def lakehouse(tmp_path: Path) -> Path:
    """A directory standing in for /lakehouse/default, with day-1/day-2 Delta tables."""
    from deltalake import write_deltalake

    root = tmp_path / "lakehouse"
    (root / "Files" / "contracts").mkdir(parents=True)
    (root / "Tables").mkdir()
    for day in (1, 2):
        write_deltalake(str(root / "Tables" / f"orders_day{day}"), make_orders(day))
    (root / "Files" / "contracts" / "orders.json").write_text(json.dumps(CONTRACT))
    import shape

    base = shape.profile(make_orders(1), name="orders_day1")
    (root / "Files" / "baselines").mkdir()
    shape.save(base, str(root / "Files" / "baselines" / "orders_day1.shape"))
    return root


@pytest.fixture()
def fabric_onelake(monkeypatch):
    """The Fabric runtime as the notebooks see it, for a Delta write (DEMO-LIVE F-1): the
    ``notebookutils`` stub gets a runtime context naming the default lakehouse and a
    ``credentials.getToken``, and ``deltalake.write_deltalake`` records its calls instead of
    writing (no OneLake here). Yields the list of recorded calls."""
    import deltalake

    utils = sys.modules["notebookutils"]
    monkeypatch.setattr(
        utils,
        "runtime",
        types.SimpleNamespace(
            context={
                "currentWorkspaceId": "11111111-2222-3333-4444-555555555555",
                "defaultLakehouseWorkspaceId": "11111111-2222-3333-4444-555555555555",
                "defaultLakehouseId": "66666666-7777-8888-9999-000000000000",
                "defaultLakehouseName": "shape_demo",
            }
        ),
        raising=False,
    )
    monkeypatch.setattr(
        utils,
        "credentials",
        types.SimpleNamespace(getToken=lambda audience: f"token-for-{audience}"),
        raising=False,
    )
    calls: list[dict] = []

    def record(location, data, **kwargs):
        table = data.read_all() if hasattr(data, "read_all") else data
        calls.append({"location": location, "table": table, **kwargs})

    monkeypatch.setattr(deltalake, "write_deltalake", record)
    yield calls

"""Shared fixtures for the contract emitters (W5-04)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "contracts" / "orders.contract.json"
TARGETS = ("ddl", "jsonschema", "pandera", "gx")
DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")


@pytest.fixture
def orders() -> dict[str, Any]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def orders_path() -> Path:
    return EXAMPLE


@pytest.fixture
def two_tables(orders: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": {
            "orders": copy.deepcopy(orders),
            "customers": {
                "required_columns": ["id"],
                "columns": {
                    "id": {"dtype": "integer", "nullable": False, "unique": True},
                    "tier": {"dtype": "string", "allowed_values": ["a", "b"]},
                },
            },
        }
    }

"""Shared fixtures for the W5-10 tests: a two-table generation schema (customer <- order), a
profile with a planted key concentration, and a run manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest


def _col(name: str, strategy: str, type_: str = "integer", **gen: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, "generator": {"strategy": strategy, **gen}}


def schema_doc(
    customers: int = 50, orders: int = 200, large: tuple[int, int] = (2_000, 40_000)
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model": {"name": "shop", "seed": 7},
        "tables": {
            "customer": {
                "name": "customer",
                "primary_key": ["customer_id"],
                "columns": {
                    "customer_id": _col("customer_id", "sequence", start=1),
                    "name": _col(
                        "name",
                        "weighted_enum",
                        "string",
                        values={"ann": 3, "bo": 2, "cy": 1, "di": 1, "ed": 1},
                    ),
                },
            },
            "order": {
                "name": "order",
                "primary_key": ["order_id"],
                "columns": {
                    "order_id": _col("order_id", "sequence", start=1),
                    "customer_id": _col("customer_id", "foreign_key", ref="customer.customer_id"),
                    "amount": _col("amount", "distribution", "float", low=1.0, high=99.0),
                },
            },
        },
        "relationships": [
            {
                "name": "o_c",
                "parent": "customer",
                "child": "order",
                "parent_columns": ["customer_id"],
                "child_columns": ["customer_id"],
            }
        ],
        "generation": {
            "scale": "small",
            "scales": {
                "small": {"customer": customers, "order": orders},
                "large": {"customer": large[0], "order": large[1]},
            },
        },
    }


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "shop.schema.json"
    path.write_text(json.dumps(schema_doc()), encoding="utf-8")
    return path


@pytest.fixture
def small_table() -> pa.Table:
    return pa.table(
        {
            "id": pa.array(list(range(40)), pa.int64()),
            "name": [f"n{i % 7}" for i in range(40)],
            "score": [float(i) / 3 for i in range(40)],
        }
    )


@pytest.fixture
def repro() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile_version": 1,
        "seed": 7,
        "scale": "small",
        "shape_version": "0.9.0",
        "kernel": "python",
        "platform": "linux-x86_64",
    }


@pytest.fixture
def keypair() -> tuple[bytes, bytes]:
    pytest.importorskip("cryptography")
    from shape.artifact.signing import generate_keypair

    return generate_keypair()


@pytest.fixture
def make_manifest(repro):
    """``make_manifest(path, tables)``: a run manifest whose dataset id is the id of ``tables``
    (``{name: pa.Table}``), with each table's file path recorded as ``<name>.parquet``."""
    from shape.repro import dataset_id
    from shape.scenario.manifest import ManifestBuilder, RunManifest

    def make(path: Path, tables: dict[str, pa.Table], **extra: Any) -> Path:
        manifest = RunManifest(
            run_id="20260101_000000_shop_small_s7",
            spec_hash="",
            pack_id="",
            domain="shop",
            scale="small",
            seed=7,
            engine_version="0.9.0",
            tables={
                n: {"rows": t.num_rows, "file_paths": [f"{n}.parquet"]} for n, t in tables.items()
            },
            reproducibility=extra.pop("reproducibility", repro),
            dataset_id=extra.pop("dataset_id", dataset_id(tables)),
        )
        ManifestBuilder.to_file(manifest, path)
        return path

    return make

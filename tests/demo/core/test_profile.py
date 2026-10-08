"""shape.profile: every source type, multi-table FK detection, summary size."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest
from deltalake import write_deltalake

import shape
from shape.profile.reference import Profile, SourceError


def _cols(p: Profile) -> dict:
    return p.to_dict()["columns"]


def test_profile_arrow_table(orders):
    p = shape.profile(orders)
    d = p.to_dict()
    assert d["name"] == "table"
    assert d["row_count"] == 400
    cols = d["columns"]
    assert list(cols) == orders.column_names
    assert cols["order_id"]["dtype"] == "integer"
    assert cols["order_id"]["is_unique"] is True
    assert cols["order_id"]["is_primary_key"] is True
    assert cols["amount"]["dtype"] == "float"
    assert cols["status"]["is_enum"] is True
    assert set(cols["status"]["enum_values"]) == {"placed", "shipped", "returned"}
    assert cols["email"]["pattern"] == "email"
    assert 0 < cols["email"]["null_rate"] < 0.2
    assert cols["order_date"]["dtype"] in ("date", "datetime")


def test_profile_name_argument(orders):
    assert shape.profile(orders, name="orders").to_dict()["name"] == "orders"


def test_profile_pandas_dataframe_matches_arrow(orders):
    from_pandas = shape.profile(orders.to_pandas()).to_dict()
    from_arrow = shape.profile(orders).to_dict()
    assert from_pandas == from_arrow


def test_profile_csv_file(tmp_path: Path, orders):
    path = tmp_path / "orders.csv"
    pacsv.write_csv(orders, path)
    p = shape.profile(path)
    d = p.to_dict()
    assert d["name"] == "orders"
    assert d["row_count"] == 400
    assert d["columns"]["order_id"]["dtype"] == "integer"
    # CSV text dates stay text, exactly as pandas.read_csv does
    assert d["columns"]["status"]["is_enum"] is True
    assert shape.profile(str(path)).to_dict() == d


def test_profile_parquet_file(tmp_path: Path, orders):
    path = tmp_path / "orders.parquet"
    pq.write_table(orders, path)
    d = shape.profile(path).to_dict()
    assert d["name"] == "orders"
    assert d["columns"]["order_date"]["dtype"] in ("date", "datetime")
    assert d["columns"] == shape.profile(orders).to_dict()["columns"]


def test_profile_jsonl_file(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    rows = [{"id": i, "kind": "a" if i % 2 else "b", "score": i * 0.5} for i in range(50)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    d = shape.profile(path).to_dict()
    assert d["name"] == "events"
    assert d["row_count"] == 50
    assert d["columns"]["id"]["dtype"] == "integer"
    assert d["columns"]["kind"]["cardinality"] == 2


def test_profile_delta_table(tmp_path: Path, orders):
    table_dir = tmp_path / "orders_delta"
    write_deltalake(str(table_dir), orders)
    p = shape.profile(table_dir)
    d = p.to_dict()
    assert d["name"] == "orders_delta"
    assert d["row_count"] == 400
    assert d["columns"]["order_id"]["is_unique"] is True
    assert d["columns"]["amount"]["dtype"] == "float"


def test_profile_delta_table_with_decimal(tmp_path: Path):
    import decimal

    t = pa.table(
        {
            "price": pa.array(
                [decimal.Decimal("1.25"), decimal.Decimal("3.50")], pa.decimal128(10, 2)
            )
        }
    )
    table_dir = tmp_path / "prices"
    write_deltalake(str(table_dir), t)
    col = shape.profile(table_dir).to_dict()["columns"]["price"]
    assert col["dtype"] == "float"
    assert col["mean"] == pytest.approx(2.375)


def test_profile_glob(tmp_path: Path, orders):
    for i in range(3):
        pq.write_table(orders.slice(i * 100, 100), tmp_path / f"part-{i}.parquet")
    d = shape.profile(str(tmp_path / "part-*.parquet")).to_dict()
    assert d["row_count"] == 300
    assert d["name"] == "part-"


def test_profile_directory_of_files(tmp_path: Path, orders):
    folder = tmp_path / "orders"
    folder.mkdir()
    for i in range(4):
        pq.write_table(orders.slice(i * 100, 100), folder / f"p{i}.parquet")
    (folder / "_SUCCESS").write_text("")
    d = shape.profile(folder).to_dict()
    assert d["name"] == "orders"
    assert d["row_count"] == 400


def test_profile_directory_of_csv_files(tmp_path: Path, orders):
    folder = tmp_path / "csvs"
    folder.mkdir()
    for i in range(2):
        pacsv.write_csv(orders.slice(i * 200, 200), folder / f"p{i}.csv")
    assert shape.profile(folder).to_dict()["row_count"] == 400


def test_multi_table_detects_foreign_keys(orders, customers):
    p = shape.profile({"orders": orders, "customer": customers})
    d = p.to_dict()
    assert set(d["tables"]) == {"orders", "customer"}
    assert d["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}
    col = d["tables"]["orders"]["columns"]["customer_id"]
    assert col["is_foreign_key"] is True
    assert col["fk_ref_table"] == "customer"
    assert d["relationships"][0]["parent"] == "customer"
    assert d["relationships"][0]["child"] == "orders"
    assert d["tables"]["customer"]["primary_key"] == ["customer_id"]


def test_multi_table_from_mixed_sources(tmp_path: Path, orders, customers):
    pq.write_table(orders, tmp_path / "o.parquet")
    p = shape.profile({"orders": tmp_path / "o.parquet", "customer": customers.to_pandas()})
    assert p.to_dict()["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}


def test_summary_single_table(orders):
    s = shape.profile(orders, name="orders").summary()
    assert s["name"] == "orders"
    assert s["row_count"] == 400
    assert set(s["columns"]["order_id"]) == {
        "dtype",
        "null_rate",
        "cardinality",
        "is_unique",
        "is_primary_key",
        "is_foreign_key",
        "fk_ref_table",
        "distribution",
        "pattern",
        "min",
        "max",
        "mean",
        "std",
    }
    assert s["columns"]["order_id"]["min"] == 1
    assert s["columns"]["order_id"]["max"] == 400
    json.dumps(s, allow_nan=False)  # JSON-safe


def test_summary_multi_table(orders, customers):
    s = shape.profile({"orders": orders, "customer": customers}).summary()
    assert set(s["tables"]) == {"orders", "customer"}
    assert s["row_count"] == 460
    assert s["relationships"]


def test_summary_is_small_for_500_columns():
    rng = np.random.default_rng(1)
    n = 200
    data = {}
    for i in range(500):
        kind = i % 4
        if kind == 0:
            data[f"c{i}"] = rng.normal(size=n)
        elif kind == 1:
            data[f"c{i}"] = rng.integers(0, 1000, size=n)
        elif kind == 2:
            data[f"c{i}"] = rng.choice(["x", "y", "z"], size=n)
        else:
            data[f"c{i}"] = rng.lognormal(size=n)
    s = shape.profile(pa.table(data)).summary()
    assert len(s["columns"]) == 500
    assert len(json.dumps(s)) < 1_000_000


def test_empty_dict_and_bad_sources_raise(tmp_path: Path):
    with pytest.raises(SourceError):
        shape.profile({})
    with pytest.raises(SourceError):
        shape.profile(12345)
    with pytest.raises(FileNotFoundError):
        shape.profile(tmp_path / "missing.csv")
    (tmp_path / "x.txt").write_text("a")
    with pytest.raises(SourceError):
        shape.profile(tmp_path / "x.txt")
    with pytest.raises(FileNotFoundError):
        shape.profile(str(tmp_path / "nothing-*.csv"))


def test_delta_without_deltalake_raises_clear_import_error(tmp_path: Path, orders, monkeypatch):
    table_dir = tmp_path / "d"
    write_deltalake(str(table_dir), orders)
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "deltalake" or name.startswith("deltalake."):
            raise ImportError("no deltalake")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="deltalake"):
        shape.profile(table_dir)


def test_constant_and_null_columns():
    t = pa.table({"k": [1, 1, 1, 1], "n": pa.array([None] * 4, pa.float64()), "s": ["a"] * 4})
    cols = _cols(shape.profile(t))
    assert cols["k"]["cardinality"] == 1
    assert cols["n"]["null_rate"] == 1.0
    assert cols["n"]["mean"] is None
    assert cols["s"]["is_enum"] is True


def test_profile_is_deterministic(orders):
    a = shape.profile(orders).to_dict()
    b = shape.profile(orders).to_dict()
    assert json.dumps(a) == json.dumps(b)

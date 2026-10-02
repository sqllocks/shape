"""``shape capture`` reads every input ``shape profile`` reads, and ``capture`` -> ``compatibility``
reports a renamed, a dropped and a retyped column on Parquet and Delta feeds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

N = 120


def orders(**override: Any) -> pa.Table:
    cols: dict[str, Any] = {
        "id": pa.array(range(N), pa.int64()),
        "status": pa.array([["new", "paid", "shipped"][i % 3] for i in range(N)]),
        "amount": pa.array([i * 1.5 for i in range(N)], pa.float64()),
        "qty": pa.array([i % 7 + 1 for i in range(N)], pa.int64()),
    }
    cols.update(override)
    return pa.table({k: v for k, v in cols.items() if v is not None})


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    return code, capsys.readouterr().out


def model(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    code, out = run(capsys, "capture", *argv)
    assert code == 0
    loaded: dict[str, Any] = json.loads(out)
    return loaded


def write_delta(path: Path, table: pa.Table, mode: str = "overwrite") -> None:
    deltalake = pytest.importorskip("deltalake")
    deltalake.write_deltalake(str(path), table, mode=mode, schema_mode="overwrite")


@pytest.fixture
def feeds(tmp_path: Path) -> dict[str, Path]:
    table = orders()
    paths = {
        "csv": tmp_path / "orders.csv",
        "parquet": tmp_path / "orders.parquet",
        "jsonl": tmp_path / "orders.jsonl",
    }
    pacsv.write_csv(table, paths["csv"])
    pq.write_table(table, paths["parquet"])
    paths["jsonl"].write_text(
        "".join(json.dumps(r) + "\n" for r in table.to_pylist()), encoding="utf-8"
    )
    return paths


def test_the_same_table_gives_the_same_model_in_every_format(
    feeds: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    models = {kind: model(capsys, str(p)) for kind, p in feeds.items()}
    assert models["csv"] == models["parquet"] == models["jsonl"]
    assert models["parquet"]["rows"] == N and set(models["parquet"]["columns"]) == {
        "id",
        "status",
        "amount",
        "qty",
    }


def test_a_folder_a_glob_and_a_delta_table_give_the_same_model(
    tmp_path: Path, feeds: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    expected = model(capsys, str(feeds["parquet"]))
    folder = tmp_path / "parts"
    folder.mkdir()
    table = orders()
    pq.write_table(table.slice(0, 50), folder / "a.parquet")
    pq.write_table(table.slice(50), folder / "b.parquet")
    assert model(capsys, str(folder)) == expected
    assert model(capsys, str(folder / "*.parquet")) == expected
    delta = tmp_path / "orders_delta"
    write_delta(delta, table)
    assert model(capsys, str(delta)) == expected


def test_a_date_column_is_text_in_csv_and_a_timestamp_in_parquet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented format-driven difference: the values are the same text in both models, but
    Parquet holds a typed timestamp, which is rendered with a time of day."""
    stamp = pa.array([f"2024-01-{i % 28 + 1:02d}" for i in range(N)])
    csv = tmp_path / "t.csv"
    pacsv.write_csv(pa.table({"d": stamp, "n": pa.array(range(N))}), csv)
    parquet = tmp_path / "t.parquet"
    pq.write_table(pa.table({"d": stamp.cast(pa.date32()), "n": pa.array(range(N))}), parquet)
    a, b = model(capsys, str(csv)), model(capsys, str(parquet))
    assert a["columns"]["n"] == b["columns"]["n"] and a["rows"] == b["rows"] == N
    assert a["columns"]["d"]["kind"] == b["columns"]["d"]["kind"] == "text"
    assert a["columns"]["d"]["distinct_estimate"] == b["columns"]["d"]["distinct_estimate"]


def test_a_dataset_folder_is_one_table_per_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "ds"
    folder.mkdir()
    pq.write_table(orders(), folder / "orders.parquet")
    pq.write_table(
        pa.table({"cid": pa.array(range(5)), "name": pa.array(list("abcde"))}),
        folder / "customers.parquet",
    )
    doc = model(capsys, str(folder), "--dataset")
    assert sorted(doc["tables"]) == ["customers", "orders"]
    assert doc["tables"]["orders"]["rows"] == N
    out = tmp_path / "ds.shape"
    assert run(capsys, "capture", str(folder), "--dataset", "-o", str(out))[0] == 0
    # a folder whose files do not share their columns is not one table
    assert main(["capture", str(folder)]) == 2


def test_dataset_compatibility_names_the_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    for folder, cust in ((before, ["cid", "name"]), (after, ["cid"])):
        folder.mkdir()
        pq.write_table(orders(), folder / "orders.parquet")
        pq.write_table(
            pa.table({c: pa.array(range(5)) for c in cust}), folder / "customers.parquet"
        )
    for folder in (before, after):
        assert run(capsys, "capture", str(folder), "--dataset", "-o", f"{folder}.shape")[0] == 0
    code, out = run(capsys, "compatibility", f"{before}.shape", f"{after}.shape")
    issues = json.loads(out)["issues"]
    assert code == 5 and [i["path"] for i in issues] == ["tables.customers.columns.name"]


CHANGES = {
    "renamed": (orders(amount=None, total=orders().column("amount")), "removed", "columns.amount"),
    "dropped": (orders(qty=None), "removed", "columns.qty"),
    "retyped": (
        orders(qty=pa.array([f"q{i % 7}" for i in range(N)])),
        "type_changed",
        "columns.qty.kind",
    ),
}


def compat(
    capsys: pytest.CaptureFixture[str], base: Path, today: Path
) -> tuple[int, dict[str, Any]]:
    code, out = run(capsys, "compatibility", str(base), str(today))
    return code, json.loads(out)


def test_unchanged_feed_is_compatible(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("base", "today"):
        pq.write_table(orders(), tmp_path / f"{name}.parquet")
        assert (
            run(
                capsys,
                "capture",
                str(tmp_path / f"{name}.parquet"),
                "-o",
                str(tmp_path / f"{name}.shape"),
            )[0]
            == 0
        )
    code, report = compat(capsys, tmp_path / "base.shape", tmp_path / "today.shape")
    assert code == 0 and report["compatible"] and report["issues"] == []


@pytest.mark.parametrize("change", sorted(CHANGES))
@pytest.mark.parametrize("fmt", ["parquet", "delta"])
def test_schema_changes_in_a_feed_are_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fmt: str, change: str
) -> None:
    changed, kind, path = CHANGES[change]
    feeds = {}
    for name, table in (("base", orders()), ("today", changed)):
        if fmt == "parquet":
            feeds[name] = tmp_path / f"{name}.parquet"
            pq.write_table(table, feeds[name])
        else:
            feeds[name] = tmp_path / f"{name}_delta"
            write_delta(feeds[name], table)
        out = tmp_path / f"{name}.shape"
        assert run(capsys, "capture", str(feeds[name]), "-o", str(out))[0] == 0
    code, report = compat(capsys, tmp_path / "base.shape", tmp_path / "today.shape")
    assert code == 5 and not report["compatible"]
    assert [(i["kind"], i["path"]) for i in report["issues"]] == [(kind, path)]


def test_delta_version_and_as_of_are_honoured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    delta = tmp_path / "feed_delta"
    write_delta(delta, orders())
    write_delta(delta, orders(qty=None))  # version 1 drops a column
    latest = model(capsys, str(delta))
    v0 = model(capsys, str(delta), "--version", "0")
    assert "qty" not in latest["columns"] and "qty" in v0["columns"]
    assert model(capsys, str(delta), "--version", "1") == latest
    assert model(capsys, str(delta), "--as-of", "2999-01-01T00:00:00Z") == latest
    assert main(["capture", str(delta), "--version", "9"]) == 2


def test_version_needs_a_delta_table(tmp_path: Path, feeds: dict[str, Path]) -> None:
    assert main(["capture", str(feeds["parquet"]), "--version", "0"]) == 2


def test_an_empty_parquet_feed_keeps_its_columns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "empty.parquet"
    pq.write_table(orders().slice(0, 0), path)
    doc = model(capsys, str(path))
    assert doc["rows"] == 0 and set(doc["columns"]) == {"id", "status", "amount", "qty"}

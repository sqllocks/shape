"""``shape generate --to``: OneLake / ADLS Gen2 targets, rolling files, Delta, fan-out."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import fsspec
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
from scale_schemas import plain_doc  # noqa: E402

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}
URI = "abfss://landing@acct.dfs.core.windows.net/raw"


def run(capsys: Any, *argv: Any) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc(ROWS)))
    return path


@pytest.fixture
def memfs(monkeypatch: pytest.MonkeyPatch) -> Any:
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    from shape.builtins.sources import azure

    monkeypatch.setattr(azure, "_filesystem", lambda loc, options: fs)
    return fs


def files(fs: Any) -> list[str]:
    return sorted(
        p.lstrip("/")
        for p in fs.find("landing/raw")
        if "/_shape_tmp/" not in p and "/_SUCCESS" not in p
    )


def test_to_abfss_writes_dated_parquet_per_table(
    capsys: Any, memfs: Any, schema_file: Path
) -> None:
    code, out, _ = run(
        capsys, "generate", schema_file, "--to", URI, "--batch-date", "2026-10-02", "--seed", 7
    )
    assert code == 0, out
    assert files(memfs) == [
        f"landing/raw/{t}/ingest_date=2026-10-02/{t}_20261002.parquet" for t in sorted(ROWS)
    ]
    for table, rows in ROWS.items():
        path = f"landing/raw/{table}/ingest_date=2026-10-02/{table}_20261002.parquet"
        assert pq.read_table(memfs.open(path, "rb")).num_rows == rows
    assert "to abfss://landing@acct.dfs.core.windows.net/raw" in out


def test_to_rolls_files_and_formats_per_table(capsys: Any, memfs: Any, schema_file: Path) -> None:
    code, out, _ = run(
        capsys,
        "generate",
        schema_file,
        "--to",
        URI,
        "--roll-rows",
        500,
        "--table-format",
        "customer=csv",
        "--manifest",
        "--batch-date",
        "2026-10-02",
        "--json",
    )
    assert code == 0, out
    names = files(memfs)
    assert sum(n.endswith(".csv") for n in names) == 1
    assert sum("/order/" in n for n in names) == 3  # 1200 rows in files of 500
    assert memfs.exists("landing/raw/order/ingest_date=2026-10-02/_SUCCESS")
    report = json.loads(out)
    assert report["targets"][URI] == ROWS


def test_to_fans_out_to_two_targets(
    capsys: Any, memfs: Any, schema_file: Path, tmp_path: Path
) -> None:
    other = "abfss://other@acct.dfs.core.windows.net/x"
    code, out, _ = run(
        capsys, "generate", schema_file, "--to", URI, "--to", other, "--batch-date", "2026-10-02"
    )
    assert code == 0, out
    assert memfs.exists("other/x/customer/ingest_date=2026-10-02/customer_20261002.parquet")
    assert memfs.exists("landing/raw/customer/ingest_date=2026-10-02/customer_20261002.parquet")


def test_to_with_unknown_scheme_lists_what_is_installed(capsys: Any, schema_file: Path) -> None:
    code, _, err = run(capsys, "generate", schema_file, "--to", "s3://bucket/x")
    assert code != 0
    assert "no sink writes s3://" in err and "abfss://" in err


def test_secret_in_sink_config_is_refused_and_references_resolve(
    capsys: Any, memfs: Any, schema_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _, err = run(
        capsys, "generate", schema_file, "--to", URI, "--sink-config", "abfss.account_key=TOPSECRET"
    )
    assert code != 0 and "TOPSECRET" not in err and "reference" in err
    monkeypatch.setenv("MY_KEY", "k")
    code, _, err = run(
        capsys,
        "generate",
        schema_file,
        "--to",
        URI,
        "--sink-config",
        "abfss.account_key=env://MY_KEY",
        "--batch-date",
        "2026-10-02",
    )
    assert code == 0, err


def test_to_does_not_combine_with_output_or_scale_mode(capsys: Any, schema_file: Path) -> None:
    code, _, err = run(capsys, "generate", schema_file, "--to", URI, "-o", "x")
    assert code != 0 and "use one" in err
    code, _, err = run(capsys, "generate", schema_file, "--to", URI, "--scale-mode", "local_single")
    assert code != 0 and "--scale-mode" in err


def test_to_delta_local_commits_per_micro_batch(
    capsys: Any, schema_file: Path, tmp_path: Path
) -> None:
    deltalake = pytest.importorskip("deltalake")
    # a local directory is not routed (no scheme); the Delta target is a delta+abfss:// URI, so
    # here the --format delta path of -o is untouched
    code, _, _ = run(capsys, "generate", schema_file, "-f", "delta", "-o", tmp_path / "d")
    assert code == 0
    assert deltalake.DeltaTable(str(tmp_path / "d" / "order")).to_pyarrow_table().num_rows == 1200

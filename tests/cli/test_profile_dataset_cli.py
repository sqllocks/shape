"""PF-06b: ``shape profile`` of a folder, and the contract check on the result, through the CLI.

PF-06's damage case: a domain generated to a folder, one table cut short. Profiled as one table
the multi-table contract passed vacuously; it must now fail loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains", reason="the domains plugin supplies the retail domain")

DOMAIN = "retail"


def _generate(tmp_path: Path, *, damage: bool) -> tuple[Path, Path]:
    from shape.integrations.fabric import generation

    data = tmp_path / "data"
    assert main(["generate", DOMAIN, "--scale", "small", "--format", "parquet", "-o", str(data)]) == 0
    planned = generation.plan_row_counts(DOMAIN, "small")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(generation.contract_for_domain(DOMAIN, planned)))
    if damage:
        customer = pq.read_table(data / "customer.parquet")
        pq.write_table(customer.slice(0, customer.num_rows - 100), data / "customer.parquet")
    return data, contract


def _check(tmp_path: Path, data: Path, contract: Path, capsys: pytest.CaptureFixture[str]):
    profile = tmp_path / "p.shape"
    assert main(["profile", str(data), "--dataset", "-o", str(profile)]) == 0
    capsys.readouterr()
    code = main(["check", str(profile), str(contract)])
    return code, json.loads(capsys.readouterr().out)


def test_a_folder_profiled_as_a_dataset_passes_the_domain_contract(tmp_path, capsys):
    data, contract = _generate(tmp_path, damage=False)
    code, result = _check(tmp_path, data, contract, capsys)
    assert code == 0 and result["passed"] is True, result["violations"][:5]


def test_a_damaged_table_fails_through_the_cli_path(tmp_path, capsys):
    data, contract = _generate(tmp_path, damage=True)
    code, result = _check(tmp_path, data, contract, capsys)
    assert code == 1 and result["passed"] is False
    assert "customer:row_count.min" in {v["rule"] for v in result["violations"]}


def test_a_dataset_profile_names_one_table_per_file(tmp_path):
    data, _ = _generate(tmp_path, damage=False)
    import shape

    out = tmp_path / "p.shape"
    assert main(["profile", str(data), "--dataset", "-o", str(out)]) == 0
    prof = shape.load(str(out))
    assert prof.is_dataset
    assert set(prof.tables) == {p.stem for p in data.glob("*.parquet")}


def test_a_folder_of_different_tables_is_refused_without_dataset(tmp_path, capsys):
    data, _ = _generate(tmp_path, damage=False)
    code = main(["profile", str(data), "-o", str(tmp_path / "p.shape")])
    err = capsys.readouterr().err
    assert code == 2 and "--dataset" in err and "do not share their columns" in err
    assert not (tmp_path / "p.shape").exists()


def test_a_multi_table_contract_against_a_one_table_profile_exits_2(tmp_path, capsys):
    data, contract = _generate(tmp_path, damage=True)
    profile = tmp_path / "one.shape"
    assert main(["profile", str(data / "customer.parquet"), "-o", str(profile)]) == 0
    capsys.readouterr()
    code = main(["check", str(profile), str(contract)])
    assert code == 2
    assert "single table" in capsys.readouterr().err


def test_the_partitions_of_one_table_are_still_one_table(tmp_path):
    import shape

    folder = tmp_path / "events"
    folder.mkdir()
    for i in range(3):
        (folder / f"part-{i}.csv").write_text("id,v\n" + "\n".join(f"{i}{j},{j}" for j in range(5)))
    out = tmp_path / "events.shape"
    assert main(["profile", str(folder), "-o", str(out)]) == 0
    prof = shape.load(str(out))
    assert not prof.is_dataset and prof.tables["events"]["row_count"] == 15


def test_dataset_needs_a_folder_and_unique_names(tmp_path, capsys):
    one = tmp_path / "t.csv"
    one.write_text("a\n1\n")
    assert main(["profile", str(one), "--dataset", "-o", str(tmp_path / "x.shape")]) == 2
    assert "needs a folder" in capsys.readouterr().err
    clash = tmp_path / "clash"
    clash.mkdir()
    (clash / "t.csv").write_text("a\n1\n")
    (clash / "t.parquet").write_bytes(b"")
    assert main(["profile", str(clash), "--dataset", "-o", str(tmp_path / "x.shape")]) == 2
    assert "both be the table 't'" in capsys.readouterr().err

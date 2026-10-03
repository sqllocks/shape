"""W3-09 (#72): ``shape resolve`` (synth, run) end to end through the CLI."""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

from shape.cli.main import main
from tests.resolve.test_synth_metrics import people


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "people.csv"
    pacsv.write_csv(people(300), path)
    return path


def run_cli(*args: str) -> int:
    return main(list(args))


def test_synth_then_run_scores_against_the_written_truth(tmp_path, source, capsys) -> None:
    dup, truth = tmp_path / "dup.csv", tmp_path / "truth.json"
    assert (
        run_cli(
            "resolve",
            "synth",
            str(source),
            "-o",
            str(dup),
            "--truth",
            str(truth),
            "--rate",
            "0.25",
            "--fuzz",
            "0.5",
            "--seed",
            "21",
            "--id-column",
            "id",
        )
        == 0
    )
    capsys.readouterr()
    golden, clusters, report = (
        tmp_path / "golden.csv",
        tmp_path / "clusters.json",
        tmp_path / "rep.json",
    )
    code = run_cli(
        "resolve",
        "run",
        str(dup),
        "--block",
        "name:ngram:5",
        "--block",
        "name:phonetic",
        "--match",
        "name:text:3",
        "--match",
        "city:exact:0.5",
        "--match",
        "income:numeric:1:0.05:relative",
        "--match",
        "born:date:1:5",
        "--threshold",
        "0.85",
        "--truth",
        str(truth),
        "--golden",
        str(golden),
        "--clusters",
        str(clusters),
        "--report",
        str(report),
        "--json",
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["metrics"]["f1"] >= 0.9 and out["metrics"]["precision"] >= 0.95
    rep = json.loads(report.read_text())
    assert rep["format"] == "shape-resolve-report" and rep["version"] == 1
    assert json.loads(clusters.read_text())["format"] == "shape-resolve-clusters"
    assert pacsv.read_csv(golden).num_rows == out["clusters"] < pacsv.read_csv(dup).num_rows


def test_synth_is_deterministic_for_a_seed(tmp_path, source) -> None:
    for name in ("a", "b"):
        assert (
            run_cli(
                "resolve",
                "synth",
                str(source),
                "-o",
                str(tmp_path / f"{name}.csv"),
                "--truth",
                str(tmp_path / f"{name}.json"),
                "--seed",
                "5",
            )
            == 0
        )
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_run_with_a_config_file_and_bad_input(tmp_path, source) -> None:
    from tests.resolve.test_synth_metrics import CONFIG

    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(CONFIG.to_dict()))
    assert run_cli("resolve", "run", str(source), "--config", str(cfg)) == 0
    assert run_cli("resolve", "run", str(source)) == 2  # nothing to block or match on
    assert run_cli("resolve", "run", str(tmp_path / "missing.csv"), "--config", str(cfg)) == 2
    assert (
        run_cli("resolve", "run", str(source), "--block", "name:bogus", "--match", "name:exact")
        == 2
    )
    assert run_cli("resolve", "run", str(source), "--config", str(cfg), "--survive", "name") == 2


def test_survivorship_flag(tmp_path, source) -> None:
    dup = tmp_path / "d.csv"
    assert (
        run_cli("resolve", "synth", str(source), "-o", str(dup), "--seed", "2", "--rate", "0.3")
        == 0
    )
    golden = tmp_path / "g.csv"
    assert (
        run_cli(
            "resolve",
            "run",
            str(dup),
            "--block",
            "name:phonetic",
            "--match",
            "name:jaro_winkler",
            "--survive",
            "name=longest",
            "--survive",
            "income=max",
            "--golden",
            str(golden),
        )
        == 0
    )
    assert isinstance(pacsv.read_csv(golden), pa.Table)

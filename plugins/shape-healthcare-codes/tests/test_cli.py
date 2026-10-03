import json
from pathlib import Path

import pytest
from fixtures import PCS_R1, order_text, zip_bytes
from shape_healthcare_codes import store
from shape_healthcare_codes.cli import HealthcareCodesCommand
from shape_healthcare_codes.provenance import all_assets

from shape.plugins import kit


def _run(argv: list[str]) -> int:
    import argparse

    cmd = HealthcareCodesCommand()
    p = argparse.ArgumentParser()
    cmd.configure(p)
    return cmd.run(p.parse_args(argv))


def test_kit_conformance():
    kit.check_command(HealthcareCodesCommand(), ["list"])


def test_list_json_covers_every_asset(capsys: pytest.CaptureFixture[str]):
    assert _run(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["asset"] for r in rows} == set(all_assets())
    by = {r["asset"]: r for r in rows}
    assert by["icd10cm"]["built"] == "shipped" and by["icd10cm"]["fetchable"] is True
    assert by["cpt"]["mode"] == "byo" and by["cpt"]["fetchable"] is False
    assert _run(["list"]) == 0


def test_notices_prints_the_licence_record(capsys: pytest.CaptureFixture[str]):
    assert _run(["notices", "nucc_taxonomy"]) == 0
    out = capsys.readouterr().out
    assert "American Medical Association" in out and "[byo]" in out
    assert _run(["notices", "nope"]) == 2
    assert _run(["notices"]) == 0


def test_fetch_from_a_file_the_user_has_and_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    z = tmp_path / "pos.html"
    from fixtures import POS_HTML

    z.write_text(POS_HTML, encoding="utf-8")
    d = tmp_path / "data"
    assert _run(["fetch", "pos", "--dir", str(d), "--file", f"html={z}"]) == 0
    assert "built" in capsys.readouterr().out
    assert len(store.load("pos", d)) == 3
    assert _run(["verify", "--dir", str(d)]) == 0
    assert "pos.arrow: ok" in capsys.readouterr().out
    (d / "pos.json").write_text(json.dumps({"rows": 99, "bytes": 1}), encoding="utf-8")
    assert _run(["verify", "--dir", str(d)]) == 1


def test_fetch_refuses_a_byo_asset_and_needs_a_name():
    with pytest.raises(SystemExit, match="cannot be fetched"):
        _run(["fetch", "cpt"])
    with pytest.raises(SystemExit, match="name an asset"):
        _run(["fetch"])
    with pytest.raises(SystemExit, match="one asset"):
        _run(["fetch", "pos", "ndc", "--file", "zip=/x"])
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        _run(["fetch", "pos", "--file", "bad"])


def test_byo_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    f = tmp_path / "cpt.csv"
    f.write_text("CPT,Descriptor\n10021,Invented\n", encoding="utf-8")
    d = tmp_path / "d"
    assert (
        _run(
            [
                "byo",
                "cpt",
                str(f),
                "--map",
                "code=CPT",
                "--map",
                "long_desc=Descriptor",
                "--dir",
                str(d),
            ]
        )
        == 0
    )
    assert "10021" in store.load("cpt", d)
    assert _run(["byo", "icd10cm", str(f), "--dir", str(d)]) == 2
    assert "not a bring-your-own system" in capsys.readouterr().out
    assert _run(["byo", "cpt", str(tmp_path / "missing.csv")]) == 2


def test_verify_with_nothing_built(tmp_path: Path):
    assert _run(["verify", "--dir", str(tmp_path / "empty")]) == 0
    _ = (order_text(PCS_R1), zip_bytes({}))

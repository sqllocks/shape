"""HUNT2-scenario: regression tests for `shape pack` defects found in the second audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains")
pytest.importorskip("yaml")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "packs"
BAD_PACK = (
    "id: b\nkind: file_drop\ndomain: retail\nfile_drop: {formats: [csv], entities: [ghost]}\n"
)


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- #665: pack run --json has one shape whatever file failed -------------------------------


def test_665_run_json_of_an_invalid_pack_and_of_an_invalid_spec_have_the_same_keys(
    capsys, tmp_path
):
    (tmp_path / "bad.yaml").write_text(BAD_PACK)
    (tmp_path / "bad.gsl.yaml").write_text(
        "version: 1\nname: n\nschema: {type: domain, domain: retail}\n"
        "scenario: {pack: bad.yaml, scale: small, seed: 1}\n"
    )
    code_p, out_p, _ = run(
        capsys, "pack", "run", tmp_path / "bad.yaml", "--json", "-o", tmp_path / "o"
    )
    code_s, out_s, _ = run(
        capsys, "pack", "run", tmp_path / "bad.gsl.yaml", "--json", "-o", tmp_path / "o"
    )
    pack_doc, spec_doc = json.loads(out_p), json.loads(out_s)
    assert code_p == code_s == 1
    assert pack_doc["success"] is False and spec_doc["success"] is False
    assert set(pack_doc) <= set(spec_doc)
    assert any("ghost" in e for e in spec_doc["errors"])
    assert spec_doc["manifest"] is None and spec_doc["files"] == []


def test_665_a_valid_spec_keeps_its_json(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "retail_basic.gsl.yaml", "--json", "-o", tmp_path / "o"
    )
    doc = json.loads(out)
    assert code == 0 and doc["success"] is True and doc["manifest"]["run_id"]


# ---- #666: pack list survives a name that is not a readable file ----------------------------


def test_666_list_skips_a_folder_named_like_a_pack_and_lists_the_rest(capsys, tmp_path):
    (tmp_path / "retail").mkdir()
    (tmp_path / "retail" / "v1.yaml").mkdir()
    (tmp_path / "retail" / "ok.yaml").write_text(
        "id: ok\nkind: file_drop\ndomain: retail\nfile_drop: {formats: [csv]}\n"
    )
    code, out, err = run(capsys, "pack", "list", tmp_path, "--json")
    assert code == 0, err
    assert [r["id"] for r in json.loads(out)["payload"] if r["kind"] != "invalid"] == ["ok"]


def test_666_list_reports_a_broken_link_as_invalid(capsys, tmp_path):
    (tmp_path / "gone.yaml").symlink_to(tmp_path / "nowhere.yaml")
    code, out, err = run(capsys, "pack", "list", tmp_path, "--json")
    assert code == 0, err
    assert [(r["id"], r["kind"]) for r in json.loads(out)["payload"]] == [("gone", "invalid")]


# ---- #705: validate takes the domain --domain names, for a spec too -------------------------


def test_705_validate_of_a_spec_refuses_a_domain_that_is_not_installed(capsys):
    code, _, err = run(
        capsys, "pack", "validate", FIXTURES / "retail_basic.gsl.yaml", "--domain", "nonexistent"
    )
    assert code == 2 and "no domain named 'nonexistent'" in err


def test_705_validate_of_a_spec_checks_the_pack_against_the_given_domain(capsys):
    code, out, _ = run(
        capsys, "pack", "validate", FIXTURES / "retail_basic.gsl.yaml", "--domain", "healthcare"
    )
    assert code == 1 and "Entity" in out and "not found in domain schema" in out


def test_705_validate_of_a_spec_without_domain_is_unchanged(capsys):
    code, out, _ = run(capsys, "pack", "validate", FIXTURES / "retail_basic.gsl.yaml")
    assert code == 0 and "PASS" in out


def test_705_run_checks_the_spec_against_the_domain_it_will_run(capsys, tmp_path):
    code, out, _ = run(
        capsys, "pack", "run", FIXTURES / "retail_basic.gsl.yaml", "--domain", "healthcare",
        "-o", tmp_path / "o",
    )  # fmt: skip
    assert code == 1 and "not found in domain schema" in out

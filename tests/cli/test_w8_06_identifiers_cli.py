"""W8-06 (#766) items 1 to 3 outside the engine: the run switch on ``shape.generate``, ``shape
generate`` (and ``composite``, ``--from``, ``--scale-mode``), ``shape demo run`` and ``shape pack
run`` (with a scenario spec's ``scenario.identifiers``); unknown values refused; the one-line
notice on standard error; the mode in the run manifest, a manifest without it read as reserved,
and a replay that uses it."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import shape
from shape.cli.main import main
from shape.generation.identifiers import REALISTIC_NOTICE
from shape.generation.schema import GenSchema
from shape.repro import dataset_id
from shape.scenario import ManifestBuilder

RESERVED_HOSTS = {"example.com", "example.org", "example.net"}


def schema_doc(**top: Any) -> dict[str, Any]:
    customer = {
        "name": "customer",
        "primary_key": ["customer_id"],
        "columns": {
            "customer_id": {
                "name": "customer_id",
                "type": "integer",
                "generator": {"strategy": "sequence"},
            },
            "email": {
                "name": "email",
                "type": "string",
                "generator": {"strategy": "native", "provider": "email"},
            },
            "ssn": {
                "name": "ssn",
                "type": "string",
                "generator": {"strategy": "native", "provider": "ssn"},
            },
        },
    }
    order = {
        "name": "order",
        "primary_key": ["order_id"],
        "columns": {
            "order_id": {
                "name": "order_id",
                "type": "integer",
                "generator": {"strategy": "sequence"},
            },
            "customer_id": {
                "name": "customer_id",
                "type": "integer",
                "generator": {"strategy": "foreign_key", "ref": "customer.customer_id"},
            },
        },
    }
    return {
        "schema_version": 1,
        "model": {"name": "ids", "domain": "ids", "seed": 3},
        "tables": {"customer": customer, "order": order},
        "relationships": [
            {
                "name": "customer_orders",
                "parent": "customer",
                "child": "order",
                "parent_columns": ["customer_id"],
                "child_columns": ["customer_id"],
            }
        ],
        "generation": {"scale": "s", "scales": {"s": {"customer": 400, "order": 50}}},
        **top,
    }


def write_schema(tmp_path: Path, **top: Any) -> Path:
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(schema_doc(**top)), encoding="utf-8")
    return path


def hosts(emails: list[str]) -> set[str]:
    return {e.split("@")[1] for e in emails}


def emails_of(folder: Path) -> list[str]:
    with open(folder / "customer.csv", newline="", encoding="utf-8") as handle:
        return [row["email"] for row in csv.DictReader(handle)]


def generate_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *flags: str, **top: Any
) -> tuple[list[str], str]:
    out = tmp_path / ("out" + "_".join(flags).replace("-", ""))
    code = main(
        ["generate", str(write_schema(tmp_path, **top)), "-f", "csv", "-o", str(out), *flags]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return emails_of(out), captured.err


# ---- shape.generate ---------------------------------------------------------------------------


def test_the_api_default_is_reserved_and_says_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    result = shape.generate(schema_doc())
    assert hosts(result["customer"]["email"].to_pylist()) == RESERVED_HOSTS
    assert capsys.readouterr().err == ""


def test_the_api_switch_turns_realistic_on_and_says_so_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = shape.generate(schema_doc(), identifiers="realistic")
    assert not hosts(result["customer"]["email"].to_pylist()) & RESERVED_HOSTS
    assert capsys.readouterr().err.count(REALISTIC_NOTICE) == 1


def test_the_api_takes_the_schema_default_and_the_switch_wins(
    capsys: pytest.CaptureFixture[str],
) -> None:
    by_schema = shape.generate(schema_doc(identifiers="realistic"))
    by_switch = shape.generate(schema_doc(), identifiers="realistic")
    assert dataset_id(by_schema.tables) == dataset_id(by_switch.tables)
    assert capsys.readouterr().err.count(REALISTIC_NOTICE) == 2  # once per run
    reserved = shape.generate(schema_doc(identifiers="realistic"), identifiers="reserved")
    assert dataset_id(reserved.tables) == dataset_id(shape.generate(schema_doc()).tables)
    assert capsys.readouterr().err == ""


def test_the_api_switch_works_for_a_genschema_and_a_domain() -> None:
    schema = GenSchema.from_dict(schema_doc())
    a = shape.generate(schema, identifiers="realistic")
    assert not hosts(a["customer"]["email"].to_pylist()) & RESERVED_HOSTS
    assert schema.identifiers is None  # the caller's schema is not changed
    retail = shape.generate("retail", scale="small", seed=5)
    realistic = shape.generate("retail", scale="small", seed=5, identifiers="realistic")
    assert dataset_id(retail.tables) != dataset_id(realistic.tables)
    assert dataset_id(shape.generate("retail", scale="small", seed=5).tables) == dataset_id(
        retail.tables
    )


@pytest.mark.parametrize("bad", ["assignable", "REALISTIC", "", 0])
def test_the_api_refuses_an_unknown_value(bad: Any) -> None:
    with pytest.raises(ValueError, match="identifiers must be reserved or realistic"):
        shape.generate(schema_doc(), identifiers=bad)
    with pytest.raises(ValueError, match="identifiers must be reserved or realistic"):
        shape.generate({"rows": 5, "columns": {}}, 5, identifiers=bad)


def test_the_api_refuses_an_unknown_schema_value() -> None:
    from shape.generation.schema import GenSchemaError

    with pytest.raises(GenSchemaError, match="identifiers"):
        shape.generate(schema_doc(identifiers="assignable"))


# ---- shape generate ---------------------------------------------------------------------------


def test_generate_defaults_to_reserved_and_says_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emails, err = generate_csv(tmp_path, capsys)
    assert hosts(emails) == RESERVED_HOSTS
    assert REALISTIC_NOTICE not in err


def test_generate_identifiers_realistic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    emails, err = generate_csv(tmp_path, capsys, "--identifiers", "realistic")
    assert not hosts(emails) & RESERVED_HOSTS
    assert err.count(REALISTIC_NOTICE) == 1


def test_generate_follows_the_schema_and_the_flag_wins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    by_schema, err = generate_csv(tmp_path, capsys, identifiers="realistic")
    assert not hosts(by_schema) & RESERVED_HOSTS and err.count(REALISTIC_NOTICE) == 1
    flag, err = generate_csv(tmp_path, capsys, "--identifiers", "reserved", identifiers="realistic")
    assert hosts(flag) == RESERVED_HOSTS and REALISTIC_NOTICE not in err
    explicit, _ = generate_csv(tmp_path, capsys, "--identifiers", "realistic")
    assert by_schema == explicit


def test_generate_reserved_flag_is_the_default_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default, _ = generate_csv(tmp_path, capsys)
    assert generate_csv(tmp_path, capsys, "--identifiers", "reserved")[0] == default


@pytest.mark.parametrize("bad", ["assignable", "Realistic", ""])
def test_generate_refuses_an_unknown_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["generate", str(write_schema(tmp_path)), "--identifiers", bad])
    assert stop.value.code == 2
    err = capsys.readouterr().err
    # argparse quotes the choices on most Pythons, not on some 3.12 releases (3.12.14)
    assert "--identifiers" in err and re.search(r"choose from '?reserved'?, '?realistic'?", err)


def test_generate_summary_and_dry_run_take_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema = str(write_schema(tmp_path))
    assert main(["generate", schema, "--identifiers", "realistic", "--json"]) == 0
    assert capsys.readouterr().err.count(REALISTIC_NOTICE) == 1
    assert main(["generate", schema, "--identifiers", "realistic", "--dry-run"]) == 0
    assert REALISTIC_NOTICE not in capsys.readouterr().err  # a plan generates nothing


def test_composite_takes_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["composite", "retail+hr", "--scale", "small", "--json"]) == 0
    assert REALISTIC_NOTICE not in capsys.readouterr().err
    assert (
        main(["composite", "retail+hr", "--scale", "small", "--identifiers", "realistic", "--json"])
        == 0
    )
    assert capsys.readouterr().err.count(REALISTIC_NOTICE) == 1


def test_generate_scale_mode_takes_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyarrow.parquet as pq

    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    schema = str(write_schema(tmp_path))
    found = {}
    for mode in ("reserved", "realistic"):
        out = tmp_path / f"scale_{mode}"
        argv = ["generate", schema, "--scale-mode", "local_mp", "--sink", "parquet", "-o", str(out)]
        assert main([*argv, "--identifiers", mode]) == 0
        err = capsys.readouterr().err
        assert err.count(REALISTIC_NOTICE) == (mode == "realistic")
        files = sorted((out / "customer").glob("*.parquet")) or sorted(
            out.rglob("customer*.parquet")
        )
        found[mode] = [e for f in files for e in pq.read_table(f)["email"].to_pylist()]
    assert hosts(found["reserved"]) == RESERVED_HOSTS
    assert not hosts(found["realistic"]) & RESERVED_HOSTS


def test_the_scale_api_refuses_an_unknown_value() -> None:
    from shape.scale.api import normalize

    with pytest.raises(ValueError, match="identifiers must be reserved or realistic"):
        normalize({"domain": "retail", "identifiers": "assignable", "scale_mode": "local_single"})


# ---- shape demo run ---------------------------------------------------------------------------


def test_demo_params_check_the_value() -> None:
    from shape.demo.api import params_from
    from shape.demo.errors import DemoError

    assert params_from({"identifiers": "realistic"}).identifiers == "realistic"
    assert params_from({}).identifiers is None  # the scenario's own default: reserved
    with pytest.raises(DemoError, match="identifiers must be reserved or realistic"):
        params_from({"identifiers": "assignable"}).validate()


def test_demo_run_takes_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from shape.demo.manifest import DemoManifest

    monkeypatch.setenv("SHAPE_HOME", str(tmp_path / "home"))
    runs = {}
    for flags in ([], ["--identifiers", "realistic"]):
        argv = ["demo", "run", "retail", "--rows", "500", "--seed", "4", "--json", *flags]
        assert main(argv) == 0
        captured = capsys.readouterr()
        runs[bool(flags)] = (json.loads(captured.out), captured.err)
    (plain, plain_err), (real, real_err) = runs[False], runs[True]
    assert REALISTIC_NOTICE not in plain_err and real_err.count(REALISTIC_NOTICE) == 1
    sessions = tmp_path / "home" / "sessions"
    recorded = DemoManifest.load(real["session_id"], sessions).params
    assert recorded["identifiers"] == "realistic"
    default = DemoManifest.load(plain["session_id"], sessions).params
    assert "identifiers" not in default  # a session without it ran with the reserved default


def test_demo_run_refuses_an_unknown_value(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["demo", "run", "retail", "--identifiers", "assignable"])
    assert stop.value.code == 2
    assert "--identifiers" in capsys.readouterr().err


def test_the_demo_modes_pass_the_switch_to_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inference, streaming and seeding build their engines with the run's mode."""
    import shape.generation.engine as engine_module

    seen: list[str | None] = []
    real = engine_module.Engine.__init__

    def spy(self: Any, *args: Any, **kwargs: Any) -> None:
        seen.append(kwargs.get("identifiers"))
        real(self, *args, **kwargs)

    monkeypatch.setattr(engine_module.Engine, "__init__", spy)
    from shape.demo.api import demo_run
    from shape.demo.runtime import DemoRuntime

    result = demo_run(
        {"scenario": "retail", "rows": 300, "identifiers": "realistic"},
        runtime=DemoRuntime(out=None),
    )
    assert result["success"], result
    assert seen and "realistic" in seen


def test_the_demo_scenarios_keep_the_reserved_default() -> None:
    from shape.demo.catalog import get_catalog
    from shape.generation.domains import domain_names, load_domain

    for meta in get_catalog().list():
        for name in [getattr(meta, "domain", None), *(getattr(meta, "domains", None) or [])]:
            if name and name in domain_names():
                assert load_domain(name).schema.identifiers is None, name


# ---- shape pack run, the run manifest and replay ----------------------------------------------

PACK = """\
pack_version: 1
id: ids_pack
kind: file_drop
domain: ids
description: test
fabric_targets:
  lakehouse_files_root: Files/landing
file_drop:
  formats: [csv]
  entities: [customer, order]
validation:
  required_gates: [schema_conformance]
"""


def project(tmp_path: Path, scenario: dict[str, Any] | None = None, **top: Any) -> Path:
    write_schema(tmp_path, **top)
    (tmp_path / "pack.yaml").write_text(PACK, encoding="utf-8")
    spec = {
        "version": 1,
        "name": "ids_run",
        "schema": {"type": "schema_file", "path": "ids.json"},
        "scenario": {"pack": "pack.yaml", "scale": "s", "seed": 7, **(scenario or {})},
        "outputs": {"lakehouse": {"mode": "files_only", "formats": ["csv"]}},
        "validation": {"gates": ["schema_conformance"]},
    }
    path = tmp_path / "run.gsl.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def pack_run(
    spec: Path, out: Path, capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[dict[str, Any], str]:
    assert main(["pack", "run", str(spec), "-o", str(out), *flags]) == 0
    err = capsys.readouterr().err
    path = next(out.glob("*_manifest.json"))
    manifest: dict[str, Any] = json.loads(path.read_text("utf-8"))
    manifest["_path"] = str(path)
    return manifest, err


def test_pack_run_records_reserved_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, err = pack_run(project(tmp_path), tmp_path / "out", capsys)
    assert manifest["reproducibility"]["identifiers"] == "reserved"
    assert REALISTIC_NOTICE not in err
    assert ManifestBuilder.from_file(manifest["_path"]).identifiers == "reserved"


@pytest.mark.parametrize(
    ("flags", "scenario", "top", "want"),
    [
        (["--identifiers", "realistic"], {}, {}, "realistic"),
        ([], {"identifiers": "realistic"}, {}, "realistic"),
        ([], {}, {"identifiers": "realistic"}, "realistic"),
        (["--identifiers", "reserved"], {"identifiers": "realistic"}, {}, "reserved"),
        ([], {"identifiers": "reserved"}, {"identifiers": "realistic"}, "reserved"),
    ],
)
def test_pack_run_records_the_mode_it_used(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    scenario: dict[str, Any],
    top: dict[str, Any],
    want: str,
) -> None:
    manifest, err = pack_run(project(tmp_path, scenario, **top), tmp_path / "out", capsys, *flags)
    assert manifest["reproducibility"]["identifiers"] == want
    assert err.count(REALISTIC_NOTICE) == (want == "realistic")
    emails = [
        row["email"]
        for f in (tmp_path / "out").rglob("customer*.csv")
        for row in csv.DictReader(open(f, newline="", encoding="utf-8"))
    ]
    assert emails
    assert (hosts(emails) == RESERVED_HOSTS) == (want == "reserved")


def test_a_spec_with_an_unknown_value_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path, {"identifiers": "assignable"})
    assert main(["pack", "run", str(spec), "-o", str(tmp_path / "out")]) == 2
    err = capsys.readouterr().err
    assert "scenario.identifiers must be reserved or realistic, not 'assignable'" in err
    assert not (tmp_path / "out").exists()  # refused before anything was generated


def test_pack_run_refuses_an_unknown_flag_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["pack", "run", str(project(tmp_path)), "--identifiers", "assignable"])
    assert stop.value.code == 2


@pytest.mark.parametrize("mode", ["reserved", "realistic"])
def test_a_replay_regenerates_with_the_recorded_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    spec = project(tmp_path)
    manifest, _ = pack_run(spec, tmp_path / "out", capsys, "--identifiers", mode)
    assert main(["pack", "replay", manifest["_path"], str(spec), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["match"] is True and result["actual"] == manifest["dataset_id"]


def test_a_manifest_without_the_field_reads_and_replays_as_reserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest written before W8-06 has no ``identifiers``: it was a reserved run."""
    spec = project(tmp_path)
    manifest, _ = pack_run(spec, tmp_path / "out", capsys)
    path = Path(manifest["_path"])
    raw = json.loads(path.read_text("utf-8"))
    del raw["reproducibility"]["identifiers"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = ManifestBuilder.from_file(path)
    assert loaded.identifiers == "reserved"
    assert "identifiers" not in loaded.to_dict()["reproducibility"]  # written back as read
    assert main(["pack", "replay", str(path), str(spec), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["match"] is True


def test_a_replay_of_a_realistic_run_fails_as_reserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recorded mode matters: the same manifest read as reserved does not match."""
    spec = project(tmp_path)
    manifest, _ = pack_run(spec, tmp_path / "out", capsys, "--identifiers", "realistic")
    path = Path(manifest["_path"])
    raw = json.loads(path.read_text("utf-8"))
    raw["reproducibility"]["identifiers"] = "reserved"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert main(["pack", "replay", str(path), str(spec), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["match"] is False


def test_a_manifest_with_an_unknown_mode_cannot_be_replayed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = project(tmp_path)
    manifest, _ = pack_run(spec, tmp_path / "out", capsys)
    path = Path(manifest["_path"])
    raw = json.loads(path.read_text("utf-8"))
    raw["reproducibility"]["identifiers"] = "assignable"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert main(["pack", "replay", str(path), str(spec)]) == 2
    err = capsys.readouterr().err
    assert "the manifest's reproducibility.identifiers must be reserved or realistic" in err


def test_the_runner_api_takes_the_switch(tmp_path: Path) -> None:
    from shape.scenario.loader import PackLoader
    from shape.scenario.runner import PackRunner

    (tmp_path / "pack.yaml").write_text(PACK, encoding="utf-8")
    pack = PackLoader().load(tmp_path / "pack.yaml")
    schema = GenSchema.from_dict(schema_doc())
    result = PackRunner().run(pack, schema, "s", 7, tmp_path / "a", identifiers="realistic")
    assert result.manifest is not None and result.manifest.identifiers == "realistic"
    plain = PackRunner().run(pack, schema, "s", 7, tmp_path / "b")
    assert plain.manifest is not None and plain.manifest.identifiers == "reserved"
    assert plain.manifest.dataset_id != result.manifest.dataset_id

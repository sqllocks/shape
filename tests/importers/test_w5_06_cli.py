"""W5-06 items 1 and 3: the ``shape import-schema`` command, the report and ``--strict``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from import_fixtures import FIXTURES, generate

from shape import schemacheck
from shape.cli.main import main
from shape.generation.spec_edit import SpecDocument
from shape.generation.spec_schema import published_schema

COMPAT = Path(__file__).parent / "report_compat"


def run(capsys: pytest.CaptureFixture[str], *argv: Any) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_command_writes_a_valid_spec_and_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, report = tmp_path / "order.gen.json", tmp_path / "report.json"
    code, stdout, _ = run(
        capsys, "import-schema", FIXTURES / "order.schema.json", "-o", out, "--report", report
    )
    assert code == 0 and "Tables: 5" in stdout
    spec = json.loads(out.read_text())
    assert schemacheck.problems(spec, published_schema()) == []
    assert SpecDocument.load(out).validate() == []
    generate(SpecDocument.load(out))
    doc = json.loads(report.read_text())
    assert doc["format"] == "shape-import-report" and doc["version"] == 1
    assert doc["summary"] == {
        "imported": len(doc["imported"]),
        "not_imported": len(doc["not_imported"]),
    }
    assert doc["source"]["format"] == "jsonschema"


@pytest.mark.parametrize(
    ("source", "fmt"),
    [
        ("order.schema.json", "jsonschema"),
        ("petstore.openapi.json", "openapi"),
        ("trip.avsc", "avro"),
        ("proto/shop.proto", "protobuf"),
        ("sales_model", "tmdl"),
    ],
)
def test_every_format_is_inferred_and_gives_a_valid_spec(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, fmt: str
) -> None:
    out = tmp_path / "x.gen.json"
    report = tmp_path / "x.report.json"
    code, _, _ = run(capsys, "import-schema", FIXTURES / source, "-o", out, "--report", report)
    assert code == 0
    assert json.loads(report.read_text())["source"]["format"] == fmt
    assert SpecDocument.load(out).validate() == []
    generate(SpecDocument.load(out))


def test_strict_exits_1_and_writes_no_spec_but_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, report = tmp_path / "r.gen.json", tmp_path / "r.json"
    code, _, err = run(
        capsys,
        "import-schema",
        FIXTURES / "refused.schema.json",
        "-o",
        out,
        "--report",
        report,
        "--strict",
    )
    assert code == 1 and "--strict" in err and "no spec written" in err
    assert not out.exists()
    assert json.loads(report.read_text())["not_imported"]


def test_strict_succeeds_when_everything_is_imported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "ok.json"
    f.write_text(
        '{"type":"object","required":["id"],"properties":{"id":{"type":"integer"},'
        '"name":{"type":"string","maxLength":20}}}'
    )
    code, _, _ = run(capsys, "import-schema", f, "-o", tmp_path / "ok.gen.json", "--strict")
    assert code == 0 and (tmp_path / "ok.gen.json").exists()


def test_without_strict_the_not_imported_elements_are_printed_and_exit_is_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(
        capsys, "import-schema", FIXTURES / "refused.schema.json", "-o", tmp_path / "r.gen.json"
    )
    assert code == 0
    assert "oneOf with 3 non-null branches: imported as string" in err
    assert "not imported:" in out or "not imported" in out


@pytest.mark.parametrize(
    ("name", "text", "needle"),
    [
        ("plain.json", '{"a": 1}', "cannot tell the format"),
        (
            "both.json",
            '{"type":"record","fields":[],"properties":{}}',
            "could be avro or jsonschema",
        ),
        ("rows.txt", "x", "cannot tell the format of a .txt file"),
        ("old.json", '{"swagger":"2.0"}', "Swagger 2.0"),
    ],
)
def test_an_ambiguous_or_unknown_file_exits_2_asking_for_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, text: str, needle: str
) -> None:
    f = tmp_path / name
    f.write_text(text)
    code, _, err = run(capsys, "import-schema", f, "-o", tmp_path / "o.json")
    assert code == 2 and needle in err
    assert not (tmp_path / "o.json").exists()
    if "tell the format" in needle:
        assert "--from" in err


def test_an_explicit_from_overrides_the_inference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "plain.json"
    f.write_text('{"properties": {"id": {"type": "integer"}}}')
    code, _, _ = run(
        capsys, "import-schema", f, "--from", "jsonschema", "-o", tmp_path / "o.gen.json"
    )
    assert code == 0


def test_a_malformed_input_exits_2_with_file_line_and_element(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "bad.json"
    f.write_text('{"type":"object","properties":{\n"a":{"$ref":"#/$defs/missing"}}}')
    code, _, err = run(capsys, "import-schema", f, "-o", tmp_path / "o.json")
    assert code == 2
    assert f"{f}:2: #/properties/a: $ref '#/$defs/missing' does not exist" in err
    assert not (tmp_path / "o.json").exists()


def test_a_missing_file_and_an_unknown_format_are_input_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "import-schema", tmp_path / "nope.json", "-o", tmp_path / "o.json")
    assert code == 2 and "file not found" in err
    with pytest.raises(SystemExit) as info:
        main(["import-schema", "x", "--from", "xsd", "-o", "o"])
    assert info.value.code == 2


def test_output_is_deterministic_across_runs_and_hash_seeds(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from shape.importers import import_schema\n"
        "for name in sys.argv[1:]:\n"
        "    r = import_schema(name)\n"
        "    print(r.spec.dumps()); print(r.report.dumps())\n"
    )
    sources = [
        FIXTURES / "order.schema.json",
        FIXTURES / "petstore.openapi.json",
        FIXTURES / "trip.avsc",
        FIXTURES / "proto" / "shop.proto",
        FIXTURES / "sales_model",
    ]
    outputs = set()
    for seed in ("0", "1", "12345", "random"):
        done = subprocess.run(
            [sys.executable, "-c", code, *map(str, sources)],
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            timeout=120,
        )
        outputs.add(done.stdout)
    assert len(outputs) == 1


# ---- the persisted format: shape-import-report ----------------------------------------------


def _shape_of(value: Any) -> Any:
    """The keys of a report at every level (the lists by their first item)."""
    if isinstance(value, dict):
        return {k: _shape_of(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape_of(value[0])] if value else []
    return type(value).__name__


def test_report_format_declares_format_and_an_integer_version() -> None:
    from shape.importers import import_schema

    doc = import_schema(FIXTURES / "refused.schema.json").report.to_dict()
    assert doc["format"] == "shape-import-report"
    assert isinstance(doc["version"], int) and not isinstance(doc["version"], bool)
    assert doc["version"] == 1


def test_a_version_1_report_still_has_every_key_the_current_one_writes() -> None:
    """``report_compat/report-v1.json`` was written by the first release of the format. Later
    versions may add keys; no key may be removed or change type, and ``version`` stays 1 until a
    key is removed or changes meaning."""
    from shape.importers import import_schema

    frozen = json.loads((COMPAT / "report-v1.json").read_text())
    current = import_schema(FIXTURES / "refused.schema.json").report.to_dict()
    assert frozen["format"] == current["format"] and frozen["version"] == current["version"]

    def contains(old: Any, new: Any, path: str = "") -> None:
        if isinstance(old, dict):
            assert isinstance(new, dict), path
            for key, value in old.items():
                assert key in new, f"{path}/{key} was removed from the report"
                contains(value, new[key], f"{path}/{key}")
        elif isinstance(old, list):
            assert isinstance(new, list), path
            if old and new:
                contains(old[0], new[0], f"{path}[]")
        else:
            assert old == new, f"{path} changed type"

    contains(_shape_of(frozen), _shape_of(current))
    assert frozen["summary"]["imported"] == len(frozen["imported"])
    assert frozen["summary"]["not_imported"] == len(frozen["not_imported"])

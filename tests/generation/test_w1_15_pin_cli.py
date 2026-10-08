"""W1-15 (#92) deliverables 3 and 4: ``shape pin`` and the exit-2 error of ``shape generate``."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from versioning_fixtures import PROBE, probes, spec_doc

from shape.cli.main import main
from shape.generation import pinning
from shape.generation.spec_edit import SpecDocument

BAD_PIN = "pins v2probe at generator version 3; this Shape has versions 1 to 2 (upgrade Shape)"


@pytest.fixture(autouse=True)
def _probes() -> Iterator[None]:
    with probes():
        yield


def write(path: Path, doc: Any) -> Path:
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def read(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return out


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- shape pin ------------------------------------------------------------------------------


def test_pin_writes_the_current_version_of_everything_the_spec_uses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc())
    code, out, err = run(capsys, "pin", str(spec))
    assert code == 0 and err == ""
    assert read(spec)["generators"] == {PROBE: 2, "sequence": 1}
    assert f"pinned 2 generators in {spec}" in out


def test_pin_keeps_unknown_fields_and_the_order_of_keys(tmp_path: Path) -> None:
    doc = spec_doc(**{"x-owner": "team", "$comment": "hi", "x-nested": {"a": [1, 2]}})
    doc["tables"]["t"]["x-note"] = "table note"
    spec = write(tmp_path / "s.json", doc)
    assert main(["pin", str(spec)]) == 0
    after = read(spec)
    for key in ("x-owner", "$comment", "x-nested"):
        assert after[key] == doc[key]
    assert list(after)[:-1] == list(doc)  # the new key goes last, nothing moved
    assert after["tables"] == doc["tables"]  # including the x- key inside a table


def test_pin_never_moves_a_pin_that_is_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={PROBE: 1}))
    code, out, _ = run(capsys, "pin", str(spec))
    assert code == 0 and read(spec)["generators"] == {PROBE: 1, "sequence": 1}
    assert "sequence=1" in out and PROBE not in out


def test_pin_of_a_pinned_spec_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={PROBE: 2, "sequence": 1}))
    before = spec.read_bytes()
    code, out, _ = run(capsys, "pin", str(spec))
    assert code == 0 and spec.read_bytes() == before
    assert "already pins all 2 generators" in out


def test_pin_twice_is_the_same_as_once(tmp_path: Path) -> None:
    spec = write(tmp_path / "s.json", spec_doc())
    main(["pin", str(spec)])
    once = spec.read_bytes()
    main(["pin", str(spec)])
    assert spec.read_bytes() == once


def test_pin_to_an_output_file_leaves_the_spec_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc())
    before = spec.read_bytes()
    out_file = tmp_path / "pinned.json"
    assert run(capsys, "pin", str(spec), "-o", str(out_file))[0] == 0
    assert spec.read_bytes() == before and read(out_file)["generators"][PROBE] == 2
    # nothing to add still writes the copy
    again = tmp_path / "copy.json"
    assert run(capsys, "pin", str(out_file), "-o", str(again))[0] == 0
    assert read(again) == read(out_file)


def result(out: str) -> dict[str, Any]:
    """The result `shape pin --json` prints, without W1-14's shape-result envelope keys."""
    doc: dict[str, Any] = json.loads(out)
    assert doc.pop("format") == "shape-result" and doc.pop("command") == "pin"
    doc.pop("version")
    doc.pop("exit_code")
    return doc


def test_pin_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={"sequence": 1}))
    code, out, _ = run(capsys, "pin", str(spec), "--json")
    assert code == 0
    assert result(out) == {
        "spec": str(spec),
        "written": str(spec),
        "generators": {PROBE: 2, "sequence": 1},
        "added": {PROBE: 2},
        "unused": [],
    }
    code, out, _ = run(capsys, "pin", str(spec), "--json")
    assert result(out)["written"] is None and result(out)["added"] == {}


def test_the_pinned_spec_gives_the_same_data_as_the_unpinned_one(tmp_path: Path) -> None:
    from versioning_fixtures import schema

    from shape.generation.engine import Engine
    from shape.generation.schema import GenSchema
    from shape.repro import dataset_id

    spec = write(tmp_path / "s.json", spec_doc())
    main(["pin", str(spec)])
    pinned = GenSchema.from_dict(read(spec))
    assert dataset_id(Engine(pinned).generate().tables) == dataset_id(
        Engine(schema()).generate().tables
    )


def test_pin_uses_the_library_the_cli_uses(tmp_path: Path) -> None:
    doc = SpecDocument.from_dict(spec_doc())
    report = pinning.pin(doc)
    assert report.added == {PROBE: 2, "sequence": 1} and report.unpinned == []
    assert doc.generators == report.added


# ---- shape pin --check ----------------------------------------------------------------------


def test_check_exits_1_and_lists_the_names_that_are_not_pinned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={"sequence": 1}))
    before = spec.read_bytes()
    code, out, _ = run(capsys, "pin", str(spec), "--check")
    assert code == 1 and f"does not pin: {PROBE}" in out
    assert spec.read_bytes() == before  # --check writes nothing


def test_check_exits_0_when_everything_is_pinned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={PROBE: 1, "sequence": 1}))
    code, out, err = run(capsys, "pin", str(spec), "--check")
    assert code == 0 and err == "" and "pins all 2 generators" in out


def test_check_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = write(tmp_path / "s.json", spec_doc())
    code, out, _ = run(capsys, "pin", str(spec), "--check", "--json")
    assert code == 1
    assert result(out) == {
        "spec": str(spec),
        "pinned": False,
        "generators": {},
        "unpinned": ["sequence", PROBE],
        "unused": [],
    }


def test_check_exits_2_on_a_spec_that_is_not_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.json"
    syntax = tmp_path / "syntax.json"
    syntax.write_text("{", encoding="utf-8")
    wrong = write(tmp_path / "wrong.json", {"schema_version": 1, "model": {"name": "m"}})
    bad_map = write(tmp_path / "map.json", spec_doc(generators={PROBE: 0}))
    for path in (missing, syntax, wrong, bad_map):
        code, _, err = run(capsys, "pin", str(path), "--check")
        assert code == 2 and err.startswith("shape: error:"), path


def test_check_does_not_take_an_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = write(tmp_path / "s.json", spec_doc())
    code, _, err = run(capsys, "pin", str(spec), "--check", "-o", str(tmp_path / "o.json"))
    assert code == 2 and "does not combine with -o" in err and not (tmp_path / "o.json").exists()


# ---- errors ---------------------------------------------------------------------------------


def test_a_pin_to_a_version_this_shape_lacks_exits_2_with_the_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={PROBE: 3}))
    for argv in (["pin", str(spec)], ["pin", str(spec), "--check"]):
        code, _, err = run(capsys, *argv)
        assert code == 2
        assert err.strip() == f"shape: error: {spec} {BAD_PIN}"


def test_a_pin_to_a_name_the_spec_does_not_use_is_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={PROBE: 2, "sequence": 1, "zipf": 1}))
    code, out, err = run(capsys, "pin", str(spec))
    assert code == 0 and f"shape: warning: {spec} pins zipf, which it does not use" in err
    assert read(spec)["generators"]["zipf"] == 1  # kept: the pin is the author's
    code, out, err = run(capsys, "pin", str(spec), "--check", "--json")
    assert code == 0 and json.loads(out)["unused"] == ["zipf"] and "zipf" in err


def test_generate_with_a_bad_pin_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = write(tmp_path / "s.json", spec_doc(generators={PROBE: 3}))
    for argv in (
        ["generate", str(spec), "--dry-run"],
        ["generate", str(spec), "--format", "csv", "-o", str(tmp_path / "out")],
    ):
        code, _, err = run(capsys, *argv)
        assert code == 2 and err.strip() == f"shape: error: {spec} {BAD_PIN}"
    assert not (tmp_path / "out").exists()


def test_generate_honours_the_pins(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def make(name: str, **extra: Any) -> list[str]:
        spec = write(tmp_path / f"{name}.json", spec_doc(rows=5, **extra))
        out = tmp_path / name
        assert main(["generate", str(spec), "--format", "csv", "-o", str(out)]) == 0
        return (out / "t.csv").read_text("utf-8").split()

    capsys.readouterr()
    latest = make("latest")
    one = make("one", generators={PROBE: 1})
    assert latest != one and one[-1] == "5,4" and latest[-1] == "5,40"
    assert make("two", generators={PROBE: 2}) == latest

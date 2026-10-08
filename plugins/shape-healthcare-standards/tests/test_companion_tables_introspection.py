"""``companion_tables(sink_name)`` and the documentation table generated from it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import shape_healthcare_standards as pkg
from shape_healthcare_standards import companion_tables, contract
from shape_healthcare_standards.companions import SINK_NAMES, markdown_table

DOC = Path(__file__).parents[3] / "docs" / "plugins" / "healthcare-standards.md"
ENTRY_POINTS = (
    "x12-837p",
    "x12-837i",
    "x12-835",
    "x12-834",
    "x12-277ca",
    "fhir-ndjson",
    "fhir-bundle",
    "omop",
    "ncpdp",
)


def test_exported_from_the_package() -> None:
    assert pkg.companion_tables is companion_tables
    assert "companion_tables" in pkg.__all__


@pytest.mark.parametrize("name", SINK_NAMES)
def test_shape_and_contract_names(name: str) -> None:
    result = companion_tables(name)
    assert set(result) == {"required", "optional"}
    for key in ("required", "optional"):
        assert isinstance(result[key], list)
        assert all(t in contract.CONTRACT for t in result[key])
        assert result[key] == [t for t in contract.TABLE_NAMES if t in result[key]]
    assert not set(result["required"]) & set(result["optional"])


def test_a_call_returns_a_fresh_dict() -> None:
    companion_tables("omop")["required"].append("x")
    assert companion_tables("omop")["required"] == ["member"]


def test_covers_every_entry_point_of_the_plugin() -> None:
    text = (Path(pkg.__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    sinks = text.split('[project.entry-points."shape.sinks"]')[1].split("[")[0]
    declared = re.findall(r"^([\w-]+) =", sinks, flags=re.M)
    assert sorted(declared) == sorted(ENTRY_POINTS)
    assert set(ENTRY_POINTS) | {"fhir"} == set(SINK_NAMES)


@pytest.mark.parametrize("bad", ["", "x12", "csv", "X12-837P", "fhir-ndjson ", "member"])
def test_unknown_sink_name_raises_value_error(bad: str) -> None:
    with pytest.raises(ValueError, match="unknown sink"):
        companion_tables(bad)


def test_documentation_table_equals_the_function() -> None:
    doc = DOC.read_text(encoding="utf-8")
    m = re.search(
        r"<!-- companion-tables:start[^>]*-->\n(.*?)\n<!-- companion-tables:end -->", doc, re.S
    )
    assert m, "the generated table markers are missing from healthcare-standards.md"
    assert m.group(1) == markdown_table()
    parsed: dict[str, dict[str, list[str]]] = {}
    for line in m.group(1).splitlines()[2:]:
        name, req, opt = (c.strip() for c in line.strip("|").split("|"))
        parsed[name.strip("`")] = {
            "required": re.findall(r"`(\w+)`", req),
            "optional": re.findall(r"`(\w+)`", opt),
        }
    assert parsed == {n: companion_tables(n) for n in SINK_NAMES}

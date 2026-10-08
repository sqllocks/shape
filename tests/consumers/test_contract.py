"""W3-13 deliverable 3: the consumer contract format and ``shape contracts validate``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from consumer_helpers import FINANCE, contract

from shape.cli.main import main
from shape.consumers import ConsumerContractError, load, parse, problems, schema
from shape.schemacheck import validate


def write(tmp_path: Path, doc: Any, name: str = "c.json") -> Path:
    p = tmp_path / name
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return p


def validate_cmd(
    capsys: pytest.CaptureFixture[str], path: Path, *flags: str
) -> tuple[int, str, str]:
    rc = main(["contracts", "validate", str(path), *flags])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_a_valid_contract_is_accepted(tmp_path, capsys):
    p = write(tmp_path, contract("finance", FINANCE))
    rc, out, _ = validate_cmd(capsys, p)
    assert rc == 0
    assert json.loads(out) == {
        "valid": True,
        "file": str(p),
        "format": "shape-consumer-contract",
        "consumer": "finance",
        "source": "orders",
    }
    c = load(p)
    assert (c.consumer, c.owner, c.source, c.since) == (
        "finance",
        "finance@example.com",
        "orders",
        "2026-10-03",
    )


def test_a_single_table_contract_is_accepted():
    doc = contract("c", {"required_columns": ["a"], "columns": {"a": {"dtype": "integer"}}})
    assert problems(doc) == []


def test_the_json_schema_is_shipped_and_agrees():
    s = schema()
    assert s["properties"]["format"] == {"const": "shape-consumer-contract"}
    assert validate(contract("c", FINANCE), s) == []
    assert validate({"format": "shape-consumer-contract"}, s)


@pytest.mark.parametrize(
    "key", ["format", "version", "consumer", "owner", "source", "since", "requires"]
)
def test_every_top_level_key_is_required(key):
    doc = contract("c", FINANCE)
    del doc[key]
    found = problems(doc)
    assert any(key in p for p in found), found


def test_wrong_format_name_and_types():
    found = problems(contract("c", FINANCE, format="shape-contract"))
    assert any(p.startswith("$.format") for p in found)
    found = problems(contract("c", FINANCE, version="1"))
    assert any(p.startswith("$.version") for p in found)
    found = problems(contract("c", FINANCE, requires=[]))
    assert any(p.startswith("$.requires") for p in found)


def test_a_newer_version_is_refused_with_its_own_message():
    (found,) = problems(contract("c", FINANCE, version=2))
    assert "newer Shape" in found and found.startswith("$.version")
    assert problems(contract("c", FINANCE, version=1)) == []


@pytest.mark.parametrize("since", ["2026-13-01", "yesterday", "2026-10-3", ""])
def test_since_must_be_a_date(since):
    found = problems(contract("c", FINANCE, since=since))
    assert any(p.startswith("$.since") for p in found)


def test_names_and_owner():
    assert any(p.startswith("$.consumer") for p in problems(contract("a b", FINANCE)))
    assert any(p.startswith("$.source") for p in problems(contract("c", FINANCE, source="-x")))
    assert any(p.startswith("$.owner") for p in problems(contract("c", FINANCE, owner="  ")))


def test_unknown_top_level_key_is_a_problem():
    found = problems({**contract("c", FINANCE), "extra": 1})
    assert any("extra" in p for p in found)


def test_extra_columns_can_never_be_forbidden():
    doc = contract(
        "c", {"tables": {"t": {"allow_extra_columns": False, "required_columns": ["a"]}}}
    )
    (found,) = problems(doc)
    assert found.startswith("$.requires.tables.t.allow_extra_columns")
    doc = contract("c", {"required_columns": ["a"], "allow_extra_columns": False})
    (found,) = problems(doc)
    assert found.startswith("$.requires.allow_extra_columns")
    # saying it is allowed is harmless
    ok = contract("c", {"required_columns": ["a"], "allow_extra_columns": True})
    assert problems(ok) == []


def test_rule_problems_carry_the_key_path():
    doc = contract(
        "c",
        {
            "tables": {
                "orders": {
                    "columns": {
                        "amount": {"dtype": "float", "bogus": 1},
                        "ok": {"dtype": "string"},
                    },
                    "row_count": {"min": 1, "median": 3},
                    "fd": [{"determinant": "a"}],
                }
            }
        },
    )
    found = problems(doc)
    paths = [p.split(":")[0] for p in found]
    assert "$.requires.tables.orders.columns.amount" in paths
    assert "$.requires.tables.orders.row_count" in paths
    assert "$.requires.tables.orders.fd" in paths
    assert not any("columns.ok" in p for p in paths)


def test_nested_tables_and_mixed_shapes_are_rejected():
    found = problems(contract("c", {"tables": {"t": {"tables": {}}}}))
    assert any(p.startswith("$.requires.tables.t.tables") for p in found)
    found = problems(contract("c", {"tables": {"t": {"required_columns": ["a"]}}, "columns": {}}))
    assert any(p.startswith("$.requires.columns") for p in found)


@pytest.mark.parametrize("requires", [{}, {"tables": {}}, {"tables": {"t": {}}}, {"columns": {}}])
def test_a_contract_that_requires_nothing_is_rejected(requires):
    assert problems(contract("c", requires))


def test_every_problem_is_reported_at_once(tmp_path, capsys):
    doc = contract("c", {"tables": {"t": {"columns": {"a": {"nope": 1}}}}}, since="soon", owner="")
    doc["unknown"] = True
    rc, _, err = validate_cmd(capsys, write(tmp_path, doc))
    assert rc == 2
    lines = [ln for ln in err.splitlines() if ln.startswith("shape: error:")]
    assert len(lines) >= 4
    assert any("$.since" in ln for ln in lines)
    assert any("$.owner" in ln for ln in lines)
    assert any("$.requires.tables.t.columns.a" in ln for ln in lines)
    assert any("unknown" in ln for ln in lines)


def test_validate_json_output(tmp_path, capsys):
    rc, out, _ = validate_cmd(capsys, write(tmp_path, contract("c", FINANCE, since="x")), "--json")
    assert rc == 2
    doc = json.loads(out)
    assert doc["valid"] is False and any(p.startswith("$.since") for p in doc["problems"])


def test_unreadable_files_are_exit_2(tmp_path, capsys):
    rc, _, err = validate_cmd(capsys, tmp_path / "missing.json")
    assert rc == 2 and "file not found" in err
    rc, _, err = validate_cmd(capsys, write(tmp_path, "{not json"))
    assert rc == 2 and "not valid JSON" in err
    rc, _, err = validate_cmd(capsys, write(tmp_path, "[1, 2]", "list.json"))
    assert rc == 2 and "JSON object" in err
    binary = tmp_path / "b.json"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    rc, _, err = validate_cmd(capsys, binary)
    assert rc == 2 and "not a text file" in err
    rc, _, err = validate_cmd(capsys, tmp_path)
    assert rc == 2 and "folder" in err


def test_parse_and_load_raise_with_every_problem(tmp_path):
    with pytest.raises(ConsumerContractError) as exc:
        parse({"format": "x"})
    assert len(exc.value.problems) >= 2
    with pytest.raises(ConsumerContractError):
        load(tmp_path / "nope.json")

"""W8-06 (#766) item 1 on the bridge: ``generate`` takes the optional ``identifiers`` argument,
added in 1.2 (an additive optional argument, ``docs/BRIDGE.md``): reserved by default, realistic
when asked (said once on standard error, never on standard output), unknown values refused, and a
1.0 or 1.1 request does not know it."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from shape.bridge.schemas import all_schemas
from shape.generation.identifiers import REALISTIC_NOTICE

RESERVED_HOSTS = {"example.com", "example.org", "example.net"}


def emails(folder: Path) -> list[str]:
    with open(folder / "customer.csv", newline="", encoding="utf-8") as handle:
        return [row["email"] for row in csv.DictReader(handle) if row["email"]]  # nulls are empty


def hosts(values: list[str]) -> set[str]:
    return {v.split("@")[1] for v in values}


def test_generate_publishes_the_argument_as_since_1_2() -> None:
    schemas = all_schemas()
    assert schemas["index.json"]["commands"]["generate"]["args"]["identifiers"] == "1.2"
    request = schemas["commands/generate.request.schema.json"]["properties"]["args"]
    arg = request["properties"]["identifiers"]
    assert arg["enum"] == ["reserved", "realistic"] and arg["x-since"] == "1.2"
    assert "identifiers" not in request.get("required", [])


def test_generate_is_reserved_by_default(
    api12, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "default"
    api12.ok("generate", domain="retail", scale="small", seed=2, format="csv", output_dir=str(out))
    assert hosts(emails(out)) <= RESERVED_HOSTS
    assert REALISTIC_NOTICE not in capsys.readouterr().err


def test_generate_identifiers_realistic(
    api12, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "realistic"
    result = api12.ok(
        "generate",
        domain="retail",
        scale="small",
        seed=2,
        format="csv",
        output_dir=str(out),
        identifiers="realistic",
    )
    assert result["integrity_pass"] is True
    assert not hosts(emails(out)) & RESERVED_HOSTS
    captured = capsys.readouterr()
    assert captured.err.count(REALISTIC_NOTICE) == 1
    assert REALISTIC_NOTICE not in captured.out


def test_generate_reserved_is_the_default_output(api12, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    api12.ok("generate", domain="retail", scale="small", seed=2, format="csv", output_dir=str(a))
    api12.ok(
        "generate",
        domain="retail",
        scale="small",
        seed=2,
        format="csv",
        output_dir=str(b),
        identifiers="reserved",
    )
    assert (a / "customer.csv").read_bytes() == (b / "customer.csv").read_bytes()


@pytest.mark.parametrize("bad", ["assignable", "Realistic", ""])
def test_generate_refuses_an_unknown_value(api12, bad: str) -> None:
    error = api12.fail("generate", "usage.invalid_argument", domain="retail", identifiers=bad)
    assert "identifiers" in error["message"]


def test_generate_refuses_a_non_string(api12) -> None:
    api12.fail("generate", "usage.invalid_argument", domain="retail", identifiers=1)


@pytest.mark.parametrize("fixture", ["api", "api11"])
def test_a_1_0_or_1_1_request_does_not_know_the_argument(
    request: pytest.FixtureRequest, fixture: str
) -> None:
    caller = request.getfixturevalue(fixture)
    error = caller.fail(
        "generate", "usage.unknown_argument", domain="retail", identifiers="reserved"
    )
    assert "1.2" in (error["hint"] or "") + error["message"]

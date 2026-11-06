"""Replay the additive 1.5 vectors without rewriting frozen publications."""

from __future__ import annotations

import json
from pathlib import Path

import vectors_lib

from shape.bridge.core import Bridge

ROOT = Path(__file__).parents[2]


def fixtures(directory):
    from shape.cli.incremental import write_tables
    from shape.proposals import DecisionFile
    from shape.scenario.library.run import _generate, _schema

    directory.mkdir(parents=True, exist_ok=True)
    DecisionFile().write(directory / "decisions.json")
    (directory / "contract.json").write_text("{}\n")
    (directory / "suite.json").write_text(
        json.dumps({"format": "shape-suite", "version": 1, "scenarios": ["clean_baseline"]})
    )
    source = directory / "selected"
    source.mkdir()
    write_tables(_generate(_schema("retail"), "tiny", 42).tables, "parquet", source)
    for name in ("real", "generated"):
        (directory / name).mkdir()
        (directory / name / "data.csv").write_text("id,amount\n1,2\n2,3\n3,4\n4,5\n")


def requests():
    base = {"api_version": "1.5"}
    return [
        {
            **base,
            "command": "rules_apply",
            "args": {"decisions": "${DIR}/decisions.json", "contract": "${DIR}/contract.json"},
        },
        {
            **base,
            "command": "suite_run",
            "args": {"suite": "${DIR}/suite.json", "input": "${DIR}/selected", "name": "selected"},
        },
        {
            **base,
            "command": "report_card",
            "args": {
                "source": "${DIR}/real",
                "path": "${DIR}/generated",
                "format": "csv",
                "tiers": [2],
            },
        },
    ]


def test_additive_vectors(tmp_path):
    directory = tmp_path / "work"
    fixtures(directory)
    bridge = Bridge(directory / "jobs")
    doc = json.loads((ROOT / "docs/bridge/vectors/1.5/additions.json").read_text())
    from jsonschema_lite import validate

    from shape.bridge.schemas import all_schemas

    schemas = all_schemas()
    assert {c["request"]["command"] for c in doc["cases"]} == {
        "rules_apply",
        "suite_run",
        "report_card",
    }
    with vectors_lib.environment({"needs": "no-advanced"}):
        for case in doc["cases"]:
            actual = bridge.handle(vectors_lib.substitute(case["request"], str(directory)))
            expected = vectors_lib.substitute(case["response"], str(directory))
            expected["api_version"] = actual["api_version"]
            assert vectors_lib.matches(expected, actual) == []
            assert (
                validate(
                    actual["result"],
                    schemas[f"commands/{case['request']['command']}.result.schema.json"],
                )
                == []
            )

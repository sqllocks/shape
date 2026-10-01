"""P4-01b: Shape's DDL import equals the baseline's on every baseline DDL fixture.

``benchmarks/vs_spindle/ddl_1to1/fixtures/expected`` holds what the baseline's ``from-ddl`` made
of each input (``dump_ddl.py``, whose ``--check`` proves the files still equal the baseline's
output). Each input is imported in both modes and compared field by field: the schema, every
inference annotation, and (through the command) the written file and the ``--explain`` report.
``benchmarks/vs_spindle/ddl_1to1/verify.py`` runs the same comparison as a script.
Nothing here needs the baseline's venv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "ddl_1to1"))

import verify  # noqa: E402

INPUTS = verify.inputs()
CASES = [(name, mode) for name in INPUTS for mode in ("smart", "plain")]


def test_every_baseline_ddl_fixture_is_covered() -> None:
    """The baseline's tests hold 8 DDL strings and a sample file; all of them are here."""
    baseline = {p.stem for p in (BENCH / "ddl_1to1" / "fixtures").glob("*.sql")}
    assert len(baseline) >= 9
    assert {
        "ddl_parser__sql_server_ddl",
        "ddl_parser__postgres_ddl",
        "ddl_parser__mysql_ddl",
        "ddl_parser__comment_ddl",
        "adventureworks_sample",
        "smart_inference__ddl_plural",
    } <= baseline
    assert {p.stem for p in (BENCH / "ddl_1to1" / "extra").glob("*.sql")} <= set(INPUTS)


def test_the_extra_inputs_fire_every_reachable_rule() -> None:
    fired: set[str] = set()
    for path in (verify.EXPECTED).glob("*.smart.json"):
        fired |= {a["rule_id"] for a in json.loads(path.read_text("utf-8"))["annotations"]}
    # FK-05 is unreachable (a self-referencing column is not a foreign_key strategy), and the
    # remaining TC-/TP-/EN- ids are per role or semantic.
    for rule in (
        "FK-00 FK-01 FK-02 FK-03 FK-04 FK-06 FK-07 FK-08 FK-09 FK-10 "
        "CA-01 CA-02 CA-03 CA-04 CA-05 CA-06 CA-07 CA-08 CA-09 CA-SCALE "
        "CR-01 CR-02 CR-04 CR-05 CR-09 BR-01 BR-02 BR-03 BR-04 BR-05 BR-06 BR-07 BR-08 "
        "ND-MONETARY ND-QUANTITY ND-PERCENTAGE ND-MEASUREMENT ND-RATING "
        "EN-STATUS EN-CATEGORICAL TP-TEMPORAL_TRANSACTION TP-TEMPORAL_END TP-TEMPORAL_BIRTH"
    ).split():
        assert rule in fired, rule


@pytest.mark.parametrize(("name", "mode"), CASES, ids=[f"{n}-{m}" for n, m in CASES])
def test_shape_equals_the_baseline(name: str, mode: str) -> None:
    want = json.loads((verify.EXPECTED / f"{name}.{mode}.json").read_text("utf-8"))
    schema, notes = verify.from_ddl(
        INPUTS[name], domain=verify.DOMAIN, smart=mode == "smart", scale=verify.SCALE
    )
    assert verify.diff(schema.to_dict(), verify.to_native(want["schema"]), "schema") == []
    got = [
        {
            "table": n.table,
            "column": n.column,
            "rule_id": n.rule_id,
            "description": n.description,
            "confidence": n.confidence,
        }
        for n in notes
    ]
    assert got == want["annotations"]
    file_doc = schema.to_dict()
    file_doc["business_rules"] = []
    file_doc["generation"]["derived_counts"] = {}
    assert verify.diff(file_doc, verify.to_native(want["file"]), "baseline file") == []


@pytest.mark.parametrize("name", ["smart_retail", "adventureworks_sample"])
def test_command_writes_the_baselines_file_and_report(name: str, tmp_path: Path) -> None:
    from shape.cli.main import main

    src, out = tmp_path / "in.sql", tmp_path / "out.json"
    src.write_text(INPUTS[name], encoding="utf-8")
    argv = ["from-ddl", str(src), "-o", str(out), "--domain", verify.DOMAIN]
    assert main([*argv, "-s", verify.SCALE, "--explain"]) == 0
    want = json.loads((verify.EXPECTED / f"{name}.smart.json").read_text("utf-8"))
    doc = json.loads(out.read_text("utf-8"))
    assert verify.diff(doc, verify.to_native(want["schema"]), "file") == []

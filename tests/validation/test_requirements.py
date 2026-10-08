"""SHAPE_2.md conformance (P1-10): one test per normative statement, and each can fail."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shape.validation import requirements as req
from shape.validation.suite import conformance

ROOT = Path(__file__).resolve().parents[2]


def test_every_statement_has_exactly_one_test():
    spec = req.statements()
    assert len(spec) >= 30 and set(spec) == set(req.REQUIREMENTS)
    assert req.coverage_problems() == []


@pytest.mark.parametrize("ident", sorted(req.REQUIREMENTS))
def test_requirement_holds(ident):
    req.REQUIREMENTS[ident]()


def test_conformance_suite_reports_every_statement_as_json():
    results = conformance()
    by_name = {r.name: r for r in results}
    assert {"artifact", "capture", "generation"} <= set(by_name)
    assert set(req.REQUIREMENTS) <= set(by_name)
    assert all(r.passed for r in results), [r for r in results if not r.passed]
    json.dumps([{"id": r.name, "passed": r.passed, "detail": r.detail} for r in results])


def test_the_coverage_script_agrees_with_the_suite():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_conformance_coverage.py")],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{len(req.statements())} normative statements" in done.stdout


def test_the_packaged_statements_match_the_specification():
    packaged = json.loads((ROOT / "src/shape/validation/statements.json").read_text("utf-8"))
    assert packaged == req.parse_statements((ROOT / "docs/specs/SHAPE_2.md").read_text("utf-8"))


def test_the_parser_rejects_a_normative_line_without_an_identifier():
    with pytest.raises(ValueError, match="without an identifier"):
        req.parse_statements("- **SH2-001** A thing MUST be.\n\nAnother thing MUST be.\n")
    with pytest.raises(ValueError, match="exactly one"):
        req.parse_statements("- **SH2-001** A thing MUST be and MUST NOT be.\n")
    with pytest.raises(ValueError, match="twice"):
        req.parse_statements("- **SH2-001** A MUST.\n- **SH2-001** B MUST.\n")
    found = req.parse_statements(
        "- **SH2-001** A thing MUST\n  continue here.\n- **SH2-002** B MUST NOT.\n"
    )
    assert found["SH2-001"] == "A thing MUST continue here."


def _fails(ident: str) -> bool:
    try:
        req.REQUIREMENTS[ident]()
    except Exception:
        return True
    return False


def test_the_tests_can_fail(monkeypatch):
    """Put each fixed bug back and the matching conformance test must notice."""
    from shape.contracts import core as contracts_core
    from shape.query import core as query_core

    # P21: a relationship "matches" when it merely mentions both names
    def loose(model, a, b):
        for rel in model.get("relationships", ()):
            if a in (rel["source"], rel["target"]) and b in (rel["source"], rel["target"]):
                return dict(rel)
        return None

    monkeypatch.setattr(query_core, "_relationship", loose)
    assert _fails("SH2-027")
    monkeypatch.undo()

    # P15: the null rate of an empty table divides by one
    monkeypatch.setattr(
        contracts_core,
        "null_rate",
        lambda column, rows: (column.get("null_count") or 0) / max(1, rows),
    )
    monkeypatch.setattr(
        "shape.spec.view.null_rate",
        lambda column, rows: (column.get("null_count") or 0) / max(1, rows),
    )
    assert _fails("SH2-021")
    monkeypatch.undo()

    # P14: the unique check compares the bare estimate with the row count
    monkeypatch.setattr(
        contracts_core, "distinct_bounds", lambda c: (float(c["distinct"]), float(c["distinct"]))
    )
    assert _fails("SH2-020")
    monkeypatch.undo()

    # the artifact writer goes back to rejecting NaN
    import shape.security.hardening as hardening

    real = hardening.validate_structure
    monkeypatch.setattr(
        "shape.artifact.shape_file.validate_structure",
        lambda obj, depth=0, **kw: real(obj, depth),
    )
    assert _fails("SH2-014")

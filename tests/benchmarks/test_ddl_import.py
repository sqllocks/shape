"""P4-01b, P4-01c: Shape's DDL import equals the baseline's on every baseline DDL fixture, apart
from the intentional differences listed in ``benchmarks/vs_refengine/ddl_1to1/differences.py``.

``benchmarks/vs_refengine/ddl_1to1/fixtures/expected`` holds what the baseline's ``from-ddl`` made
of each input (``dump_ddl.py``, whose ``--check`` proves the files still equal the baseline's
output). Each input is imported in both modes and compared field by field: the schema, every
inference annotation, and (through the command) the written file and the ``--explain`` report.
A difference is accepted only if the allow-list names it (input, mode and field); an entry that
matches no difference fails too. ``benchmarks/vs_refengine/ddl_1to1/verify.py`` runs the same
comparison as a script. Nothing here needs the baseline's venv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"
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
def test_shape_equals_the_baseline_apart_from_the_listed_differences(name: str, mode: str) -> None:
    problems, _ = verify.compare(name, INPUTS[name], mode)
    assert problems == []


def test_every_intentional_difference_is_shown_by_some_case() -> None:
    seen: set[str] = set()
    for name, mode in CASES:
        seen |= verify.compare(name, INPUTS[name], mode)[1]
    assert seen == set(verify.FIXES)


def test_the_allow_list_is_narrow() -> None:
    """Every entry names one input and a field under it, and every field a fix touches is a
    column, a rule, a relationship or a count of the table concerned (never a whole table)."""
    for entry in verify.ALLOWED:
        assert entry.case in INPUTS
        assert entry.fix in verify.FIXES
        if isinstance(entry, verify.Field):
            assert entry.path.split(".")[0] in {
                "tables",
                "relationships",
                "business_rules",
                "generation",
            }
            assert entry.path.count(".") >= 1  # at least a table or a name under the section
            if entry.path.startswith("tables."):
                assert ".columns." in entry.path  # never a whole table
    assert len(verify.ALLOWED) == len(set(verify.ALLOWED))


@pytest.mark.parametrize("fix", sorted(verify.FIXES))
def test_without_its_entries_a_fix_is_reported(fix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: take one fix's entries out of the allow-list and the comparison fails
    where that fix shows (and only there)."""
    rest = [e for e in verify.ALLOWED if e.fix != fix]
    shows = {e.case for e in verify.ALLOWED if e.fix == fix}
    monkeypatch.setattr(verify, "ALLOWED", rest)
    failing = {name for name, mode in CASES if verify.compare(name, INPUTS[name], mode)[0]}
    assert failing == shows


def test_a_stale_entry_fails() -> None:
    stale = verify.Field("F2", "quoted_and_exotic", "tables.nothing.columns.here")
    old = verify.ALLOWED
    verify.ALLOWED = [*old, stale]
    try:
        problems, _ = verify.compare("quoted_and_exotic", INPUTS["quoted_and_exotic"], "smart")
    finally:
        verify.ALLOWED = old
    assert any("matches no difference" in p for p in problems)


@pytest.mark.parametrize("name", ["smart_retail", "adventureworks_sample"])
def test_command_writes_the_baselines_file_and_report(name: str) -> None:
    """Through ``shape from-ddl``: the written file is the schema, and the ``--explain`` report
    is the baseline's apart from the lines of the listed differences."""
    problems, _ = verify.check(name, INPUTS[name], "smart")
    assert problems == []

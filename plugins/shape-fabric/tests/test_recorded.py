"""Contract tests against recorded interactions, and the guarantee that no tape holds a secret.

Every file in ``fixtures/`` is a tape produced by ``python -m shape_fabric.scenarios record``: the
requests a writer made (Kusto HTTP, ODBC statements) and the answers it got. These tests replay
each scenario against its tape and fail at the first request that differs.
"""

from pathlib import Path

import pytest
from shape_fabric import EventhouseWriter
from shape_fabric.recording import (
    ReplayMismatch,
    Tape,
    TapeConnection,
    TapeTransport,
    find_secrets,
    load,
    replay_tape,
)
from shape_fabric.scenarios import KQL_URI, SCENARIOS, record, replay
from shape_fabric.testing import sample_batches

FIXTURES = Path(__file__).parent / "fixtures"
TAPES = sorted(FIXTURES.glob("*.json"))
pytestmark = pytest.mark.contract


def test_every_scenario_has_a_tape_and_every_tape_a_scenario():
    assert sorted(p.stem for p in TAPES) == sorted(SCENARIOS)


@pytest.mark.parametrize("path", TAPES, ids=lambda p: p.stem)
def test_the_writer_makes_exactly_the_recorded_requests(path):
    doc = load(path)
    scenario = SCENARIOS[doc["scenario"]]
    assert doc["channel"] == scenario.channel
    assert len(doc["steps"]) >= 1
    assert replay(scenario, doc) == doc["result"]


@pytest.mark.parametrize("path", TAPES, ids=lambda p: p.stem)
def test_tapes_made_by_the_fakes_are_what_the_scenarios_record_today(path):
    # a tape is only ever produced by running its scenario; one edited by hand (or left behind
    # when a writer changed) differs from a fresh recording
    doc = load(path)
    if doc["source"] != "in-repo fake service":
        pytest.skip("a tape recorded against a live service is reviewed, not regenerated")
    assert record(SCENARIOS[doc["scenario"]]) == doc


@pytest.mark.parametrize("path", TAPES, ids=lambda p: p.stem)
def test_no_tape_holds_a_secret_or_token(path):
    text = path.read_text(encoding="utf-8")
    assert find_secrets(text) == []
    assert "Bearer " not in text.replace("Bearer <redacted>", "")


def test_a_changed_request_is_a_mismatch():
    tape = replay_tape(load(FIXTURES / "eventhouse_create.json"))
    writer = EventhouseWriter(
        KQL_URI, transport=TapeTransport(tape), credential=lambda scope: "t", busy_pause=0.0
    )
    with pytest.raises(ReplayMismatch, match="request #2 differs"):
        # the recording created the table with `.create table`; append asks `.create-merge`
        writer.write_table("customer", sample_batches(), write_mode="append")


def test_a_request_that_was_not_made_is_reported():
    tape = replay_tape(load(FIXTURES / "eventhouse_create.json"))
    client = EventhouseWriter(
        KQL_URI, transport=TapeTransport(tape), credential=lambda scope: "t", busy_pause=0.0
    ).client
    client.table_exists("customer")  # the recording's first request; the others are never made
    with pytest.raises(ReplayMismatch, match="never made"):
        tape.assert_done()


def test_an_extra_request_is_a_mismatch():
    tape = Tape([], channel="odbc", scenario="x")
    conn = TapeConnection(tape)
    with pytest.raises(ReplayMismatch, match="unexpected extra request"):
        conn.commit()

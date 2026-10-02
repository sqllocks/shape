"""Contract tests against recorded interactions, and the guarantee that no tape holds a secret.

Every file in ``fixtures/`` is a tape produced by ``python -m shape_fabric.scenarios record``: the
requests a writer made (Kusto HTTP, ODBC statements) and the answers it got. These tests replay
each scenario against its tape and fail at the first request that differs.
"""

import json
from pathlib import Path

import pytest
from shape_fabric import EventhouseWriter
from shape_fabric.recording import (
    RecordingError,
    ReplayMismatch,
    Tape,
    TapeConnection,
    TapeTransport,
    find_secrets,
    load,
    replay_tape,
    save,
    scrub,
)
from shape_fabric.scenarios import KQL_URI, SCENARIOS, record, replay
from shape_fabric.testing import FakeKusto, sample_batches

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


# --- the scrubber ------------------------------------------------------------------------

JWT = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJodHRwczovL3N0b3JhZ2UuYXp1cmUuY29tIiwic3ViIjoiYWJjIn0.c2lnbmF0dXJlLXNpZ25hdHVyZS1zaWduYXR1cmU"
ACCOUNT_KEY = "dGhpcy1pcy1ub3QtYS1yZWFsLWtleS1idXQtbG9va3MtbGlrZS1vbmUtMTIzNDU2Nzg5MDEyMzQ1Njc4OTA="
SECRETS = [
    f"Authorization: Bearer {JWT}",
    "Bearer abcdefghijklmnop1234567890",
    JWT,
    "Server=s;UID=u;PWD=hunter2hunter2;Encrypt=yes",
    "Server=s;Password={p;w}}x};Database=d",
    f"DefaultEndpointsProtocol=https;AccountName=a;AccountKey={ACCOUNT_KEY};EndpointSuffix=x",
    "Endpoint=sb://ns.servicebus.windows.net/;SharedAccessKeyName=k;SharedAccessKey=Zm9vYmFyYmF6cXV4",
    "https://acct.blob.core.windows.net/c/f?sv=2022-11-02&sig=Zm9vYmFyYmF6cXV4YWJjZA%3D%3D&se=2030",
    "client_secret=Zq~8Q~abcdefghijklmnopqrstuvwxyz0123456789",
    "abc8Q~abcdefghijklmnopqrstuvwxyz0123456789",
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
    ACCOUNT_KEY,
]


@pytest.mark.parametrize("secret", SECRETS)
def test_the_scrubber_finds_and_removes_every_kind_of_secret(secret):
    assert find_secrets(secret), "find_secrets must notice it (else the scan proves nothing)"
    assert find_secrets(scrub(secret)) == []
    assert scrub(secret) != secret


def test_ordinary_text_is_left_alone():
    plain = ".create table ['customer'] (['id']:long)"
    assert scrub(plain) == plain and find_secrets(plain) == []
    assert scrub("COPY INTO [dbo].[t] FROM 'https://onelake.dfs.fabric.microsoft.com/a/b/'") == (
        "COPY INTO [dbo].[t] FROM 'https://onelake.dfs.fabric.microsoft.com/a/b/'"
    )


def test_save_refuses_a_tape_that_holds_a_secret(tmp_path):
    doc = {"format": 1, "steps": [{"request": {"body": f"token {JWT}"}, "response": {}}]}
    with pytest.raises(RecordingError, match="would hold a secret"):
        save(tmp_path / "x.json", doc)
    assert not (tmp_path / "x.json").exists()


def test_a_planted_secret_in_a_committed_tape_would_be_caught(tmp_path):
    clean = (FIXTURES / "eventhouse_create.json").read_text()
    planted = clean.replace('"Bearer <redacted>"', f'"Bearer {JWT}"')
    assert planted != clean and find_secrets(planted)


def test_recording_scrubs_tokens_so_a_tape_does_not_depend_on_them():
    kusto = FakeKusto()
    tape = Tape(channel="http", scenario="s")
    transport = TapeTransport(tape, kusto)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {JWT}"}
    body = json.dumps({"db": "db1", "csl": ".show tables | where TableName == 'x' | count"}).encode()
    transport("POST", "https://kql.example.test/v1/rest/mgmt", headers, body, 5)
    text = json.dumps(tape.steps)
    assert JWT not in text and find_secrets(text) == []
    assert tape.steps[0]["request"]["headers"]["authorization"] == "Bearer <redacted>"
    # replaying with a different token (tokens expire daily) still matches
    again = Tape(tape.steps, channel="http", scenario="s")
    TapeTransport(again)(
        "POST",
        "https://kql.example.test/v1/rest/mgmt",
        {**headers, "Authorization": "Bearer another.token.value-1234567890"},
        body,
        5,
    )
    again.assert_done()


def test_odbc_parameters_are_scrubbed_too():
    from shape_fabric.testing import FakeSqlServer

    server = FakeSqlServer()
    tape = Tape(channel="odbc", scenario="s")
    cursor = TapeConnection(tape, server.connect("x")).cursor()
    cursor.execute("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", "dbo", "Server=s;PWD=hunter2hunter2")
    assert find_secrets(json.dumps(tape.steps)) == []

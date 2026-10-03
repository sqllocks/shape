"""The tape scrubber and the guarantee that no tape holds a secret.

These tests need synthetic secret literals (a JWT, an account key, a private-key header) to prove
the scrubber finds and removes each kind. They live under ``security/`` so that
``scripts/check_secrets.py`` exempts them, the same convention as ``tests/security/``; the
contract tests that replay the recorded tapes stay in ``test_recorded.py``.
"""

import json
from pathlib import Path

import pytest
from shape_fabric.recording import (
    RecordingError,
    Tape,
    TapeConnection,
    TapeTransport,
    find_secrets,
    save,
    scrub,
)
from shape_fabric.testing import FakeKusto

FIXTURES = Path(__file__).parent.parent / "fixtures"
pytestmark = pytest.mark.contract


# --- the scrubber ------------------------------------------------------------------------

JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJodHRwczovL3N0b3JhZ2UuYXp1cmUuY29tIiwic3ViIjoiYWJjIn0."
    "c2lnbmF0dXJlLXNpZ25hdHVyZS1zaWduYXR1cmU"
)
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
    body = json.dumps(
        {"db": "db1", "csl": ".show tables | where TableName == 'x' | count"}
    ).encode()
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
    cursor.execute(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
        "dbo",
        "Server=s;PWD=hunter2hunter2",
    )
    assert find_secrets(json.dumps(tape.steps)) == []

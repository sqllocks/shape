"""The tape scrubber redacts every written form of a secret (#411)."""

from __future__ import annotations

import json

import pytest
from shape_fabric import recording
from shape_fabric.auth import connection_string_with_login

SECRET = "TOPSECRETvalue"


def _clean(text: str) -> None:
    out = recording.scrub(text)
    assert SECRET not in out, out
    assert recording.find_secrets(out) == []


def test_a_braced_password_holding_a_closing_brace() -> None:
    cs = connection_string_with_login("Driver={ODBC Driver 18};Server=x", "u", f"ab}}{SECRET};x")
    assert "}}" in cs  # the ODBC escape for '}'
    _clean(cs)
    # find_secrets sees what scrub would miss
    assert recording.find_secrets(cs)


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"client_secret": SECRET, "password": SECRET}),
        json.dumps(json.dumps({"client_secret": SECRET})),  # a body inside a tape (escaped)
        f"Password='{SECRET} two';",
        f'pwd="{SECRET} two"',
        f"password=my {SECRET} phrase",
    ],
)
def test_json_and_quoted_values(text: str) -> None:
    _clean(text)


def test_a_json_body_stays_json() -> None:
    out = recording.scrub(json.dumps({"password": SECRET, "user": "u"}))
    assert json.loads(out) == {"password": recording.REDACTED, "user": "u"}


def test_save_refuses_a_tape_holding_a_json_secret(tmp_path) -> None:
    doc = {"format": "x", "body": json.dumps({"client_secret": SECRET})}
    with pytest.raises(recording.RecordingError):
        recording.save(tmp_path / "t.json", doc)
    assert not (tmp_path / "t.json").exists()


def test_scrubbing_twice_changes_nothing() -> None:
    once = recording.scrub(f'{{"password": "{SECRET}"}};PWD={{a}}}}b}};pwd=x y')
    assert recording.scrub(once) == once

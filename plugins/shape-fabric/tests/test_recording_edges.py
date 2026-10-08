"""Recording and replay keep what the code under test sees (#442); a malformed tape is a
RecordingError, and fetchone gives real values (#455)."""

from __future__ import annotations

import datetime as dt
import urllib.error
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from shape_fabric import recording as r
from shape_fabric._auth import StaticCredential
from shape_fabric.keyvault import KeyVaultResolver

from shape.security.credrefs import CredentialReferenceError


def _unreachable(method: str, url: str, headers: Any, body: bytes, timeout: float) -> Any:
    raise urllib.error.URLError("dns failure")


def _resolve(transport: Any) -> str:
    with pytest.raises(CredentialReferenceError) as err:
        KeyVaultResolver(StaticCredential("t" * 30), transport)("myvault/sec")
    return str(err.value)


def test_a_transport_error_is_the_same_direct_recorded_and_replayed() -> None:
    direct = _resolve(_unreachable)
    tape = r.Tape(channel="http", scenario="kv")
    recorded = _resolve(r.TapeTransport(tape, _unreachable))
    replayed = _resolve(r.TapeTransport(r.replay_tape(tape.document("t"))))
    assert direct == recorded == replayed
    assert "could not be reached (URLError)" in direct


def test_a_recorded_exception_keeps_its_class_while_recording() -> None:
    tape = r.Tape(channel="http", scenario="x")

    def fails(*_: Any) -> Any:
        raise KeyError("boom")

    with pytest.raises(KeyError):
        r.TapeTransport(tape, fails)("GET", "https://x/y", {}, b"", 1.0)


class _Conn:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def commit(self) -> None:
        self.calls.append("commit")
        raise RuntimeError("deadlock")

    def rollback(self) -> None:
        self.calls.append("rollback")


def test_a_failing_commit_is_recorded_and_replayed() -> None:
    tape = r.Tape(channel="odbc", scenario="c")
    conn = r.TapeConnection(tape, _Conn())
    with pytest.raises(RuntimeError, match="deadlock"):
        conn.commit()
    conn.rollback()
    again = r.TapeConnection(r.replay_tape(tape.document("t")))
    with pytest.raises(RuntimeError, match="deadlock"):
        again.commit()
    again.rollback()
    again.tape.assert_done()


@pytest.mark.parametrize("text", ["[1, 2]", "{not json", "", '"x"'])
def test_a_malformed_tape_is_a_recording_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "t.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(r.RecordingError, match="t.json"):
        r.load(path)


class _Cursor:
    def __init__(self, row: Any) -> None:
        self.row = row

    def fetchone(self) -> Any:
        return self.row


class _RowConn:
    def __init__(self, row: Any) -> None:
        self.row = row

    def cursor(self) -> _Cursor:
        return _Cursor(self.row)


def test_fetchone_gives_the_real_values_recording_and_replaying() -> None:
    row = (Decimal("1.5"), dt.date(2024, 1, 1), b"\x00\xff", 7, "s", None)
    tape = r.Tape(channel="odbc", scenario="f")
    assert r.TapeConnection(tape, _RowConn(row)).cursor().fetchone() == row
    replay = r.TapeConnection(r.replay_tape(tape.document("t")))
    assert replay.cursor().fetchone() == row

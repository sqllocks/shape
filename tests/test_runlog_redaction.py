"""AUD-security2 #290: the JSON run log never carries a credential (message, extra fields or the
exception), as the threat model says."""

from __future__ import annotations

import io
import json
import logging

from shape.runlog import JsonFormatter


def _log(emit) -> list[dict]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("shape.test.runlog_redaction")
    log.addHandler(handler)
    log.propagate = False
    try:
        emit(log)
    finally:
        log.removeHandler(handler)
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_messages_extras_and_exceptions_are_redacted():
    def emit(log):
        try:
            raise RuntimeError("Server=db;UID=sa;PWD=LOGSECRET")
        except RuntimeError:
            log.exception("failed on Server=db;PWD=MSGSECRET")
        log.error(
            "x",
            extra={
                "connection_string": "Server=db;PWD=EXTRASECRET",
                "target": "postgresql://u:URISECRET@h/db",
                "rows": 3,
            },
        )

    records = _log(emit)
    text = json.dumps(records)
    for secret in ("LOGSECRET", "MSGSECRET", "EXTRASECRET", "URISECRET"):
        assert secret not in text
    assert "RuntimeError" in records[0]["exception"]
    assert records[1]["connection_string"] == "***" and records[1]["rows"] == 3
    assert records[1]["target"] == "postgresql://u:***@h/db"

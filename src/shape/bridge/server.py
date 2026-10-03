"""The stdio loop: requests in on one stream, responses out on another (P6-11).

``serve`` reads one request per line (JSON Lines) and writes one response per line, until end of
input. ``serve(once=True)`` reads the whole input as one request (it may span lines) and answers
once. Standard output carries only responses: anything a command prints goes to standard error."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from typing import IO, Any

from shape.bridge.core import Bridge
from shape.bridge.protocol import MAX_REQUEST_BYTES, BridgeError, dumps, error_response


def serve(
    bridge: Bridge,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    *,
    once: bool = False,
) -> int:
    """Serve until end of input. Returns 0, or in ``once`` mode 1 when the answer is an error."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout

    def send(response: dict[str, Any]) -> None:
        sink.write(dumps(response) + "\n")
        sink.flush()

    with contextlib.redirect_stdout(sys.stderr):
        try:
            if once:
                return _serve_once(bridge, source, send)
            return _serve_lines(bridge, source, send)
        finally:
            bridge.close()  # streams stop, other jobs finish, so the job files say what happened


def _serve_once(bridge: Bridge, source: IO[str], send: Callable[[dict[str, Any]], None]) -> int:
    raw = source.read(MAX_REQUEST_BYTES + 1)
    if not raw.strip():
        response = error_response(BridgeError("usage.invalid_json", "empty input"))
    else:
        response = bridge.handle(raw)
    send(response)
    return 0 if response["ok"] else 1


def _serve_lines(bridge: Bridge, source: IO[str], send: Callable[[dict[str, Any]], None]) -> int:
    while True:
        line = source.readline(MAX_REQUEST_BYTES + 2)
        if not line:
            return 0
        if not line.strip():
            continue
        if len(line) > MAX_REQUEST_BYTES and not line.endswith("\n"):
            _skip_rest_of_line(source)
            send(
                error_response(
                    BridgeError(
                        "usage.request_too_large",
                        f"a request may be at most {MAX_REQUEST_BYTES} bytes",
                        "pass large inputs as file paths",
                    )
                )
            )
            continue
        send(bridge.handle(line))


def _skip_rest_of_line(source: IO[str]) -> None:
    while True:
        chunk = source.readline(1 << 20)
        if not chunk or chunk.endswith("\n"):
            return

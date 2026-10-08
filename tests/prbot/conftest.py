from __future__ import annotations

import ipaddress
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))

_REAL_CREATE_CONNECTION = socket.create_connection


def _is_loopback(host: object) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _loopback_connections(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The suite-wide guard refuses every `create_connection`; the HTTP stubs of these tests
    live on this machine's loopback, so that one door is opened for loopback addresses only."""

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        if not _is_loopback(address[0]):
            raise AssertionError(f"a test tried to connect to {address[0]!r}")
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield

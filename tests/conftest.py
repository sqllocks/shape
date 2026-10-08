"""Suite-wide collection rules and the zero-network guard."""

from __future__ import annotations

import importlib.util
import ipaddress
import socket
from collections.abc import Iterator
from typing import Any

import pytest

# Tests marked `sign` import the optional `cryptography` package ([sign] extra) at module
# level. Without it they cannot be collected, so `pytest -m "not sign"` must not collect them.
collect_ignore = []
if importlib.util.find_spec("cryptography") is None:
    collect_ignore += [
        "security/test_crypto.py",
        "artifact/test_secure.py",
        "security/test_ga_security_extended.py",
    ]

# A test that needs a network (an emulator or live cloud resources) keeps its own marker and is
# never put under the guard. Every other test is `zero_network`.
NETWORK_MARKERS = frozenset({"emulator", "live"})

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo


class NetworkAccessError(AssertionError):
    """Raised when a `zero_network` test tries to reach a network."""


def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _leaves_machine(sock: socket.socket, address: Any) -> bool:
    """True for an internet-family socket whose peer is not this machine's loopback."""
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return False
    host = address[0] if isinstance(address, tuple) and address else address
    return not _is_loopback(host)


def _denied(what: str, target: object) -> NetworkAccessError:
    return NetworkAccessError(f"zero_network test attempted network access: {what}({target!r})")


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if _leaves_machine(self, address):
        raise _denied("connect", address)
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    if _leaves_machine(self, address):
        raise _denied("connect_ex", address)
    return _REAL_CONNECT_EX(self, address)


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
    raise _denied("create_connection", address)


def _guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if _is_loopback(host):
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)
    raise _denied("getaddrinfo", (host, port))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if NETWORK_MARKERS & {m.name for m in item.iter_markers()}:
            continue
        item.add_marker(pytest.mark.zero_network)


@pytest.fixture(autouse=True)
def _zero_network_guard(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail any internet-socket connection or name lookup in a `zero_network` test.

    Local sockets stay usable: AF_UNIX and socketpair (the event loop needs them) and loopback
    (an in-process helper such as the Spark JVM gateway). Name lookups are blocked except for
    loopback names.
    """
    if request.node.get_closest_marker("zero_network") is None:
        yield
        return
    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "connect", _guarded_connect)
    patch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    patch.setattr(socket, "create_connection", _guarded_create_connection)
    patch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    try:
        yield
    finally:
        patch.undo()

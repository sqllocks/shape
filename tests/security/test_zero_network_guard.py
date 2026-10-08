"""The `zero_network` fixture blocks internet sockets and lets local ones through."""

import socket
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.security, pytest.mark.zero_network]


def test_connect_off_machine_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="zero_network"):
            s.connect(("192.0.2.1", 9))
        with pytest.raises(AssertionError, match="zero_network"):
            s.connect_ex(("192.0.2.1", 9))
    finally:
        s.close()


def test_ipv6_connect_off_machine_is_blocked():
    # A stand-in with the IPv6 family: the guard reads only `family`, and some hosts have no IPv6.
    fake = SimpleNamespace(family=socket.AF_INET6)
    with pytest.raises(AssertionError, match="zero_network"):
        socket.socket.connect(fake, ("2001:db8::1", 9))  # type: ignore[arg-type]


def test_loopback_connect_is_not_blocked_by_the_guard():
    # Nothing listens on port 9: a refused connection proves the guard let the call through.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert s.connect_ex(("127.0.0.1", 9)) != 0
    finally:
        s.close()


def test_create_connection_and_lookup_are_blocked():
    with pytest.raises(AssertionError, match="zero_network"):
        socket.create_connection(("example.invalid", 80))
    with pytest.raises(AssertionError, match="zero_network"):
        socket.getaddrinfo("example.invalid", 80)


def test_urllib_is_blocked():
    import urllib.request

    with pytest.raises(AssertionError, match="zero_network"):
        urllib.request.urlopen("http://example.invalid/", timeout=1)  # noqa: S310


def test_local_socketpair_still_works():
    a, b = socket.socketpair()
    try:
        a.sendall(b"ok")
        assert b.recv(2) == b"ok"
    finally:
        a.close()
        b.close()


def test_every_test_that_needs_no_network_is_marked(request):
    items = request.session.items
    missing = [
        i.nodeid
        for i in items
        if not {"emulator", "live"} & {m.name for m in i.iter_markers()}
        and i.get_closest_marker("zero_network") is None
    ]
    assert not missing, missing[:5]
    marked_network = [
        i.nodeid
        for i in items
        if {"emulator", "live"} & {m.name for m in i.iter_markers()}
        and i.get_closest_marker("zero_network") is not None
    ]
    assert not marked_network, marked_network[:5]

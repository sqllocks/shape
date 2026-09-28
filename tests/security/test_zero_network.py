import socket

import pytest

pytestmark = [pytest.mark.security, pytest.mark.zero_network]


def test_shape_import_does_not_require_network(monkeypatch):
    def denied(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    import shape

    assert shape.__version__

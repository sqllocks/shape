"""Portal strings use the same driver normalization through every SQL entry point."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from shape_fabric import _tsql

PORTAL = "Data Source=host.datawarehouse.fabric.microsoft.com;Initial Catalog=shape_wh"


@pytest.mark.parametrize("credential", [None, object()])
def test_direct_connection_used_by_publish_cleanup_accepts_portal_string(monkeypatch, credential):
    connections = []
    connection = object()

    def connect(cs, **options):
        connections.append((cs, options))
        if "Driver={ODBC Driver 18 for SQL Server}" not in cs:
            raise RuntimeError("IM002: no default driver specified")
        return connection

    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=connect, Error=RuntimeError))
    monkeypatch.setattr(_tsql, "token_for", lambda cred, scope: "test-token")
    assert _tsql.connect(PORTAL, credential, retries=1) is connection
    cs, options = connections[0]
    assert "Server=host.datawarehouse.fabric.microsoft.com" in cs
    assert "Database=shape_wh" in cs
    assert "Encrypt=yes" in cs and "TrustServerCertificate=no" in cs
    assert ("attrs_before" in options) is (credential is not None)

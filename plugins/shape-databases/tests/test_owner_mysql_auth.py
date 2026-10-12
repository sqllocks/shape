"""MySQL full-authentication driver failure must give actionable guidance."""

import sys
from types import SimpleNamespace

import pytest
from shape_databases import MySqlSink

from shape.errors import ShapeError


def test_caching_sha2_without_tls_is_an_actionable_error(monkeypatch):
    def connect(**kwargs):
        raise AttributeError("'NoneType' object has no attribute 'is_auth_switch_request'")

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(connect=connect))
    with pytest.raises(ShapeError, match="caching_sha2_password.*TLS"):
        MySqlSink().default_connect(ssl_disabled=True)


def test_an_unrelated_driver_error_is_not_mislabelled(monkeypatch):
    def connect(**kwargs):
        raise AttributeError("unrelated bug")

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(connect=connect))
    with pytest.raises(AttributeError, match="unrelated bug"):
        MySqlSink().default_connect(ssl_disabled=True)

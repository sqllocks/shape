"""``shape.plugins.schemes``: scheme detection, local paths and redaction."""

from __future__ import annotations

from pathlib import Path

import pytest

from shape.plugins.schemes import (
    UnsupportedSchemeError,
    local_path,
    redact,
    require_scheme,
    sinks_by_scheme,
    uri_scheme,
)


@pytest.mark.parametrize(
    ("uri", "secret"),
    [
        ("https://acct.blob.core.windows.net/c/x?sv=2020-08-04&sig=SECRETSIG", "SECRETSIG"),
        ("mssql://host/db?password=hunter2", "hunter2"),
        ("mssql://host/db?Pwd=hunter2&encrypt=yes", "hunter2"),
        ("abfss://c@a.dfs.core.windows.net/p?AccountKey=KEY123", "KEY123"),
        ("eventhubs://ns/hub?SharedAccessKey=SAK456", "SAK456"),
        ("postgres://u:pw@host/db?token=TOK", "TOK"),
    ],
)
def test_credentials_in_the_query_string_are_hidden(uri, secret):
    """#371: a SAS signature or a password parameter must not reach a message."""
    out = redact(uri)
    assert secret not in out and "***" in out


def test_redact_keeps_what_is_not_a_secret():
    assert redact("abfss://c@a.dfs.core.windows.net/p/x.parquet") == (
        "abfss://c@a.dfs.core.windows.net/p/x.parquet"
    )
    assert redact("mssql://host/db?encrypt=yes") == "mssql://host/db?encrypt=yes"
    assert redact("postgres://u:pw@host/db") == "postgres://u:***@host/db"
    assert redact("out/dir") == "out/dir"


@pytest.mark.parametrize(
    ("uri", "scheme"),
    [
        ("out/dir", "file"),
        ("/abs/path.parquet", "file"),
        ("file:///tmp/x", "file"),
        ("C:\\data\\x.csv", "file"),
        ("c:/data/x.csv", "file"),
        ("ABFSS://c@a.dfs.core.windows.net/p", "abfss"),
        ("delta+abfss://c@a/p", "delta+abfss"),
        ("mssql://host/db", "mssql"),
    ],
)
def test_uri_scheme(uri, scheme):
    assert uri_scheme(uri) == scheme


def test_local_path_of_a_file_uri_and_a_plain_path():
    assert local_path("file:///tmp/a%20b/x.csv") == Path("/tmp/a b/x.csv")
    assert local_path("out/x.csv") == Path("out/x.csv")
    assert local_path(Path("out")) == Path("out")


class _Sink:
    def __init__(self, name, schemes):
        self.name, self.schemes = name, schemes


class _Host:
    def __init__(self, sinks):
        self.sinks = sinks

    def names(self, group):
        assert group == "shape.sinks"
        return sorted(self.sinks)

    def try_get(self, group, name):
        return self.sinks[name]


def test_sinks_by_scheme_lists_file_first_and_skips_sinks_that_fail_to_load():
    host = _Host(
        {
            "parquet": _Sink("parquet", ["file"]),
            "csv": _Sink("csv", ["FILE", "abfss"]),
            "broken": None,
            "lake": _Sink("lake", ["abfss"]),
        }
    )
    assert sinks_by_scheme(host) == {"file": ["csv", "parquet"], "abfss": ["csv", "lake"]}


def test_require_scheme_accepts_a_declared_scheme():
    require_scheme(_Sink("parquet", ["file"]), "out/x.parquet", host=_Host({}))
    require_scheme(_Sink("lake", ["abfss"]), "abfss://c@a/p", host=_Host({}))


def test_require_scheme_says_what_the_sink_writes_and_who_handles_the_scheme():
    host = _Host({"lake": _Sink("lake", ["abfss"]), "parquet": _Sink("parquet", ["file"])})
    with pytest.raises(UnsupportedSchemeError) as info:
        require_scheme(_Sink("parquet", ["file"]), "abfss://c@a/p", host=host)
    msg = str(info.value)
    assert "the parquet sink writes only to local files" in msg
    assert "provided by the lake sink: shape generate --to abfss://" in msg
    assert "Sinks by scheme: file: parquet; abfss: lake" in msg
    assert isinstance(info.value, ValueError)


def test_require_scheme_for_a_database_and_an_unknown_scheme():
    host = _Host({"parquet": _Sink("parquet", ["file"])})
    with pytest.raises(UnsupportedSchemeError) as info:
        require_scheme(_Sink("parquet", ["file"]), "mssql://u:pw@host/db?password=x", host=host)
    msg = str(info.value)
    assert "SQL Server database sinks are not available yet" in msg
    assert "INSERT scripts" in msg and ":pw@" not in msg and "password=x" not in msg
    with pytest.raises(UnsupportedSchemeError, match="no installed sink writes zz://"):
        require_scheme(_Sink("parquet", ["file"]), "zz://x", host=host)
    with pytest.raises(UnsupportedSchemeError, match="Amazon S3 sinks are not available yet"):
        require_scheme(_Sink("both", ["file", "abfss"]), "s3://b/k", host=host)
    with pytest.raises(UnsupportedSchemeError, match="local files and URIs of scheme abfss://"):
        require_scheme(_Sink("both", ["file", "abfss"]), "s3://b/k", host=host)
    with pytest.raises(UnsupportedSchemeError, match="writes only to URIs of scheme abfss://"):
        require_scheme(_Sink("lake", ["abfss"]), "out/x", host=_Host({}))

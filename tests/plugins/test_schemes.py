"""``shape.plugins.schemes``: scheme detection, local paths and redaction."""

from __future__ import annotations

import pytest

from shape.plugins.schemes import redact


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

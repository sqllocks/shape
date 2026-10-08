"""P7-04 security review regression tests for the SQL Server plugin (moved here from
tests/security/test_p7_04_review.py so they run with the plugin installed instead of being
skipped by the core suite). Each failed before its fix."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.security


def test_sqlserver_redaction_hides_quoted_secrets():
    from shape_sqlserver.sql import redact_connection_string as red

    assert "q" not in red('Server=s;PWD="p;q";UID=u')
    assert "q" not in red("Server=s;pwd = 'p;q';UID=u")
    assert "hunter" not in red("Server=s;Client_Secret=hunter2;UID=u")


def test_connection_string_extra_keys_cannot_inject_attributes():
    from shape_sqlserver.sql import SqlServerError, build_connection_string

    with pytest.raises(SqlServerError):
        build_connection_string("s", extra={"Application Name=x;Trusted_Connection": "yes"})
    assert "ApplicationIntent=ReadOnly" in build_connection_string(
        "s", extra={"ApplicationIntent": "ReadOnly"}
    )

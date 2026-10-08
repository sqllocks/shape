"""AUD-security2 #299: an error the Fabric UDF returns to its (remote) caller never carries a
credential from the exception it wraps."""

from __future__ import annotations

import pytest

from shape.integrations.fabric import udf

SAS = "sv=2024-01-01&sig=UDFSECRET"


class _Client:
    def get_file_properties(self):
        raise OSError(f"403 from https://acct.dfs.core.windows.net/f/x.csv?{SAS}")


class _Files:
    def get_file_client(self, path):
        return _Client()


class _Lakehouse:
    def connectToFiles(self):  # noqa: N802 - the Fabric SDK's name
        return _Files()


def test_a_wrapped_storage_error_is_redacted():
    with pytest.raises(udf.UserThrownError) as caught:
        udf._read_bytes(_Lakehouse(), "Files/x.csv")
    assert "UDFSECRET" not in str(caught.value) + repr(getattr(caught.value, "message", ""))
    assert "Cannot read" in str(caught.value)

"""kv:// references: a trailing newline is not part of a valid name (#453)."""

from __future__ import annotations

import pytest
from shape_fabric import keyvault as kv

from shape.security.credrefs import CredentialReferenceError


@pytest.mark.parametrize(
    "rest", ["myvault/sec\n", "myvault\n/sec", "myvault/sec/abc123\n", "myvault/sec\r"]
)
def test_a_line_break_in_a_reference_is_refused(rest: str) -> None:
    with pytest.raises(CredentialReferenceError):
        kv.parse(rest)


def test_a_valid_reference_parses() -> None:
    assert kv.parse("my-vault/db-password/0123abcd") == ("my-vault", "db-password", "0123abcd")

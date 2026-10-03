"""An empty token is an AuthError; the fallback credential reports both failures (#452)."""

from __future__ import annotations

from typing import Any

import pytest
from shape_fabric._auth import StaticCredential, token_for
from shape_fabric.auth import _FallbackCredential
from shape_fabric.errors import AuthError


@pytest.mark.parametrize(
    "credential", [lambda scope: None, lambda scope: "", StaticCredential(""), lambda scope: "  "]
)
def test_an_empty_token_is_an_auth_error(credential: Any) -> None:
    with pytest.raises(AuthError, match="no token"):
        token_for(credential, "https://example/.default")


class _Fails:
    def __init__(self, message: str) -> None:
        self.message = message

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        raise RuntimeError(self.message)


def test_the_fallback_names_both_failures() -> None:
    both = _FallbackCredential(_Fails("notebook identity gave nothing"), lambda: _Fails("IMDS down"))
    with pytest.raises(AuthError) as err:
        both.get_token("https://example/.default")
    text = str(err.value)
    assert "notebook identity gave nothing" in text and "IMDS down" in text


def test_the_fallback_hides_secrets_in_the_messages() -> None:
    both = _FallbackCredential(
        _Fails("bad: Password=hunter2pass"), lambda: _Fails("bad: client_secret=abcdefgh123")
    )
    with pytest.raises(AuthError) as err:
        both.get_token("https://example/.default")
    assert "hunter2pass" not in str(err.value) and "abcdefgh123" not in str(err.value)


def test_the_fallback_still_returns_the_second_token() -> None:
    both = _FallbackCredential(_Fails("no"), lambda: StaticCredential("t" * 30))
    assert both.get_token("s").token == "t" * 30

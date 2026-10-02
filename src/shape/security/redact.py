"""Hide secrets in text that is about to be shown: error messages, log lines, reports.

:func:`redact_text` replaces the value of a password, key or token wherever it appears, in a
connection string (``PWD=...``, ``AccountKey=...``) and also inside a longer sentence, plus bearer
tokens, JWTs and PEM private keys. It is the last line of defence: code that handles a secret
should not let it reach a message at all, and the command line passes every error through here as
well.
"""

from __future__ import annotations

import re

MASK = "***"

_KEYS = (
    r"pwd|password|passwd|accountkey|sharedaccesskey|sharedaccesssignature|sas_token|sig|"
    r"client_secret|clientsecret|secret|access_token|accesstoken|access token|token"
)
_PAIR = re.compile(
    rf"(?i)\b({_KEYS})(\s*[=:]\s*)(\{{(?:[^}}]|\}}\}})*\}}|\"[^\"]*\"|'[^']*'|[^;&\s\"',]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)
_URL_USER = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+(@)")


def redact_text(text: str) -> str:
    """``text`` with secrets replaced by ``***``."""
    text = _PEM.sub(MASK, text)
    text = _JWT.sub(MASK, text)
    text = _BEARER.sub(rf"\1{MASK}", text)
    text = _URL_USER.sub(rf"\1{MASK}\2", text)
    return _PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)


def holds_secret(connection_string: str) -> bool:
    """Whether ``connection_string`` carries a password or key (``PWD=``, ``Password=``,
    ``AccountKey=``, ``SharedAccessKey=``, ``SharedAccessSignature=``)."""
    return (
        re.search(
            r"(?i)\b(pwd|password|accountkey|sharedaccesskey|sharedaccesssignature)\s*=",
            connection_string,
        )
        is not None
    )

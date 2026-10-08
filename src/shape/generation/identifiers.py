"""The run switch for identifier values (issue #766): ``reserved`` (the default) or ``realistic``.

The identifier providers of the ``native`` and ``faker`` strategies (e-mail addresses, URIs, phone
numbers, social security numbers) produce values that cannot belong to a real person unless asked
(``docs/GENERATION_STRATEGIES.md``). One switch turns the realistic forms on for a whole run. The
mode in effect for a column is decided, most specific first, by:

1. the column's own ``domains`` or ``range`` key;
2. the run switch (``shape.generate(..., identifiers=...)``, ``--identifiers``, the bridge's
   ``identifiers`` argument, a scenario spec's ``scenario.identifiers``);
3. the schema's top-level ``"identifiers"``;
4. ``reserved``.

The engine resolves 2 and 3 into its own copy of the schema (``Engine.identifiers``), so the
schema a chunk worker or a Spark job rebuilds the engine from carries the same mode, and every
strategy reads it from its context (``EngineContext.identifiers``).
"""

from __future__ import annotations

import sys
from typing import Any, TextIO

RESERVED = "reserved"
REALISTIC = "realistic"
IDENTIFIER_MODES = (RESERVED, REALISTIC)
DEFAULT_IDENTIFIERS = RESERVED

REALISTIC_NOTICE = (
    "shape: realistic identifiers are on: e-mail addresses, URIs, phone numbers and social "
    "security numbers can belong to real people; never use this data outside a test system"
)


def check_identifiers(value: Any, where: str = "identifiers") -> str:
    """``value`` if it is an identifier mode; otherwise a ``ValueError`` that names the modes."""
    if isinstance(value, str) and value in IDENTIFIER_MODES:
        return value
    raise ValueError(f"{where} must be {' or '.join(IDENTIFIER_MODES)}, not {value!r}")


def announce(mode: str, stream: TextIO | None = None) -> None:
    """Say on standard error (or ``stream``) that realistic identifiers are on; nothing for
    ``reserved``. Each command calls it once per run."""
    if mode == REALISTIC:
        print(REALISTIC_NOTICE, file=stream if stream is not None else sys.stderr)


__all__ = [
    "DEFAULT_IDENTIFIERS",
    "IDENTIFIER_MODES",
    "REALISTIC",
    "REALISTIC_NOTICE",
    "RESERVED",
    "announce",
    "check_identifiers",
]

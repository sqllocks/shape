"""SQL script text that data cannot turn into statements (#724, #285).

The SQL sink, the design DDL and the contract DDL write scripts that a client runs as written.

* **Identifiers** with a control character are refused. ``sqlcmd``, SSMS and Azure Data Studio
  split a T-SQL script on every line that holds only ``GO``, even inside a bracketed name, so a
  name with a line break could start a statement of its own.
* **T-SQL string literals** never hold a raw line break: ``a\\nb`` is written as
  ``N'a' + NCHAR(10) + N'b'`` (``'a' + CHAR(10) + 'b'`` for Fabric Warehouse, whose strings are
  ``VARCHAR``). A value without a line break keeps its bytes. Concatenating strings that are not
  ``MAX`` truncates at 8,000 bytes, so a longer value starts with ``CAST(N'' AS NVARCHAR(MAX))``.
* **PostgreSQL literals** with a backslash are written as ``E'...'`` with the backslash doubled,
  which reads the same whatever ``standard_conforming_strings`` says. Literals without a
  backslash keep their bytes.
"""

from __future__ import annotations

import re

_LINE_BREAK = re.compile(r"(\r|\n)")
_TSQL_LIMIT_BYTES = 8000


def has_control_character(text: str) -> bool:
    """A C0 or C1 control character, or a Unicode line or paragraph separator."""
    return any(ord(c) < 32 or 127 <= ord(c) < 160 or c in "  " for c in text)


def check_identifier(name: str) -> str:
    """``name`` unchanged, or a ``ValueError`` when it holds a control character."""
    if has_control_character(name):
        raise ValueError(
            f"SQL name {name!r} holds a control character (such as a line break); "
            "it cannot be written to a script"
        )
    return name


def tsql_text(escaped: str, *, national: bool) -> str:
    """A T-SQL string literal of ``escaped`` (quotes already doubled), with each line break
    written as ``NCHAR(10)`` / ``CHAR(13)`` and so on, never as a raw line break."""
    prefix, char = ("N", "NCHAR") if national else ("", "CHAR")
    if not _LINE_BREAK.search(escaped):
        return f"{prefix}'{escaped}'"
    pieces = [
        f"{char}({ord(part)})" if part in ("\r", "\n") else f"{prefix}'{part}'"
        for part in _LINE_BREAK.split(escaped)
        if part
    ]
    size = len(escaped) * 2 if national else len(escaped.encode("utf-8"))
    if size > _TSQL_LIMIT_BYTES:
        pieces.insert(0, f"CAST({prefix}'' AS {'NVARCHAR' if national else 'VARCHAR'}(MAX))")
    return " + ".join(pieces)


def postgres_text(escaped: str) -> str:
    """A PostgreSQL string literal of ``escaped`` (quotes already doubled)."""
    if "\\" not in escaped:
        return f"'{escaped}'"
    return "E'" + escaped.replace("\\", "\\\\") + "'"

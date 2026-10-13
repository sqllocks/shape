"""The three acceptance checks of P6-03, written independently of either implementation.

Every function takes the original and the masked data as ``{column: [str | None]}`` (all values
read as text, so nothing is re-typed) and returns a list of problems (empty = the check passes).
Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

Column = Sequence["str | None"]
Table = Mapping[str, Column]

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
WORDS = re.compile(r"^[^\W\d_]+(?:[ '.\-,][^\W\d_]+)*\.?$")  # letters, with spaces/hyphens inside
IDENTIFIER_RATIO = 0.2  # distinct/non-null above this: an identifier, not a category
MIN_SCAN_LENGTH = 5  # shorter original values are not scanned for in other columns


def _same(a: str | None, b: str | None) -> bool:
    """Equal as text, or equal as numbers (a tool may write 10 as 10.0)."""
    if a == b:
        return True
    if a is None or b is None:
        return False
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def changed_columns(original: Table, masked: Table) -> set[str]:
    """The columns where at least one value differs (as text and as a number)."""
    return {
        c
        for c, col in original.items()
        if c in masked and any(not _same(a, b) for a, b in zip(col, masked[c], strict=False))
    }


def collapse(value: str) -> str:
    """The format of a value: digits 9, lower-case letters a, upper-case A, runs collapsed."""
    s = re.sub(r"[A-Z]", "A", re.sub(r"[a-z]", "a", re.sub(r"\d", "9", value)))
    return re.sub(r"(.)\1+", r"\1", s)


def _valid_ipv4(v: str) -> bool:
    m = IPV4.match(v)
    return bool(m) and all(int(g) <= 255 for g in m.groups())


def _present(col: Column) -> list[str]:
    return [v for v in col if v is not None]


def check_structure(original: Table, masked: Table) -> list[str]:
    """Same columns in the same order, same row count, nulls in the same places."""
    problems = []
    if list(original) != list(masked):
        problems.append(f"columns differ: {list(original)} vs {list(masked)}")
        return problems
    for c, col in original.items():
        if len(col) != len(masked[c]):
            problems.append(f"{c}: {len(col)} rows became {len(masked[c])}")
        elif [v is None for v in col] != [v is None for v in masked[c]]:
            problems.append(f"{c}: null positions changed")
    return problems


def check_columns(original: Table, masked: Table, expected: set[str]) -> list[str]:
    """The columns that changed are exactly ``expected``."""
    got = changed_columns(original, masked)
    problems = []
    if got - expected:
        problems.append(f"unexpectedly changed: {sorted(got - expected)}")
    if expected - got:
        problems.append(f"expected to change but did not: {sorted(expected - got)}")
    return problems


def check_format(original: Table, masked: Table, columns: set[str]) -> list[str]:
    """Each masked value has the format of the column's original values: an e-mail address
    stays one, an IPv4 address stays a valid one, a word or name stays words (letters with
    spaces and hyphens), anything else has a format (digit and letter
    layout, punctuation) found among the originals."""
    problems = []
    for c in sorted(columns):
        orig, new = _present(original[c]), _present(masked[c])
        if not orig:
            continue
        if sum(bool(EMAIL.match(v)) for v in orig) >= 0.95 * len(orig):
            bad = [v for v in new if not EMAIL.match(v)]
        elif sum(_valid_ipv4(v) for v in orig) >= 0.95 * len(orig):
            bad = [v for v in new if not _valid_ipv4(v)]
        elif sum(bool(WORDS.match(v)) for v in orig) >= 0.95 * len(orig):
            bad = [v for v in new if not WORDS.match(v)]  # a name stays a name
        else:
            formats = {collapse(v) for v in orig}
            bad = [v for v in new if collapse(v) not in formats]
        if bad:
            problems.append(
                f"{c}: {len(bad)} of {len(new)} values break the format, e.g. {bad[0]!r}"
            )
    return problems


def check_no_original(original: Table, masked: Table, columns: set[str]) -> list[str]:
    """No masked cell keeps its original value; in an identifier-like column no masked value is
    any original value of that column; and no original value of an identifier-like masked column
    (at least 5 characters) appears in any column of the output."""
    problems = []
    scan: dict[str, set[str]] = {}
    for c in sorted(columns):
        pairs = [(a, b) for a, b in zip(original[c], masked[c], strict=False) if a is not None]
        kept = sum(1 for a, b in pairs if b is not None and _same(a, b))
        if kept:
            problems.append(f"{c}: {kept} of {len(pairs)} cells keep their original value")
        orig = {a for a, _ in pairs}
        if orig and len(orig) / len(pairs) >= IDENTIFIER_RATIO:
            reused = sum(1 for _, b in pairs if b in orig)
            if reused:
                problems.append(f"{c}: {reused} masked values are original values of the column")
            scan[c] = {v for v in orig if len(v) >= MIN_SCAN_LENGTH}
    everything: set[str] = set().union(*scan.values())
    for c, col in masked.items():
        own = scan.get(c, set())
        hits = sum(1 for v in col if v is not None and v in everything and v not in own)
        if hits:
            problems.append(f"{c}: {hits} cells hold an original value of another masked column")
    return problems

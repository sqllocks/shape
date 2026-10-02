"""The deliberate differences of Shape's domain output from the baseline's (ISS-gen).

The owner decided (2026-10-01) to fix behaviour of the baseline that harms trust, and to record
each such difference in a narrow, named allow-list with its reason (``ddl_1to1/differences.py`` and
``strategy_1to1/differences.py`` are the others). ``verify.py`` accepts a column that fails **only**
the listed checks, and only when the column also meets the replacement rule given here; a listed
column that passes every check fails the run (the entry is stale).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

RESERVED_DOMAINS = frozenset({"example.com", "example.org", "example.net"})


@dataclass(frozen=True)
class Deliberate:
    fails: tuple[str, ...]  # the checks of compare_column that fail, and nothing else
    reason: str
    accepts: Callable[[Any], bool]  # the replacement rule, on the implementation's column


def _reserved_email_domains(column: Any) -> bool:
    values = column.dropna().astype(str)
    return bool(len(values)) and all(v.rpartition("@")[2] in RESERVED_DOMAINS for v in values)


# (domain, table, column) -> difference
DELIBERATE: dict[tuple[str, str, str], Deliberate] = {
    ("retail", "customer", "email"): Deliberate(
        fails=("vocab",),
        reason=(
            "The baseline's addresses are at 50 real mail providers (gmail.com, ...), so a "
            "synthetic customer can be a real person's mailbox. Shape uses example.com, "
            "example.org and example.net, which can never be one (owner issue 11). The null "
            "rate and the distinct-value ratio are still compared."
        ),
        accepts=_reserved_email_domains,
    ),
}

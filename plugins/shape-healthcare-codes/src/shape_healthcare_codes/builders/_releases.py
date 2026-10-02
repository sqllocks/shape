"""Shared by the builders of code sets that change release to release (ICD-10-CM, ICD-10-PCS):
which releases each code appears in, as bit masks, and the text of the newest one."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from shape_healthcare_codes.model import Release


@dataclass(frozen=True, slots=True)
class OrderRow:
    code: str
    leaf: bool
    short_desc: str
    long_desc: str


def parse_order(text: str) -> list[OrderRow]:
    """Parse a CDC or CMS order file. Fixed width: order(5) code(cols 7-13) flag(15)
    short(17-76) long(78-); a line that does not fit the layout is an error, not skipped."""
    rows: list[OrderRow] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(line) < 78 or not line[:5].isdigit() or line[14] not in "01":
            raise ValueError(f"order file line {n} is not in the CDC layout: {line[:60]!r}")
        rows.append(
            OrderRow(
                line[6:13].strip().upper(),
                line[14] == "1",
                line[16:76].strip(),
                line[77:].strip(),
            )
        )
    return rows


def read_text(path: Path, member: str | None) -> str:
    """The text of a plain file, or of the zip member whose base name matches ``member``."""
    if member is None:
        return path.read_text(encoding="utf-8", errors="replace")
    with zipfile.ZipFile(path) as z:
        names = [
            n
            for n in z.namelist()
            if re.search(member, n.rsplit("/", 1)[-1], re.I) and not n.endswith("/")
        ]
        if not names:
            raise ValueError(f"{path.name}: no member matching {member!r}")
        return z.read(sorted(names, key=len)[0]).decode("utf-8", errors="replace")


@dataclass(slots=True)
class Acc:
    vmask: int = 0
    lmask: int = 0
    first: int = 0
    last: int = 0
    short: str = ""
    long: str = ""
    leaf: bool = False


def accumulate(per_release: Sequence[tuple[Release, Iterable[OrderRow]]]) -> dict[str, Acc]:
    """Per code: the releases it is in (``vmask``), in which it is billable (``lmask``), the
    first and last release index, and the text and flag of the newest release that has it."""
    eff = [r.effective for r, _ in per_release]
    if eff != sorted(eff) or len(set(eff)) != len(eff):
        raise ValueError("releases must be given oldest first, with distinct effective dates")
    acc: dict[str, Acc] = {}
    for k, (_, rows) in enumerate(per_release):
        for r in rows:
            e = acc.get(r.code)
            if e is None:
                e = acc[r.code] = Acc(first=k)
            e.vmask |= 1 << k
            if r.leaf:
                e.lmask |= 1 << k
            e.short, e.long, e.leaf, e.last = r.short_desc, r.long_desc, r.leaf, k
    return acc

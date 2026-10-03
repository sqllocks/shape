"""X12 envelope and segment building shared by 837P, 837I, 835 and 834 (005010).

One interchange per file: ``ISA`` / ``GS`` / one or more ``ST`` ... ``SE`` / ``GE`` / ``IEA``,
with control numbers that agree between header and trailer and segment counts computed from
what was written. The sender and receiver ids default to clearly synthetic values, and the
interchange is marked as a test (``ISA15 = T``).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..contract import ContractError

_UNSAFE = re.compile(r"[*~:^\r\n\t|]+")


@dataclass(frozen=True, slots=True)
class Delimiters:
    """The separators of an interchange: element, component (ISA16), repetition and segment."""

    element: str = "*"
    component: str = ":"
    repetition: str = "^"
    segment: str = "~"


@dataclass(frozen=True, slots=True)
class EnvelopeOptions:
    """Interchange and functional-group header values. All defaults are synthetic."""

    sender_id: str = "SYNTHSENDER"
    receiver_id: str = "SYNTHRECEIVER"
    sender_qualifier: str = "ZZ"
    receiver_qualifier: str = "ZZ"
    application_sender: str = "SYNTHSENDER"
    application_receiver: str = "SYNTHRECEIVER"
    usage_indicator: str = "T"
    created: dt.datetime = dt.datetime(2024, 1, 1, 12, 0)
    interchange_control: int = 1
    group_control: int = 1
    first_set_control: int = 1
    delimiters: Delimiters = field(default_factory=Delimiters)
    newline: bool = False


def clean(value: object, *, upper: bool = True, limit: int | None = None) -> str:
    """Text safe for an element: separators and control characters become spaces."""
    text = _UNSAFE.sub(" ", "" if value is None else str(value)).strip()
    text = re.sub(r"\s{2,}", " ", text)
    if upper:
        text = text.upper()
    return text[:limit] if limit else text


def need(value: object, what: str, where: str) -> str:
    """A required value as text, or a clear error naming the field and the record."""
    text = "" if value is None else str(value).strip()
    if not text:
        raise ContractError(f"{where}: {what} is required by the standard and is empty")
    return text


class Segments:
    """An ordered list of segments of one transaction set, built element by element."""

    def __init__(self, delimiters: Delimiters) -> None:
        self._d = delimiters
        self._items: list[str] = []

    def add(self, seg_id: str, *elements: object) -> None:
        """Append a segment; trailing empty elements are dropped."""
        values = ["" if e is None else str(e) for e in elements]
        while values and values[-1] == "":
            values.pop()
        self._items.append(self._d.element.join([seg_id, *values]))

    def comp(self, *parts: object) -> str:
        """A composite element: components joined by the component separator."""
        values = ["" if p is None else str(p) for p in parts]
        while values and values[-1] == "":
            values.pop()
        return self._d.component.join(values)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> list[str]:
        return self._items


def _pad(value: str, width: int) -> str:
    return value[:width].ljust(width)


def interchange(
    sets: Sequence[Sequence[str]],
    *,
    functional_id: str,
    version: str,
    options: EnvelopeOptions,
) -> str:
    """Wrap transaction sets (each a list of segments from ``ST`` to ``SE``) in an interchange.

    The sets must already carry their own ``ST`` and ``SE``; this adds ISA, GS, GE and IEA.
    """
    d = options.delimiters
    e, s = d.element, d.segment
    ic = f"{options.interchange_control:09d}"
    gc = str(options.group_control)
    isa = e.join(
        [
            "ISA",
            "00",
            _pad("", 10),
            "00",
            _pad("", 10),
            options.sender_qualifier,
            _pad(options.sender_id, 15),
            options.receiver_qualifier,
            _pad(options.receiver_id, 15),
            options.created.strftime("%y%m%d"),
            options.created.strftime("%H%M"),
            d.repetition,
            "00501",
            ic,
            "0",
            options.usage_indicator,
            d.component,
        ]
    )
    gs = e.join(
        [
            "GS",
            functional_id,
            options.application_sender,
            options.application_receiver,
            options.created.strftime("%Y%m%d"),
            options.created.strftime("%H%M"),
            gc,
            "X",
            version,
        ]
    )
    lines = [isa, gs]
    for block in sets:
        lines.extend(block)
    lines.append(e.join(["GE", str(len(sets)), gc]))
    lines.append(e.join(["IEA", "1", ic]))
    sep = s + ("\n" if options.newline else "")
    return sep.join(lines) + sep


def transaction(
    body: Iterable[str],
    *,
    set_id: str,
    version: str | None,
    control: int,
    delimiters: Delimiters,
) -> list[str]:
    """``ST`` + body + ``SE`` with the segment count (ST and SE included) and control number.

    ``version`` is the implementation convention reference (ST03); ``None`` leaves it out, as
    the 835 and 834 guides require.
    """
    e = delimiters.element
    ctl = f"{control:04d}"
    head = ["ST", set_id, ctl] + ([version] if version else [])
    items = [e.join(head), *body]
    items.append(e.join(["SE", str(len(items) + 1), ctl]))
    return items

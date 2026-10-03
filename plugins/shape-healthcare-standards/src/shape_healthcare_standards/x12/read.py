"""Read X12 (005010) interchanges into Arrow tables: structure only, no code interpretation.

``parse_x12`` reads the delimiters from each ISA segment (fixed 106 characters: ISA01..ISA16 and
the segment terminator), then every interchange, group and transaction set into
:class:`Segment` rows. Envelope rules are checked as the segments go by: ISA13 = IEA02, IEA01 =
the number of groups, GS06 = GE02, GE01 = the number of sets, ST02 = SE02 and SE01 = the number
of segments from ST to SE. A broken rule raises ``ValueError`` naming the segment and its
position; with ``strict=False`` it is recorded as a :class:`Problem` instead.

For 005010X221A1 (the 835), each segment also gets the loop it belongs to, and
:func:`loop_tables` builds one table per loop with columns named by segment and element position
(``CLP01``, ``SVC01_1``, ``CAS02``). The code here reads segments; the writers' segment code is in
:mod:`.core`, and the delimiters are its :class:`~.core.Delimiters`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

from .core import Delimiters

if TYPE_CHECKING:
    from shape.generation.schema import Relationship

ISA_LENGTH = 106
V835 = "005010X221A1"
LOOP_TABLES = ("header", "1000A", "1000B", "2000", "2100", "2110")
_PARENT = {"1000A": "header", "1000B": "header", "2000": "header", "2100": "2000", "2110": "2100"}
SEGMENT_SCHEMA = pa.schema(
    [
        ("interchange_control", pa.string()),
        ("group_control", pa.string()),
        ("transaction_control", pa.string()),
        ("position", pa.int64()),
        ("segment_id", pa.string()),
        ("loop_id", pa.string()),
        ("elements", pa.list_(pa.string())),
    ]
)
PROBLEM_SCHEMA = pa.schema(
    [
        ("interchange_control", pa.string()),
        ("segment_id", pa.string()),
        ("position", pa.int64()),
        ("message", pa.string()),
    ]
)


@dataclass(slots=True)
class Segment:
    interchange_control: str
    group_control: str | None
    transaction_control: str | None
    position: int
    segment_id: str
    loop_id: str | None
    elements: list[str]
    delimiters: Delimiters
    loop_row: int | None = None


@dataclass(frozen=True, slots=True)
class Problem:
    interchange_control: str | None
    segment_id: str
    position: int
    message: str


@dataclass(slots=True)
class _Loop:
    """One instance of a loop of an 835: its segments, and the instance it hangs from."""

    kind: str
    row_id: int
    parent: int | None
    control: tuple[str, str | None, str | None]
    segments: list[Segment] = field(default_factory=list)


@dataclass(slots=True)
class X12Result:
    segments: list[Segment]
    problems: list[Problem]
    loops: list[_Loop]


class _Reader:
    def __init__(self, text: str, strict: bool) -> None:
        self.text = text
        self.strict = strict
        self.segments: list[Segment] = []
        self.problems: list[Problem] = []
        self.loops: list[_Loop] = []
        self._ids = dict.fromkeys(LOOP_TABLES, 0)

    def problem(self, control: str | None, seg_id: str, position: int, message: str) -> None:
        if self.strict:
            raise ValueError(f"{seg_id} at position {position}: {message}")
        self.problems.append(Problem(control, seg_id, position, message))

    # -- envelope ---------------------------------------------------------------------------

    def delimiters_at(self, pos: int) -> Delimiters:
        text = self.text
        if not text.startswith("ISA", pos):
            raise ValueError(
                f"expected an ISA segment at character {pos}, found {text[pos : pos + 3]!r}"
            )
        have = len(text) - pos
        if have < ISA_LENGTH:
            raise ValueError(
                f"ISA segment is truncated: it is {ISA_LENGTH} characters and only {have} remain"
            )
        element, repetition = text[pos + 3], text[pos + 82]
        component, segment = text[pos + 104], text[pos + 105]
        seps = {element, component, segment}
        if len(seps) != 3 or any(c.isalnum() or c.isspace() for c in (element, component)):
            raise ValueError(
                f"ISA segment has unusable delimiters: element {element!r}, "
                f"component {component!r}, segment {segment!r}"
            )
        if text[pos : pos + 105].count(element) != 16:
            raise ValueError("ISA segment must have 16 elements (ISA01 to ISA16)")
        return Delimiters(element, component, repetition, segment)

    def run(self) -> X12Result:
        text, pos = self.text, 0
        while True:
            while pos < len(text) and text[pos] in " \t\r\n\x00":
                pos += 1
            if pos >= len(text):
                break
            pos = self.interchange(pos)
        return X12Result(self.segments, self.problems, self.loops)

    def interchange(self, pos: int) -> int:
        d = self.delimiters_at(pos)
        text, term = self.text, d.segment
        newline_term = term in "\r\n"
        ic = ""
        group: str | None = None
        version = ""
        groups = sets = 0
        txn: str | None = None
        set_id = ""
        in_set = 0
        number = 0
        loops = _Loops(self)
        isa_pos = 1
        closed = False
        st_pos = gs_pos = 0
        while pos < len(text) and not closed:
            end = text.find(term, pos)
            raw = text[pos:] if end < 0 else text[pos:end]
            pos = len(text) if end < 0 else end + 1
            seg_text = raw.strip("\r\n\t ") if not newline_term else raw.strip("\r\t ")
            if not seg_text:
                continue
            number += 1
            parts = seg_text.split(d.element)
            sid, elements = parts[0], parts[1:]
            if end < 0:
                self.problem(ic or None, sid, number, "segment is not terminated")
            if number == 1:
                ic = elements[12]
            if sid == "GS":
                if group is not None:
                    self.problem(ic, "GS", number, "GS inside an open group (missing GE)")
                group, version = (
                    elements[5] if len(elements) > 5 else "",
                    (elements[7] if len(elements) > 7 else ""),
                )
                groups += 1
                sets = 0
                gs_pos = number
            elif sid == "ST":
                if txn is not None:
                    self.problem(ic, "ST", number, "ST inside an open transaction set (no SE)")
                if group is None:
                    self.problem(ic, "ST", number, "ST outside a functional group")
                txn = elements[1] if len(elements) > 1 else ""
                set_id = elements[0] if elements else ""
                sets += 1
                in_set = 0
                st_pos = number
                loops.begin(set_id == "835" and version == V835, (ic, group, txn))
            seg_txn = txn if txn is not None else None
            if txn is not None:
                in_set += 1
            seg = Segment(
                ic,
                group if sid not in ("ISA", "IEA") else None,
                seg_txn,
                number,
                sid,
                None,
                elements,
                d,
            )
            self.segments.append(seg)
            if txn is not None:
                loops.assign(seg)
            if sid == "SE":
                self.check_se(ic, number, elements, txn, in_set)
                txn = None
            elif sid == "GE":
                if txn is not None:
                    self.problem(ic, "ST", st_pos, "transaction set has no SE segment")
                    txn = None
                self.check_ge(ic, number, elements, group, sets)
                group = None
            elif sid == "IEA":
                if group is not None:
                    self.problem(ic, "GS", gs_pos, "functional group has no GE segment")
                    group = None
                self.check_iea(ic, number, elements, groups)
                closed = True
            if sid == "ISA":
                isa_pos = number
        if not closed:
            if txn is not None:
                self.problem(ic, "ST", st_pos, "transaction set has no SE segment")
            if group is not None:
                self.problem(ic, "GS", gs_pos, "functional group has no GE segment")
            self.problem(ic, "ISA", isa_pos, "interchange has no IEA segment")
        return pos

    def check_se(self, ic: str, pos: int, el: list[str], txn: str | None, count: int) -> None:
        if txn is None:
            self.problem(ic, "SE", pos, "SE without a matching ST")
            return
        if len(el) < 2 or el[1] != txn:
            self.problem(
                ic, "SE", pos, f"SE02 {el[1] if len(el) > 1 else ''!r} does not match ST02 {txn!r}"
            )
        if not el or el[0] != str(count):
            self.problem(
                ic,
                "SE",
                pos,
                f"SE01 says {el[0] if el else ''!r} segments but the set has {count} (ST to SE)",
            )

    def check_ge(self, ic: str, pos: int, el: list[str], group: str | None, sets: int) -> None:
        if group is None:
            self.problem(ic, "GE", pos, "GE without a matching GS")
            return
        if len(el) < 2 or el[1] != group:
            self.problem(
                ic,
                "GE",
                pos,
                f"GE02 {el[1] if len(el) > 1 else ''!r} does not match GS06 {group!r}",
            )
        if not el or el[0] != str(sets):
            self.problem(
                ic, "GE", pos, f"GE01 says {el[0] if el else ''!r} sets but the group has {sets}"
            )

    def check_iea(self, ic: str, pos: int, el: list[str], groups: int) -> None:
        if len(el) < 2 or el[1] != ic:
            self.problem(
                ic,
                "IEA",
                pos,
                f"IEA02 {el[1] if len(el) > 1 else ''!r} does not match ISA13 {ic!r}",
            )
        if not el or el[0] != str(groups):
            self.problem(
                ic, "IEA", pos, f"IEA01 says {el[0] if el else ''!r} groups but there are {groups}"
            )


class _Loops:
    """The 835 loop of each segment of a transaction set (005010X221A1)."""

    def __init__(self, reader: _Reader) -> None:
        self.r = reader
        self.on = False
        self.control: tuple[str, str | None, str | None] = ("", None, None)
        self.current: dict[str, _Loop] = {}
        self.where = "header"

    def begin(self, on: bool, control: tuple[str, str | None, str | None]) -> None:
        self.on, self.control, self.current, self.where = on, control, {}, "header"

    def _open(self, kind: str, parent: str | None) -> _Loop:
        r = self.r
        r._ids[kind] += 1
        parent_loop = self.current.get(parent) if parent else None
        loop = _Loop(kind, r._ids[kind], parent_loop.row_id if parent_loop else None, self.control)
        r.loops.append(loop)
        self.current[kind] = loop
        return loop

    def assign(self, seg: Segment) -> None:
        if not self.on:
            return
        sid, el = seg.segment_id, seg.elements
        if sid == "ST":
            self._open("header", None)
            self.where = "header"
        elif sid == "N1" and el and el[0] in ("PR", "PE"):
            self.where = "1000A" if el[0] == "PR" else "1000B"
            self._open(self.where, "header")
        elif sid == "LX":
            self.where = "2000"
            self._open("2000", "header")
            self.current.pop("2100", None)
            self.current.pop("2110", None)
        elif sid == "CLP":
            if "2000" not in self.current:
                self._open("2000", "header")
            self.where = "2100"
            self._open("2100", "2000")
            self.current.pop("2110", None)
        elif sid == "SVC":
            self.where = "2110"
            self._open("2110", "2100")
        elif sid in ("PLB", "SE"):
            self.where = "summary"
        seg.loop_id = self.where
        loop = self.current.get(self.where)
        if loop is not None:
            loop.segments.append(seg)
            seg.loop_row = loop.row_id


def parse_x12(text: str, *, strict: bool = True) -> X12Result:
    """Every segment of every interchange of ``text`` (see the module docstring)."""
    return _Reader(text.lstrip("\ufeff \t\r\n\x00"), strict).run()


def segment_table(segments: list[Segment]) -> pa.Table:
    return pa.table(
        {
            "interchange_control": [s.interchange_control for s in segments],
            "group_control": [s.group_control for s in segments],
            "transaction_control": [s.transaction_control for s in segments],
            "position": [s.position for s in segments],
            "segment_id": [s.segment_id for s in segments],
            "loop_id": [s.loop_id for s in segments],
            "elements": [s.elements for s in segments],
        },
        schema=SEGMENT_SCHEMA,
    )


def problem_table(problems: list[Problem]) -> pa.Table:
    return pa.table(
        {
            "interchange_control": [p.interchange_control for p in problems],
            "segment_id": [p.segment_id for p in problems],
            "position": [p.position for p in problems],
            "message": [p.message for p in problems],
        },
        schema=PROBLEM_SCHEMA,
    )


def _split_widths(loops: list[_Loop]) -> dict[tuple[str, int], int]:
    """(segment id, element position) -> the widest composite there; absent = never composite."""
    widths: dict[tuple[str, int], int] = {}
    for loop in loops:
        d = None
        for seg in loop.segments:
            d = seg.delimiters
            for i, value in enumerate(seg.elements, start=1):
                if d.component in value:
                    key = (seg.segment_id, i)
                    widths[key] = max(widths.get(key, 0), value.count(d.component) + 1)
    return widths


def loop_tables(result: X12Result) -> tuple[dict[str, pa.Table], list[Relationship]]:
    """One table per 835 loop (``loop_header``, ``loop_1000a`` ...) and the links between them.

    Every table has ``_id`` (1, 2, ... in file order), the three control numbers and, below the
    header, ``_parent_id``. Element ``i`` of segment ``SEG`` is column ``SEG{i:02d}``; where an
    element is a composite its components are ``SEG{i:02d}_1``, ``_2`` and so on. A segment that
    repeats inside one loop instance puts its n-th occurrence in the same names suffixed
    ``__n``; the unabridged segments are always in ``x12_segment``."""
    from shape.generation.schema import Relationship

    tables: dict[str, pa.Table] = {}
    for kind in LOOP_TABLES:
        loops = [lp for lp in result.loops if lp.kind == kind]
        widths = _split_widths(loops)
        rows: list[dict[str, Any]] = []
        columns: dict[str, None] = {}
        for loop in loops:
            row: dict[str, Any] = {
                "_id": loop.row_id,
                "interchange_control": loop.control[0],
                "group_control": loop.control[1],
                "transaction_control": loop.control[2],
            }
            if kind != "header":
                row["_parent_id"] = loop.parent
            seen: dict[str, int] = {}
            for seg in loop.segments:
                n = seen[seg.segment_id] = seen.get(seg.segment_id, 0) + 1
                tail = "" if n == 1 else f"__{n}"
                comp = seg.delimiters.component
                for i, value in enumerate(seg.elements, start=1):
                    base = f"{seg.segment_id}{i:02d}"
                    if (seg.segment_id, i) in widths:
                        for j, part in enumerate(value.split(comp), start=1):
                            name = f"{base}_{j}{tail}"
                            row[name] = part or None
                            columns.setdefault(name)
                    else:
                        name = base + tail
                        row[name] = value or None
                        columns.setdefault(name)
            rows.append(row)
        head = ["_id", "interchange_control", "group_control", "transaction_control"]
        if kind != "header":
            head.insert(1, "_parent_id")
        data: dict[str, pa.Array] = {}
        for name in head:
            typ = pa.int64() if name in ("_id", "_parent_id") else pa.string()
            data[name] = pa.array([r.get(name) for r in rows], typ)
        for name in columns:
            data[name] = pa.array([r.get(name) for r in rows], pa.string())
        tables[f"loop_{kind.lower()}"] = pa.table(data)
    rels = [
        Relationship(
            name=f"loop_{kind.lower()}_to_loop_{parent.lower()}",
            parent=f"loop_{parent.lower()}",
            child=f"loop_{kind.lower()}",
            parent_columns=["_id"],
            child_columns=["_parent_id"],
            type="one_to_many",
        )
        for kind, parent in _PARENT.items()
    ]
    return tables, rels

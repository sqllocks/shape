"""Read HL7 v2 messages into Arrow tables: structure only, no message-type validation.

The separators come from each message's own ``MSH`` segment (``MSH-1`` is the field separator,
``MSH-2`` holds the component, repetition, escape and subcomponent characters). MLLP framing
(``0x0B`` ... ``0x1C 0x0D``) is stripped when present, and segments end at ``\\r``, ``\\n`` or
``\\r\\n``. Escape sequences are decoded after splitting, so a decoded separator never splits a
value again: ``\\F\\ \\S\\ \\T\\ \\R\\ \\E\\`` give the separator character, ``\\.br\\`` a newline,
``\\Xhh..\\`` the hex-coded characters, ``\\H\\`` and ``\\N\\`` are dropped, and anything else (for
example ``\\Zxxx\\``) is kept as written.

Subcomponents are not split: a component keeps its subcomponent separators (and a ``\\T\\`` escape
decodes to that same character, so the two cannot be told apart in the decoded text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from shape.generation.schema import Relationship

SEGMENT_SCHEMA = pa.schema(
    [
        ("message_index", pa.int64()),
        ("message_control_id", pa.string()),
        ("message_type", pa.string()),
        ("position", pa.int64()),
        ("segment_id", pa.string()),
        ("fields", pa.list_(pa.list_(pa.list_(pa.string())))),
    ]
)
_LINES = re.compile(r"\r\n|\r|\n")
_SEGMENT_ID = re.compile(r"^[A-Z][A-Z0-9]{2}$")


@dataclass(slots=True)
class Hl7Segment:
    position: int
    segment_id: str
    fields: list[list[list[str]]]


@dataclass(slots=True)
class Hl7Message:
    index: int
    control_id: str | None
    message_type: str | None
    segments: list[Hl7Segment]


def _unescape(text: str, fs: str, comp: str, rep: str, esc: str, sub: str) -> str:
    if esc not in text:
        return text
    fixed = {"F": fs, "S": comp, "T": sub, "R": rep, "E": esc, "H": "", "N": ""}
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] != esc:
            out.append(text[i])
            i += 1
            continue
        j = text.find(esc, i + 1)
        if j < 0:
            out.append(text[i:])
            break
        code = text[i + 1 : j]
        if code in fixed:
            out.append(fixed[code])
        elif code == ".br":
            out.append("\n")
        elif code.startswith("."):
            pass
        elif code[:1] == "X" and len(code) > 1 and len(code) % 2 == 1:
            try:
                out.append(bytes.fromhex(code[1:]).decode("latin-1"))
            except ValueError:
                out.append(text[i : j + 1])
        else:
            out.append(text[i : j + 1])
        i = j + 1
    return "".join(out)


def parse_hl7(text: str) -> list[Hl7Message]:
    """The messages of ``text`` (one or more, MLLP framing allowed)."""
    lines = [ln for ln in _LINES.split(text.replace("\x0b", "").replace("\x1c", "")) if ln.strip()]
    lines[:1] = [lines[0].lstrip("﻿")] if lines else []
    if not lines:
        raise ValueError("HL7 input has no MSH segment: it is empty")
    messages: list[Hl7Message] = []
    seps: tuple[str, str, str, str, str] | None = None
    for line in lines:
        is_msh = line.startswith("MSH") and len(line) > 3 and not line[3].isalnum()
        if is_msh:
            fs = line[3]
            parts = line.split(fs)
            enc = parts[1] if len(parts) > 1 else ""
            if len(enc) < 4:
                raise ValueError(
                    f"MSH-2 of message {len(messages) + 1} must hold the four encoding "
                    f"characters, found {enc!r}"
                )
            seps = (fs, enc[0], enc[1], enc[2], enc[3])
            raw = [fs, *parts[1:]]
            control = raw[9].strip() if len(raw) > 9 and raw[9].strip() else None
            mtype = raw[8] if len(raw) > 8 and raw[8] else None
            messages.append(Hl7Message(len(messages) + 1, control, mtype, []))
        elif not messages:
            raise ValueError(
                f"HL7 message has no MSH segment: the input starts with {line[:3]!r} "
                "(segment 1 must be MSH)"
            )
        assert seps is not None
        fs, comp, rep, esc, sub = seps
        message = messages[-1]
        position = len(message.segments) + 1
        parts = line.split(fs)
        sid = parts[0]
        if not _SEGMENT_ID.match(sid):
            raise ValueError(
                f"message {message.index}, segment {position}: {sid[:8]!r} is not a segment id"
            )
        raw_fields = [fs, *parts[1:]] if sid == "MSH" else parts[1:]
        fields: list[list[list[str]]] = []
        for n, value in enumerate(raw_fields):
            if sid == "MSH" and n < 2:
                fields.append([[value]])
                continue
            fields.append(
                [
                    [_unescape(c, fs, comp, rep, esc, sub) for c in r.split(comp)]
                    for r in value.split(rep)
                ]
            )
        message.segments.append(Hl7Segment(position, sid, fields))
    return messages


def segment_table(messages: list[Hl7Message]) -> pa.Table:
    rows = [(m, s) for m in messages for s in m.segments]
    return pa.table(
        {
            "message_index": [m.index for m, _ in rows],
            "message_control_id": [m.control_id for m, _ in rows],
            "message_type": [m.message_type for m, _ in rows],
            "position": [s.position for _, s in rows],
            "segment_id": [s.segment_id for _, s in rows],
            "fields": [s.fields for _, s in rows],
        },
        schema=SEGMENT_SCHEMA,
    )


def segment_tables(messages: list[Hl7Message]) -> tuple[dict[str, pa.Table], list[Relationship]]:
    """``hl7_message`` (one row per message) and one ``hl7_<id>`` table per segment id.

    Field ``n`` of segment ``SEG`` is column ``SEG_n``; where any value has components they are
    ``SEG_n_1``, ``SEG_n_2`` ...; a field that repeats puts the r-th repetition in the same names
    suffixed ``__r``. An empty value is null here (``hl7_segment`` keeps the empty string)."""
    from shape.generation.schema import Relationship

    tables: dict[str, pa.Table] = {
        "hl7_message": pa.table(
            {
                "message_index": pa.array([m.index for m in messages], pa.int64()),
                "message_control_id": pa.array([m.control_id for m in messages], pa.string()),
                "message_type": pa.array([m.message_type for m in messages], pa.string()),
            }
        )
    }
    by_id: dict[str, list[tuple[Hl7Message, Hl7Segment]]] = {}
    for m in messages:
        for s in m.segments:
            by_id.setdefault(s.segment_id, []).append((m, s))
    rels: list[Relationship] = []
    for sid, items in by_id.items():
        widths: dict[int, int] = {}
        reps: dict[int, int] = {}
        for _, seg in items:
            for n, field in enumerate(seg.fields, start=1):
                reps[n] = max(reps.get(n, 1), len(field))
                width = max(len(r) for r in field)
                if width > 1:
                    widths[n] = max(widths.get(n, 0), width)
        columns: dict[str, list[Any]] = {}
        names: list[str] = []
        for n in sorted(reps):
            for r in range(1, reps[n] + 1):
                tail = "" if r == 1 else f"__{r}"
                if n in widths:
                    names.extend(f"{sid}_{n}_{j}{tail}" for j in range(1, widths[n] + 1))
                else:
                    names.append(f"{sid}_{n}{tail}")
        for name in names:
            columns[name] = []
        for _, seg in items:
            row: dict[str, Any] = {}
            for n, field in enumerate(seg.fields, start=1):
                for r, comps in enumerate(field, start=1):
                    tail = "" if r == 1 else f"__{r}"
                    if n in widths:
                        for j, c in enumerate(comps, start=1):
                            row[f"{sid}_{n}_{j}{tail}"] = c or None
                    else:
                        row[f"{sid}_{n}{tail}"] = comps[0] or None
            for name in names:
                columns[name].append(row.get(name))
        data: dict[str, pa.Array] = {
            "message_index": pa.array([m.index for m, _ in items], pa.int64()),
            "message_control_id": pa.array([m.control_id for m, _ in items], pa.string()),
            "position": pa.array([s.position for _, s in items], pa.int64()),
        }
        for name, values in columns.items():
            data[name] = pa.array(values, pa.string())
        table = f"hl7_{sid.lower()}"
        tables[table] = pa.table(data)
        rels.append(
            Relationship(
                name=f"{table}_to_hl7_message",
                parent="hl7_message",
                child=table,
                parent_columns=["message_index"],
                child_columns=["message_index"],
                type="one_to_many",
            )
        )
    return tables, rels

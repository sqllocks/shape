"""XML records to flat, related Arrow tables (W5-07), by way of :mod:`shape.io.nested`.

Each record element becomes a row. Attributes become ``@name`` columns, the text of an element
that also has attributes or children becomes ``#text``, a leaf child becomes a column named after
it, a child with attributes or children becomes prefixed columns (``address.city``), and a child
that repeats inside its parent (in any record) becomes a child table, as with JSON arrays.
Namespaces are dropped (local names are used). Every value is a string: the reader interprets
nothing.
"""

from __future__ import annotations

import re
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from shape.security.xmlsafe import MAX_XML_DEPTH, parse_xml, xml_depth

from .nested import MAX_DOCUMENT_BYTES, NestedTables, flatten_documents, table_name
from .readers import ReaderError

_STEP = r"(?:[A-Za-z_][\w.\-]*|\*)"
_PATH = re.compile(rf"^(\.?//?)?{_STEP}(?://?{_STEP})*$")
_TOKENS = re.compile(rf"(//?)?({_STEP})")


def _local(name: Any) -> str:
    return str(name).rsplit("}", 1)[-1]


def _children(el: ET.Element) -> list[ET.Element]:
    return [c for c in el if isinstance(c.tag, str)]


def select_records(root: ET.Element, path: str) -> list[ET.Element]:
    """Elements of ``root``'s document picked by ``path``: ``//name`` (anywhere), ``/a/b``
    (from the document), ``a/b`` or ``./a/b`` (from the root element), ``.//name`` (below the
    root), ``*`` for any name. No predicates."""
    if not _PATH.match(path):
        raise ReaderError(
            f"unsupported record path {path!r}: use names joined by '/' or '//' "
            "(for example //order or /orders/order); predicates are not supported"
        )
    doc = ET.Element("#document")
    doc.append(root)
    nodes = [root]
    rest = path
    if rest.startswith(".//"):
        rest = rest[1:]
    elif rest.startswith("./"):
        rest = rest[2:]
    elif rest.startswith("/"):
        nodes = [doc]
    for m in _TOKENS.finditer(rest):
        axis, name = m.group(1), m.group(2)
        nxt: list[ET.Element] = []
        for node in nodes:
            if axis == "//":
                pool = [e for e in node.iter() if isinstance(e.tag, str) and e is not node]
            else:
                pool = _children(node)
            nxt.extend(e for e in pool if name == "*" or _local(e.tag) == name)
        nodes = nxt
    return nodes


def _text(el: ET.Element) -> str | None:
    text = (el.text or "").strip()
    return text or None


def _repeated(records: list[ET.Element]) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    stack: list[tuple[ET.Element, tuple[str, ...]]] = [(r, ()) for r in records]
    while stack:
        el, path = stack.pop()
        seen: dict[str, int] = {}
        for child in _children(el):
            name = _local(child.tag)
            seen[name] = seen.get(name, 0) + 1
            stack.append((child, (*path, name)))
        found.update((*path, n) for n, count in seen.items() if count > 1)
    return found


def _convert(el: ET.Element, path: tuple[str, ...], repeated: set[tuple[str, ...]]) -> Any:
    kids = _children(el)
    if not kids and not el.attrib:
        return _text(el)
    return _record(el, path, repeated)


def _record(
    el: ET.Element, path: tuple[str, ...], repeated: set[tuple[str, ...]]
) -> dict[str, Any]:
    out: dict[str, Any] = {f"@{_local(k)}": v for k, v in el.attrib.items()}
    text = _text(el)
    if text is not None:
        out["#text"] = text
    groups: dict[str, list[ET.Element]] = {}
    for child in _children(el):
        groups.setdefault(_local(child.tag), []).append(child)
    for name, members in groups.items():
        cp = (*path, name)
        if cp in repeated:
            out[name] = [_convert(m, cp, repeated) for m in members]
        else:
            out[name] = _convert(members[0], cp, repeated)
    return out


def read_xml_nested(
    path: str | Path,
    *,
    record: str | None = None,
    name: str | None = None,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> NestedTables:
    """An XML file as related tables. ``record`` picks the row elements (see
    :func:`select_records`); without it the most repeated element under the root is used and
    named in a warning."""
    path = Path(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise ReaderError(
            f"{path} is {size} bytes, over the {max_bytes}-byte limit (option max_bytes)"
        )
    try:
        root = parse_xml(path.read_bytes())
    except ET.ParseError as exc:
        raise ReaderError(f"cannot parse {path} as XML: {exc}") from exc
    if xml_depth(root) > MAX_XML_DEPTH:
        raise ReaderError(f"cannot parse {path} as XML: nested deeper than {MAX_XML_DEPTH} levels")
    notes: list[str] = []
    if record is not None:
        records = select_records(root, record)
        if not records:
            raise ReaderError(f"record path {record!r} matches no element of {path}")
    else:
        counts: dict[str, int] = {}
        for child in _children(root):
            counts[_local(child.tag)] = counts.get(_local(child.tag), 0) + 1
        best = max(counts, key=lambda n: counts[n]) if counts else None
        if best is None or counts[best] == 1:
            records = [root]
            notes.append(
                f"no record path given and no element repeats under <{_local(root.tag)}>; "
                "the root element is the one record"
            )
        else:
            records = [c for c in _children(root) if _local(c.tag) == best]
            notes.append(
                f"no record path given; using the most repeated element <{best}> "
                f"({counts[best]} under <{_local(root.tag)}>)"
            )
    for note in notes:
        warnings.warn(note, UserWarning, stacklevel=3)
    repeated = _repeated(records)
    docs = [_record(r, (), repeated) for r in records]
    result = flatten_documents(name or table_name(_local(records[0].tag)), docs)
    result.warnings[:0] = notes
    return result

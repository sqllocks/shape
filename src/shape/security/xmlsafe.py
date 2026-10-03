"""XML parsing for files that come from outside: refuse entity declarations first (W5-07).

``xml.etree`` does not fetch external DTDs or entities, but it expands internal entities, so a
crafted file could expand to gigabytes (the "billion laughs" attack). A document that declares an
entity is refused rather than parsed. The check also looks through the NUL bytes of a UTF-16 or
UTF-32 document, so re-encoding a document does not get it past the check.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_ENTITY = re.compile(rb"<!ENTITY", re.IGNORECASE)
MAX_XML_DEPTH = 128


class UnsafeXml(ValueError):
    """The XML declares entities (or nests too deeply); it is not parsed."""


def parse_xml(data: bytes | str) -> ET.Element:
    """The root element of ``data``; :class:`UnsafeXml` when it declares entities."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if _ENTITY.search(raw) or _ENTITY.search(raw.replace(b"\x00", b"")):
        raise UnsafeXml("the XML declares entities; refusing to parse it")
    # Entity declarations were refused above, and the standard parser fetches nothing.
    return ET.fromstring(raw)  # nosec B314


def xml_depth(root: ET.Element) -> int:
    """The depth of the element tree (a lone root is 1), counted without recursion."""
    deepest, stack = 0, [(root, 1)]
    while stack:
        el, depth = stack.pop()
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in el)
    return deepest

"""XML parsing for files that come from outside: refuse entity declarations first.

``xml.etree`` does not fetch external DTDs, but it expands internal entities, so a crafted file
could expand to gigabytes (the "billion laughs" attack). Real CDC and ClaML files declare no
entities (ClaML files point at an external DTD with ``<!DOCTYPE``, which is harmless here), so a
file that declares one is refused rather than parsed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_ENTITY = re.compile(rb"<!ENTITY", re.IGNORECASE)


class UnsafeXml(ValueError):
    """The XML declares entities; it is not parsed."""


def parse_xml(data: bytes | str) -> ET.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if _ENTITY.search(raw):
        raise UnsafeXml("the XML declares entities; refusing to parse it")
    # Entity declarations were refused above, so the standard parser is safe here.
    return ET.fromstring(raw)  # nosec B314

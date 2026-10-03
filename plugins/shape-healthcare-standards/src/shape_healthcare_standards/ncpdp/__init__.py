"""NCPDP-style pharmacy claim wire output driven by a layout the user supplies.

The NCPDP Telecommunication Standard and its data dictionary are licensed by NCPDP, so this
package contains **no** field table, code list or segment definition from them. It is a
bring-your-own-specification layer: a user who holds the licence writes a JSON layout
(see :mod:`.layout`); the package then maps the pharmacy tables of the input contract onto that
layout (:mod:`.mapping`) and writes or parses the generic delimited wire structure
(:mod:`.wire`). Nothing here is conformance-tested against the standard.
"""

from __future__ import annotations

from .layout import FieldFormat, Layout, LayoutError
from .mapping import MappingError, map_claims
from .sink import NcpdpSink
from .wire import WireError, parse_transaction, write_transaction

__all__ = [
    "FieldFormat",
    "Layout",
    "LayoutError",
    "MappingError",
    "NcpdpSink",
    "WireError",
    "map_claims",
    "parse_transaction",
    "write_transaction",
]

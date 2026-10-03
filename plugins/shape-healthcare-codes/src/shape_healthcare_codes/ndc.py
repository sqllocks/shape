"""National Drug Code: 11-digit 5-4-2 normalization of the FDA's 10-digit labeler formats.

The FDA lists an NDC as three dash-separated segments in one of three 10-digit layouts:
``4-4-2``, ``5-3-2`` and ``5-4-1`` (labeler-product-package). The 11-digit billing form
(HIPAA, NCPDP) is always ``5-4-2``: a leading zero pads the segment that is short. Without
the dashes a 10-digit NDC is ambiguous (three layouts), so :func:`normalize_ndc` refuses it
unless the caller names the layout.
"""

from __future__ import annotations

import re

_DASHED = re.compile(r"^([0-9]{4,5})-([0-9]{3,4})-([0-9]{1,2})$")
_LAYOUTS = {"4-4-2": (4, 4, 2), "5-3-2": (5, 3, 2), "5-4-1": (5, 4, 1)}


class AmbiguousNdc(ValueError):
    """A 10-digit NDC with no dashes and no layout: it could be 4-4-2, 5-3-2 or 5-4-1."""


def normalize_ndc(value: str, layout: str | None = None) -> str | None:
    """The 11-digit 5-4-2 form of ``value`` (digits only), or ``None`` if it is not an NDC.

    Accepts a dashed 10-digit NDC (``0002-3227-30``), a dashed 11-digit one
    (``00002-3227-30``), 11 bare digits, and 10 bare digits with ``layout`` in
    ``{"4-4-2", "5-3-2", "5-4-1"}``. Raises :class:`AmbiguousNdc` for 10 bare digits without
    a layout.
    """
    v = value.strip()
    m = _DASHED.match(v)
    if m:
        a, b, c = m.groups()
        if (len(a), len(b), len(c)) not in set(_LAYOUTS.values()) | {(5, 4, 2)}:
            return None
        return a.zfill(5) + b.zfill(4) + c.zfill(2)
    if re.fullmatch(r"[0-9]{11}", v):
        return v
    if re.fullmatch(r"[0-9]{10}", v):
        if layout is None:
            raise AmbiguousNdc(f"{value!r}: 10 digits without dashes; give layout= or use dashes")
        if layout not in _LAYOUTS:
            raise ValueError(f"layout must be one of {sorted(_LAYOUTS)}")
        la, lb, lc = _LAYOUTS[layout]
        return v[:la].zfill(5) + v[la : la + lb].zfill(4) + v[la + lb :].zfill(2)
    return None


def format_ndc11(ndc11: str) -> str:
    """``00002322730`` -> ``00002-3227-30``."""
    if not re.fullmatch(r"[0-9]{11}", ndc11):
        raise ValueError("expected 11 digits")
    return f"{ndc11[:5]}-{ndc11[5:9]}-{ndc11[9:]}"


def product_ndc9(ndc11: str) -> str:
    """The labeler+product part (9 digits) of an 11-digit NDC."""
    return ndc11[:9]

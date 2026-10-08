"""Render a fidelity report through the ``shape.reports`` plugin group."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shape.plugins.host import default_host


def render_report(report: Mapping[str, Any], fmt: str) -> bytes:
    """Render ``report`` in the ``shape.reports`` format named ``fmt`` (``json``, ``md``, ``html``,
    or any installed plugin's)."""
    return bytes(default_host().get("shape.reports", fmt).render(report))

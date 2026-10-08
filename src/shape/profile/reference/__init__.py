"""Pure-Python reference profiler (numpy + pyarrow only).

This is the reference twin of the future compiled kernel: it produces a table or
dataset profile and is verified field by field against the internal baseline by the parity
harness under ``benchmarks/``.
"""

from __future__ import annotations

from .profile import Profile, load, profile, save
from .sources import SourceError

__all__ = ["Profile", "SourceError", "load", "profile", "save"]

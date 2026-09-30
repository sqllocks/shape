"""Pure-Python reference profiler (numpy + pyarrow only).

This is the reference twin of the future compiled kernel: it produces a Spindle-shaped
table or dataset profile and is verified field by field against the pinned Spindle
baseline (``benchmarks/vs_spindle/profile_1to1/verify.py --impl shape``).
"""

from __future__ import annotations

from .profile import Profile, load, profile, save
from .sources import SourceError

__all__ = ["Profile", "SourceError", "load", "profile", "save"]

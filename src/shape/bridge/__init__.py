"""``shape bridge``: a versioned JSON request/response protocol over standard input and output
(P6-11). See ``docs/BRIDGE.md``.

Nothing heavy loads at import (T-18): the handlers import numpy, pyarrow and the engine when a
command runs."""

from __future__ import annotations

from shape.bridge.protocol import API_VERSION, ERROR_CODES

__all__ = ["API_VERSION", "ERROR_CODES"]

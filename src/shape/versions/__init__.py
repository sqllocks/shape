"""History tools on top of the registry (W3-03): ``bisect``, ``bisect_layers`` and ``timelapse``.

``bisect`` finds the first committed version of a name that tests bad; ``bisect_layers`` finds the
layer of a pipeline where a change first appears; ``timelapse`` follows one column across the
versions. Each returns a result object whose ``to_dict()`` is the JSON of the matching command.
See ``docs/HISTORY.md``.
"""

from __future__ import annotations

from shape.versions.bisect import BisectResult, bisect
from shape.versions.layers import LayerBisectResult, bisect_layers
from shape.versions.timelapse import TimelapseResult, load_timelapse, timelapse
from shape.versions.versions import HistoryError

__all__ = [
    "BisectResult",
    "HistoryError",
    "LayerBisectResult",
    "TimelapseResult",
    "bisect",
    "bisect_layers",
    "load_timelapse",
    "timelapse",
]

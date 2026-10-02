"""Generation at scale (P6-13): the scale router, its sinks and its jobs.

``shape generate --scale-mode local_single|local_mp|fabric_spark`` runs through here. Nothing heavy
loads at import time (T-18): each part is imported when it is used.

* :mod:`shape.scale.router`: ``ScaleRouter``, the two local modes (one thread, or every core).
* :mod:`shape.scale.sinks`: where the chunks go (memory, Parquet part files, Lakehouse, Warehouse,
  SQL Database, KQL), and :mod:`shape.scale.sink_registry`, the fan-out to several at once.
* :mod:`shape.scale.chunk_worker` and :mod:`shape.scale.chunked`: per-chunk files, optionally made
  by worker processes.
* :mod:`shape.scale.jobs`: the durable job store, the Fabric job tracker and the stream manager.
* :mod:`shape.scale.spark`: the ``fabric_spark`` router and its worker notebook.
"""

from __future__ import annotations

__all__: list[str] = []

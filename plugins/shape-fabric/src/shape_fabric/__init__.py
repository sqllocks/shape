"""Shape plugin for Fabric, Synapse and Azure Data Factory pipeline integration.

* ``shape.emitters``: ``eventstream`` sends events to a Fabric Eventstream custom endpoint
  (``eventstream://<name>``) and ``eventhouse`` to an Eventhouse (KQL database) by streaming
  ingestion (``eventhouse://<query-uri host>/<database>``), for ``shape emit``.

Other entry points are added by the work packages that implement them.
"""

from .eventhouse import EventhouseEmitter
from .eventstream import EventstreamEmitter

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "EventhouseEmitter", "EventstreamEmitter"]

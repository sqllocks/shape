"""Shape plugin: Azure Event Hubs.

* ``shape.stream_sources``: ``eventhubs`` reads an event hub
  (``eventhubs://<namespace>/<hub>``) as Arrow micro-batches with ``{partition: next sequence
  number}`` offsets, for ``shape stream-profile``.

``azure-eventhub`` loads only when a read starts. The Event Hubs emitter arrives with P5-02.
"""

from .source import EventHubsStreamSource

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "EventHubsStreamSource"]

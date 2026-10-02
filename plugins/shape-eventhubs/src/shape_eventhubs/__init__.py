"""Shape plugin: Azure Event Hubs.

* ``shape.stream_sources``: ``eventhubs`` reads an event hub
  (``eventhubs://<namespace>/<hub>``) as Arrow micro-batches with ``{partition: next sequence
  number}`` offsets, for ``shape stream-profile``.

* ``shape.emitters``: ``eventhubs`` sends events to an event hub, each carrying the idempotency
  key ``<table>/<seq>`` as the message property ``shape_key``, for ``shape emit``.

``azure-eventhub`` loads only when a read or a send starts.
"""

from .emitter import EventHubsEmitter
from .source import EventHubsStreamSource

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "EventHubsEmitter", "EventHubsStreamSource"]

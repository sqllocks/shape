"""Shape plugin: Kafka.

* ``shape.stream_sources``: ``kafka`` reads a topic (``kafka://host:9092/topic``) as Arrow
  micro-batches with ``{partition: next offset}`` offsets, for ``shape stream-profile``.

* ``shape.emitters``: ``kafka`` sends events to a topic (``kafka://host:9092/topic``), one
  message per event keyed by the idempotency key ``<table>/<seq>``, for ``shape emit``.

``confluent-kafka`` loads only when a read or a send starts.
"""

from .emitter import KafkaEmitter
from .source import KafkaStreamSource

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "KafkaEmitter", "KafkaStreamSource"]

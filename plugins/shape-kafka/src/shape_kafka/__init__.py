"""Shape plugin: Kafka.

* ``shape.stream_sources``: ``kafka`` reads a topic (``kafka://host:9092/topic``) as Arrow
  micro-batches with ``{partition: next offset}`` offsets, for ``shape stream-profile``.

``confluent-kafka`` loads only when a read starts. The Kafka emitter arrives with P5-02.
"""

from .source import KafkaStreamSource

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "KafkaStreamSource"]

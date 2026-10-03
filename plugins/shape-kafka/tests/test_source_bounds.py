"""A bounded read ends where the topic ended when the read began (#351)."""

from __future__ import annotations

from shape_kafka.testing import FakeBroker, json_messages


def test_a_bounded_read_ignores_messages_produced_after_it_began() -> None:
    broker = FakeBroker({"t": {0: json_messages([{"a": i} for i in range(10)])}}, chunk=4)
    seen: list[int] = []
    last = None
    for offset, batch in broker.source().read("kafka://b:9092/t", batch_size=4):
        if not seen:
            # A producer writes while the read is under way.
            broker.topics["t"][0].extend(json_messages([{"a": 100 + i} for i in range(6)]))
        seen.extend(batch.column("a").to_pylist())
        last = offset.value
    assert seen == list(range(10))
    assert last == {"0": 10}

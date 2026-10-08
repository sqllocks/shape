"""Event Hubs source: the end of a bounded read, max_messages per read, bodies that are not
UTF-8 (#353, #354, #355)."""

from __future__ import annotations

from typing import Any

from shape_eventhubs.source import EventHubsStreamSource
from shape_eventhubs.testing import FakeClient, FakeHub, json_events

URI = "eventhubs://ns.servicebus.example/h"


def test_a_bounded_read_ignores_events_enqueued_after_it_began() -> None:
    hub = FakeHub({"0": json_events([{"a": i} for i in range(16)])}, chunk=4)

    class Late(FakeClient):
        # The partition held 10 events when the read began; 6 more arrived since.
        def get_partition_properties(self, partition_id: str) -> dict[str, Any]:
            return {
                "is_empty": False,
                "beginning_sequence_number": 0,
                "last_enqueued_sequence_number": 9,
            }

    src = EventHubsStreamSource(lambda target, options: Late(hub))
    out = list(src.read(URI, batch_size=4))
    assert [b.column("a").to_pylist() for _, b in out] == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    assert out[-1][0].value == {"0": 10}


def test_max_messages_counts_each_read_on_its_own() -> None:
    rows = [{"a": i} for i in range(10)]
    src = FakeHub({"0": json_events(rows), "1": json_events(rows)}).source()
    first = sum(b.num_rows for _, b in src.read(URI, max_messages=5))
    second = sum(b.num_rows for _, b in src.read(URI, max_messages=5))
    assert (first, second) == (5, 5)


class _NotUtf8:
    sequence_number = 0
    enqueued_time = None
    body = [b"\xff\xfe{"]

    def body_as_str(self) -> str:
        # What azure-eventhub's EventData.body_as_str raises for such a body.
        raise TypeError("Message data is not compatible with string type")


def test_a_body_that_is_not_utf8_is_counted_and_skipped() -> None:
    hub = FakeHub({"0": json_events([{"a": 1}, {"a": 2}])})
    hub.partitions["0"][0] = _NotUtf8()  # type: ignore[call-overload]
    src = EventHubsStreamSource(lambda target, options: FakeClient(hub))
    out = [b.column("a").to_pylist() for _, b in src.read(URI)]
    assert out == [[2]]
    assert src.stats.undecodable == 1

"""The Event Hubs emitter end to end against the Event Hubs emulator (the nightly job's
containers).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
    pytest -m emulator plugins/shape-eventhubs/tests/test_emitter_emulator.py

Hub ``eh1``, as in ``test_emulator.py``; ``SHAPE_TEST_EVENTHUBS`` overrides the connection string.
Nothing here is skipped when the emulator is missing: the nightly job must fail if it cannot
reach it. The emulator keeps its events between runs, so each harness reads only what was sent
after it was made.
"""

import json
import os
import time

import pytest
from shape_eventhubs import EventHubsEmitter
from shape_eventhubs.testing import HubHarness, hub_ends, read_raw

from shape.streaming.emit import EmitConfig, EmitRunner, EmitterSink, contract
from shape.streaming.emit.formats import FIELD_SEQ

pytestmark = pytest.mark.emulator

CONNECTION = os.environ.get(
    "SHAPE_TEST_EVENTHUBS",
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;",
)
HUB = "eh1"
GROUP = "cg1"
URI = f"eventhubs://localhost/{HUB}"


@pytest.fixture(scope="module", autouse=True)
def emulator_is_up():
    deadline = time.monotonic() + 120
    while True:
        try:
            assert len(hub_ends(CONNECTION, HUB, GROUP)) == 4
            return
        except Exception:  # the emulator is still starting
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def new_harness():
    return HubHarness(EventHubsEmitter, URI, CONNECTION, HUB, GROUP)


def test_idempotency_key():
    contract.check_idempotency_key(new_harness)


def test_at_least_once():
    contract.check_at_least_once(new_harness)


def test_checkpoint(tmp_path):
    contract.check_checkpoint(new_harness, directory=tmp_path)


def test_properties_content_type_and_partitioning_on_a_real_hub():
    h = new_harness()
    plan = contract.default_plan()
    batch = next(iter(plan.blocks(0))).batch.slice(0, 200)
    e = h.make()
    assert e.emit(URI, [batch]) == 200
    e.close()
    end = hub_ends(CONNECTION, HUB, GROUP)
    raw = read_raw(CONNECTION, HUB, GROUP, h.start, end)
    assert len(raw) == 200
    assert [p["shape_key"] for p, _ in raw] == [f"order_line/{i}" for i in range(200)]
    assert all(p["shape_table"] == "order_line" for p, _ in raw)
    assert [json.loads(b)[FIELD_SEQ] for _, b in raw] == list(range(200))
    # one table -> one partition key -> one partition
    moved = [p for p in end if end[p] > h.start[p]]
    assert len(moved) == 1


def test_a_run_with_a_tiny_queue_delivers_everything_in_order():
    h = new_harness()
    sink = EmitterSink(h.make(), URI)
    report = EmitRunner(
        contract.default_plan(),
        sink,
        EmitConfig(max_events=1500, batch_events=100, queue_batches=1),
    ).run()
    assert report.events == 1500
    assert [json.loads(b)[FIELD_SEQ] for _, b in h.delivered()] == list(range(1500))


def test_a_wrong_hub_is_an_error_not_a_hang():
    started = time.monotonic()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 3)
    with pytest.raises(Exception, match="(?i)hub|entity|not found|refused|send failed"):
        EventHubsEmitter().emit(
            "eventhubs://localhost/no-such-hub", [batch], connection_string=CONNECTION
        )
    assert time.monotonic() - started < 120


def test_keyed_metadata_round_trip_emulator():
    h = new_harness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 3)
    emitter = h.make()
    emitter.emit(
        URI, [batch], key="_shape_table", partition_key="_shape_table", headers=["rehearsal=yes"]
    )
    emitter.close()
    raw = read_raw(CONNECTION, HUB, GROUP, h.start, hub_ends(CONNECTION, HUB, GROUP))
    assert [p["shape_key"] for p, _ in raw] == ["order_line"] * 3
    assert [p["shape-key"] for p, _ in raw] == [f"order_line/{i}" for i in range(3)]
    assert all(p["rehearsal"] == "yes" for p, _ in raw)

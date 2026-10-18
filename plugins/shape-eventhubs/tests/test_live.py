"""The Event Hubs emitter against a real event hub (nightly, only where the secrets exist).

    EVENTHUBS_CONNECTION_STRING='Endpoint=sb://...' EVENTHUBS_HUB=<hub> \\
    [EVENTHUBS_GROUP=<consumer group>] pytest -m live plugins/shape-eventhubs/tests

A missing variable skips the live test with the missing setting named.
"""

import os

import pytest
from shape_eventhubs import EventHubsEmitter
from shape_eventhubs.testing import HubHarness

from shape.streaming.emit import contract

pytestmark = pytest.mark.live


def need(name, default=None):
    value = os.environ.get(name, default)
    if not value:
        pytest.skip(f"missing live setting: {name}")
    return value


def new_harness():
    hub = need("EVENTHUBS_HUB")
    return HubHarness(
        EventHubsEmitter,
        f"eventhubs://live/{hub}",
        need("EVENTHUBS_CONNECTION_STRING"),
        hub,
        need("EVENTHUBS_GROUP", "$Default"),
    )


def test_idempotency_key():
    contract.check_idempotency_key(new_harness)


def test_at_least_once():
    contract.check_at_least_once(new_harness)


def test_checkpoint(tmp_path):
    contract.check_checkpoint(new_harness, directory=tmp_path)


def test_keyed_metadata_round_trip_live():
    h = new_harness()
    batch = next(iter(contract.default_plan().blocks(0))).batch.slice(0, 3)
    emitter = h.make()
    emitter.emit(
        h.uri, [batch], key="_shape_table", partition_key="_shape_table", headers=["rehearsal=yes"]
    )
    emitter.close()
    assert [key for key, _ in h.delivered()] == ["order_line"] * 3

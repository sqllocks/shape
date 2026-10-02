"""The Event Hubs emitter against a real event hub (nightly, only where the secrets exist).

    EVENTHUBS_CONNECTION_STRING='Endpoint=sb://...' EVENTHUBS_HUB=<hub> \\
    [EVENTHUBS_GROUP=<consumer group>] pytest -m live plugins/shape-eventhubs/tests

A missing variable fails the test with the variable's name (nothing is silently skipped).
"""

import os

import pytest
from shape_eventhubs import EventHubsEmitter
from shape_eventhubs.testing import HubHarness

from shape.streaming.emit import contract

pytestmark = pytest.mark.live


def need(name, default=None):
    value = os.environ.get(name, default)
    assert value, f"{name} is not set (live tests need it)"
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

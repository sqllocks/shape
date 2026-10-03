"""The Fabric emitters end to end (the nightly job's containers).

    docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
    docker compose -f ci/emulators/docker-compose.yml up -d kusto
    pytest -m emulator plugins/shape-fabric/tests/test_emulator.py

* ``eventstream://`` speaks the Event Hubs protocol, so the Event Hubs emulator (hub ``eh1``)
  stands in for an Eventstream custom endpoint; ``SHAPE_TEST_EVENTHUBS`` overrides its connection
  string.
* ``eventhouse://`` runs against the Kusto emulator (``kustainer``, ``localhost:8080``, database
  ``NetDefaultDB``, no sign-in); ``SHAPE_TEST_KUSTO`` overrides ``host:port``. Rows are counted
  through the query API; the emulator's types come back as Kusto formats them, so the checks are on
  counts, keys and sums, not on the text of the rows.

Nothing here is skipped when an emulator is missing: the nightly job must fail if it cannot reach
it.
"""

import json
import os
import time
import urllib.request
import uuid

import pytest
from shape_eventhubs.testing import HubHarness, hub_ends
from shape_fabric import EventhouseEmitter, EventstreamEmitter
from shape_fabric.eventhouse import dedupe_query

from shape.streaming.emit import EmitConfig, EmitRunner, EmitterSink, contract

pytestmark = pytest.mark.emulator

CONNECTION = os.environ.get(
    "SHAPE_TEST_EVENTHUBS",
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;",
)
HUB = "eh1"
GROUP = "cg1"
KUSTO = os.environ.get("SHAPE_TEST_KUSTO", "localhost:8080")
DATABASE = "NetDefaultDB"


# ------------------------------------------------------------------------ Eventstream


@pytest.fixture(scope="module")
def hub_is_up():
    deadline = time.monotonic() + 120
    while True:
        try:
            assert len(hub_ends(CONNECTION, HUB, GROUP)) == 4
            return
        except Exception:  # the emulator is still starting
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def new_stream_harness():
    return HubHarness(EventstreamEmitter, f"eventstream://emulator/{HUB}", CONNECTION, HUB, GROUP)


def test_eventstream_idempotency_key(hub_is_up):
    contract.check_idempotency_key(new_stream_harness)


def test_eventstream_at_least_once(hub_is_up):
    contract.check_at_least_once(new_stream_harness)


def test_eventstream_checkpoint(hub_is_up, tmp_path):
    contract.check_checkpoint(new_stream_harness, directory=tmp_path)


# ------------------------------------------------------------------------ Eventhouse


def kql(csl, *, mgmt=False):
    path = "mgmt" if mgmt else "query"
    req = urllib.request.Request(
        f"http://{KUSTO}/v1/rest/{path}",
        data=json.dumps({"db": DATABASE, "csl": csl}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["Tables"][0]["Rows"]


@pytest.fixture(scope="module")
def kusto_is_up():
    deadline = time.monotonic() + 180
    while True:
        try:
            assert kql("print 1") == [[1]]
            return
        except Exception:  # the emulator is still starting
            if time.monotonic() > deadline:
                raise
            time.sleep(3)


def run(table, tmp_path, **cfg):
    uri = f"eventhouse://{KUSTO}/{DATABASE}/{table}?tls=false"
    sink = EmitterSink(EventhouseEmitter(), uri)
    cfg.setdefault("max_events", contract.EVENTS)
    cfg.setdefault("batch_events", contract.BATCH)
    return EmitRunner(contract.default_plan(), sink, EmitConfig(**cfg)).run()


def reference_sum():
    return sum(e["order_line_id"] for e in contract.reference(contract.default_plan()))


def test_a_run_lands_every_event_once_with_typed_columns(kusto_is_up, tmp_path):
    table = f"e2e_{uuid.uuid4().hex[:8]}"
    report = run(table, tmp_path)
    assert report.events == contract.EVENTS
    ((rows, total),) = kql(f"['{table}'] | summarize count(), sum(order_line_id)")
    # dcount() is an approximate distinct count in KQL (HyperLogLog); `distinct` is exact
    ((keys,),) = kql(f"['{table}'] | distinct _shape_seq | count")
    assert (rows, keys, total) == (contract.EVENTS, contract.EVENTS, reference_sum())
    cols = {r[0]: r[1] for r in kql(f"['{table}'] | getschema | project ColumnName, ColumnType")}
    assert cols["_shape_table"] == "string" and cols["_shape_seq"] == "long"
    assert cols["order_line_id"] == "long"


def test_a_crash_leaves_repeats_and_the_key_removes_them(kusto_is_up, tmp_path):
    table = f"e2e_{uuid.uuid4().hex[:8]}"
    ck = tmp_path / "ck.json"
    run(table, tmp_path, checkpoint_path=str(ck), checkpoint_every=contract.BATCH, max_events=1800)
    doc = json.loads(ck.read_text())
    doc.update(offset=700, complete=False)  # a stale checkpoint, as after kill -9
    ck.write_text(json.dumps(doc))
    run(table, tmp_path, checkpoint_path=str(ck), checkpoint_every=contract.BATCH)
    ((rows,),) = kql(f"['{table}'] | count")
    assert rows == contract.EVENTS + (1800 - 700)  # the repeats
    ((deduped, total),) = kql(f"{dedupe_query(table)} | summarize count(), sum(order_line_id)")
    assert (deduped, total) == (contract.EVENTS, reference_sum())


def test_each_shape_table_gets_its_own_kql_table(kusto_is_up, tmp_path):
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine
    from shape.streaming.emit import EventPlan

    plan = EventPlan(Engine(load_target("retail"), scale="small", seed=11), tables=["customer"])
    uri = f"eventhouse://{KUSTO}/{DATABASE}?tls=false"
    EmitRunner(plan, EmitterSink(EventhouseEmitter(), uri), EmitConfig(max_events=300)).run()
    ((rows,),) = kql("['customer'] | count")
    assert rows >= 300

"""The Fabric emitters against real Fabric items (nightly, only where the secrets exist).

The workflow job runs only when the secrets are set; run by hand with

    FABRIC_EVENTSTREAM_CONNECTION_STRING='Endpoint=sb://...;EntityPath=es_...' \\
    FABRIC_EVENTHOUSE_URI='eventhouse://<query-uri host>/<database>' \\
    FABRIC_EVENTHOUSE_TOKEN='<bearer token for the query URI>' \\
    pytest -m live plugins/shape-fabric/tests/test_live.py

A missing variable fails the test with the variable's name (nothing is silently skipped).
"""

import json
import os
import urllib.request
import uuid
from urllib.parse import urlsplit

import pytest
from shape_fabric import EventhouseEmitter, EventstreamEmitter
from shape_fabric.eventhouse import dedupe_query

from shape.streaming.emit import EmitConfig, EmitRunner, EmitterSink, contract

pytestmark = pytest.mark.live


def need(name):
    value = os.environ.get(name)
    assert value, f"{name} is not set (live tests need it)"
    return value


def test_eventstream_accepts_a_run_and_acknowledges_every_event():
    conn = need("FABRIC_EVENTSTREAM_CONNECTION_STRING")
    sink = EmitterSink(EventstreamEmitter(), "eventstream://live", connection_string=conn)
    report = EmitRunner(
        contract.default_plan(), sink, EmitConfig(max_events=1000, batch_events=250)
    ).run()
    assert report.events == 1000 and report.complete


def kql(uri, token, csl):
    target = urlsplit(uri)
    database = target.path.strip("/").split("/")[0]
    req = urllib.request.Request(
        f"https://{target.netloc}/v1/rest/query",
        data=json.dumps({"db": database, "csl": csl}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["Tables"][0]["Rows"]


def test_eventhouse_lands_a_run_and_dedupe_on_the_key_matches_the_stream():
    uri = need("FABRIC_EVENTHOUSE_URI")
    token = need("FABRIC_EVENTHOUSE_TOKEN")
    table = f"shape_live_{uuid.uuid4().hex[:8]}"
    full = f"{uri.rstrip('/')}/{table}"
    sink = EmitterSink(EventhouseEmitter(), full, token=token)
    report = EmitRunner(
        contract.default_plan(), sink, EmitConfig(max_events=1000, batch_events=250)
    ).run()
    assert report.events == 1000
    # streaming ingestion is eventually visible: poll the count
    import time

    deadline = time.monotonic() + 300
    while True:
        ((rows,),) = kql(uri, token, f"['{table}'] | count")
        if rows >= 1000 or time.monotonic() > deadline:
            break
        time.sleep(10)
    ((deduped,),) = kql(uri, token, f"{dedupe_query(table)} | count")
    assert deduped == 1000

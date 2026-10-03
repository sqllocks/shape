"""#542: a Fabric status the bridge has no name for keeps the job active (asked again, and
cancelled in Fabric), with Fabric's own name in the job's progress."""

from __future__ import annotations

import pytest
from fakes import LH, WS, FakeFabric

STATUSES = {"running", "submitted", "succeeded", "failed", "cancelled", "interrupted"}


@pytest.fixture
def fabric(monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr("shape.scale.http.urllib_transport", fake)
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor-1")
    return fake


def _submit(api, schema_file) -> str:
    return api.ok(
        "scale_generate",
        domain=str(schema_file),
        scale_mode="fabric_spark",
        sinks=["lakehouse"],
        sink_config={"workspace_id": WS, "lakehouse_id": LH, "token": "tok-1"},
    )["job_id"]


def test_an_unknown_fabric_status_keeps_the_job_active(api, fabric, schema_file):
    job_id = _submit(api, schema_file)
    fabric.job_status = "Paused"  # a name neither Shape nor the bridge knows
    state = api.ok("job_status", job_id=job_id, token="tok-1")
    assert state["status"] in STATUSES and state["status"] == "submitted", state
    assert state["progress"]["fabric_status"] == "paused"
    assert api.ok("job_list")["jobs"][0]["status"] == "submitted"
    result = api.ok("job_cancel", job_id=job_id, token="tok-1")
    assert result["cancelled"] is True and result["status"] == "cancelled"
    assert any(c["method"] == "POST" and c["url"].endswith("/cancel") for c in fabric.calls)

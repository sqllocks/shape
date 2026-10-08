"""#545: a stream with a very long interval waits (and stops when asked) instead of failing with
an overflow."""

from __future__ import annotations

import time


def test_a_huge_interval_waits_until_the_stream_is_stopped(api):
    started = api.ok(
        "stream",
        domain="retail",
        scale="small",
        chunk_size=10,
        max_chunks=2,
        interval_seconds=1e300,
    )
    stream_id = started["stream_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        state = api.ok("stream_status", stream_id=stream_id)
        if state["chunks_written"] >= 1 or state["status"] != "running":
            break
        time.sleep(0.05)
    time.sleep(0.3)  # the stream is now in its wait between chunks
    state = api.ok("stream_status", stream_id=stream_id)
    assert state["status"] == "running" and state["error"] is None, state
    assert api.ok("stream_stop", stream_id=stream_id)["status"] == "stopped"
    final = api.ok("job_status", job_id=stream_id)
    assert final["status"] == "cancelled" and final["result"]["chunks_written"] == 1, final

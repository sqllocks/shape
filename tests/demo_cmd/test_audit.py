"""AUD-scenario: regression tests for the ``shape demo`` defects the audit lane found."""

from __future__ import annotations

import json

import pytest
from demo_helpers import write_schema

from shape.demo.api import demo_cleanup, demo_init, demo_run


def sessions(home) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((home / "sessions").glob("demo-*.json"))]


# ---- #511: an interrupted run saves its session ------------------------------------------------


def test_511_an_interrupted_seeding_run_is_recorded_and_can_be_cleaned_up(
    home, tmp_path, monkeypatch
):
    from shape.scale import router

    def interrupted(self):  # the first table is written, then Ctrl+C
        sink = self.sinks[0]
        sink.open(self.engine.schema)
        table = self.engine.order[0]
        for batch in self.engine.iter_chunks(table):
            sink.write_batch(table, batch)
        sink.finish_table(table)
        raise KeyboardInterrupt

    landing = tmp_path / "landing"
    demo_init("local", local_path=str(landing))
    real = router.ScaleRouter.run
    monkeypatch.setattr(router.ScaleRouter, "run", interrupted)
    params = {
        "scenario": "retail",
        "mode": "seeding",
        "connection": "local",
        "domain": str(write_schema(tmp_path / "shop.json")),
        "rows": 1000,
    }
    with pytest.raises(KeyboardInterrupt):
        demo_run(params)
    monkeypatch.setattr(router.ScaleRouter, "run", real)
    (record,) = sessions(home)
    assert record["success"] is False and "interrupted" in record["error"]
    assert record["artifacts"], "what the run wrote is in the record"
    assert any(landing.iterdir())
    outcome = demo_cleanup(record["session_id"])
    assert outcome["ok"] and not any(landing.iterdir())

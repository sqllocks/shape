"""PRIV-092: regression tests for the scenario-pack fixes (one section per old issue)."""

from __future__ import annotations

import json
from pathlib import Path

from shape.scenario import PackLoader, PackRunner
from tests.scenario.test_audit import FILE_DROP, one_preset

# ---- #714: two runs that reach the manifest in the same second never share one --------------


def test_714_racing_runs_each_get_their_own_manifest(tmp_path, retail, monkeypatch):
    from datetime import UTC, datetime

    import shape.scenario.manifest as manifest_module

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    monkeypatch.setattr(manifest_module, "datetime", Frozen)
    # Both runs pass an existence check before either writes: the check and the write race.
    original = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: False if self.name.endswith("_manifest.json") else original(self),
    )
    pack = PackLoader().parse(FILE_DROP)
    schema = one_preset(retail)
    out = tmp_path / "out"
    first = PackRunner().run(pack, schema, "small", 1, out)
    first_text = Path(first.files_written[-1]).read_text(encoding="utf-8")
    second = PackRunner().run(pack, schema, "small", 1, out)

    base = "20260102_030405_retail_small_s1"
    assert [first.manifest.run_id, second.manifest.run_id] == [base, f"{base}_x2"]
    manifests = sorted(p.name for p in out.glob("*_manifest.json"))
    assert manifests == [f"{base}_manifest.json", f"{base}_x2_manifest.json"]
    # The first run's manifest is never replaced, and each manifest names its own run.
    assert Path(first.files_written[-1]).read_text(encoding="utf-8") == first_text
    for result in (first, second):
        doc = json.loads(Path(result.files_written[-1]).read_text(encoding="utf-8"))
        assert doc["run_id"] == result.manifest.run_id

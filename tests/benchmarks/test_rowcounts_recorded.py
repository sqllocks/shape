"""All nine composite plans are frozen and input-bound, never planned by the verifier."""

import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "benchmarks/vs_refengine/domain_1to1/rowcounts.py"
    spec = importlib.util.spec_from_file_location("frz_rowcounts", path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("damage", ["equal", "missing", "schema", "count"])
def test_nine_composite_plans_are_authenticated(monkeypatch, damage):
    m = module()
    ids = m.composite_ids()
    assert len(ids) == 9
    entries = {
        key: {"schema_sha256": m.sha256(m.composite_path(key)), "planned": {"small": {"t": 7}}}
        for key in ids
    }
    if damage == "missing":
        del entries[ids[0]]
    if damage == "schema":
        entries[ids[0]]["schema_sha256"] = "0" * 64
    if damage == "count":
        entries[ids[0]]["planned"]["small"]["t"] = -1
    monkeypatch.setattr(m, "load", lambda *a, **kw: {"composites": entries})
    if damage == "equal":
        assert m.recorded_composite_plans() == {
            key: entry["planned"] for key, entry in entries.items()
        }
    else:
        with pytest.raises(m.RecordingError, match=ids[0]):
            m.recorded_composite_plans()


def test_missing_composite_recording_returns_2_without_planning(monkeypatch, capsys):
    m = module()
    monkeypatch.setattr(
        m, "load", lambda *a, **kw: (_ for _ in ()).throw(m.RecordingError("no recording"))
    )
    assert m.main(["baseline", "--generate-scales", ""]) == 2
    assert "composite_" in capsys.readouterr().err

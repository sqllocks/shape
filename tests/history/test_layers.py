"""W3-03 deliverable 3: ``shape.history.bisect_layers`` (which layer introduced a change)."""

from __future__ import annotations

from pathlib import Path

import pytest
from history_helpers import layered, total_step

from shape.history import HistoryError, bisect_layers

STEP = [total_step(2)]  # x1.4 on `total` from the third day (2026-03-03)
GOOD, BAD = "2026-03-01", "2026-03-03"


def run(project: Path, layers: str = "raw,clean,pub", **kw):
    return bisect_layers(
        layers.split(","), good_date=GOOD, bad_date=BAD, project=project, **kw
    ).to_dict()


def test_change_planted_in_the_second_layer_persists_downstream(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {"events": STEP}, "pub": {"events": STEP}})
    d = run(project)
    assert d["found"] is True
    assert d["first_layer"] == "clean"
    assert d["persists"] == ["pub"]
    assert d["disappears"] == []
    assert [layer["status"] for layer in d["layers"]] == ["unchanged", "origin", "persists"]
    clean = d["layers"][1]
    assert clean["columns"] == ["total"]
    assert {c["column"] for c in clean["changes"]} == {"total"}
    assert clean["good"]["business_date"] == GOOD and clean["bad"]["business_date"] == BAD


def test_change_planted_in_the_first_layer_and_removed_by_the_second(tmp_path: Path):
    project = layered(tmp_path, {"raw": {"events": STEP}, "clean": {}, "pub": {}})
    d = run(project)
    assert d["first_layer"] == "raw"
    assert d["persists"] == []
    assert d["disappears"] == ["clean", "pub"]
    assert d["layers"][1]["status"] == "disappears"
    assert d["layers"][1]["changed"] is False


def test_no_change_finds_no_layer(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {}, "pub": {}})
    d = run(project)
    assert d["found"] is False
    assert d["first_layer"] is None
    assert d["persists"] == [] and d["disappears"] == []
    assert {layer["status"] for layer in d["layers"]} == {"unchanged"}


def test_result_shape(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {"events": STEP}})
    d = run(project, "raw,clean")
    assert d["format"] == "shape-bisect-layers"
    assert d["version"] == 1
    assert d["good_date"] == GOOD and d["bad_date"] == BAD
    assert d["column"] is None
    assert d["warnings"] == []
    assert [layer["source"] for layer in d["layers"]] == ["raw", "clean"]
    assert d["layers"][0]["name"] == "layer_raw"


def test_the_column_restricts_what_counts_as_the_change(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {"events": STEP}})
    assert run(project, "raw,clean", column="total")["first_layer"] == "clean"
    assert run(project, "raw,clean", column="amount")["found"] is False


def test_renamed_columns_match_through_the_map(tmp_path: Path):
    spec = {
        "raw": {},
        "clean": {"events": STEP, "rename": {"total": "total_usd"}},
        "pub": {"events": STEP, "rename": {"total": "total_usd"}},
    }
    project = layered(tmp_path, spec)
    d = run(project, column="total", mapping={"clean.total_usd": "total", "pub.total_usd": "total"})
    assert d["first_layer"] == "clean" and d["persists"] == ["pub"]
    assert d["layers"][1]["columns"] == ["total"]
    # without the map the renamed layers do not hold `total`: no layer shows the change
    assert run(project, column="total")["found"] is False
    # without --column the change is reported under the name each layer uses, or the mapped one
    d = run(project, mapping={"clean.total_usd": "total", "pub.total_usd": "total"})
    assert d["first_layer"] == "clean" and d["layers"][2]["status"] == "persists"


def test_a_change_that_disappears_and_comes_back_is_persisting_where_it_shows(tmp_path: Path):
    project = layered(tmp_path, {"raw": {"events": STEP}, "clean": {}, "pub": {"events": STEP}})
    d = run(project)
    assert d["first_layer"] == "raw"
    assert d["disappears"] == ["clean"] and d["persists"] == ["pub"]


def test_each_layer_uses_the_newest_version_on_or_before_a_date(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {"events": STEP}}, days=3, extra_days=2)
    d = bisect_layers(
        ["raw", "clean"], good_date="2026-03-02", bad_date="2026-03-10", project=project
    ).to_dict()
    assert d["layers"][1]["bad"]["business_date"] == "2026-03-05"  # the newest on or before
    assert d["layers"][1]["good"]["business_date"] == "2026-03-02"
    assert d["first_layer"] == "clean"
    # two dates that fall on the same version compare it with itself
    d = bisect_layers(
        ["raw", "clean"], good_date="2026-03-06", bad_date="2026-03-10", project=project
    ).to_dict()
    assert d["found"] is False
    assert d["layers"][1]["good"] == d["layers"][1]["bad"]


def test_thresholds_of_each_layer_apply(tmp_path: Path):
    # `clean` ignores `total`, so the change planted there is not seen; `pub` still shows it
    project = layered(
        tmp_path,
        {"raw": {}, "clean": {"events": STEP}, "pub": {"events": STEP}},
        thresholds={"clean": "    ignore: [total]"},
    )
    d = run(project)
    assert d["first_layer"] == "pub"
    assert d["layers"][1]["changed"] is False


# ---- unusable input -----------------------------------------------------------------------------


def test_unknown_source(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {}})
    with pytest.raises(Exception, match="no source 'nope'"):
        run(project, "raw,nope")


def test_no_version_on_or_before_a_date(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {}})
    with pytest.raises(HistoryError, match=r"no version.*on or before 2026-02-01"):
        bisect_layers(["raw", "clean"], good_date="2026-02-01", bad_date=BAD, project=project)


def test_dates_must_be_ordered_and_valid(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {}})
    with pytest.raises(HistoryError, match="not before"):
        bisect_layers(["raw"], good_date=BAD, bad_date=GOOD, project=project)
    with pytest.raises(HistoryError, match="YYYY-MM-DD"):
        bisect_layers(["raw"], good_date="yesterday", bad_date=BAD, project=project)


def test_a_source_without_a_baseline_has_no_registry(tmp_path: Path):
    layered(tmp_path, {"raw": {}})
    project = tmp_path / "shape.yml"
    project.write_text(project.read_text() + "  bare:\n    path: data/bare\n")
    with pytest.raises(HistoryError, match="bare.*baseline"):
        bisect_layers(["raw", "bare"], good_date=GOOD, bad_date=BAD, project=project)


def test_map_must_name_a_layer_and_a_column(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}, "clean": {}})
    for bad in ({"nolayer.x": "y"}, {"clean": "total"}, {"clean.total": ""}):
        with pytest.raises(HistoryError, match="--map"):
            run(project, "raw,clean", mapping=bad)


def test_layers_must_be_named(tmp_path: Path):
    project = layered(tmp_path, {"raw": {}})
    with pytest.raises(HistoryError, match="at least one layer"):
        bisect_layers([], good_date=GOOD, bad_date=BAD, project=project)


def test_a_project_is_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HistoryError, match=r"shape\.yml"):
        bisect_layers(["raw"], good_date=GOOD, bad_date=BAD)

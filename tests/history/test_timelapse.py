"""W3-03 deliverables 4 and 5: ``shape.history.timelapse`` and its outputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from history_helpers import History, add_days, build_history, value_history, value_table

import shape
from shape.history import HistoryError, timelapse
from shape.history.timelapse import load_timelapse, render_html, render_text
from shape.registry.local import LocalRegistry

STATS = ("row_count", "null_rate", "cardinality", "mean", "std")
QUANTILES = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")


def frames_of(h: History, **kw: Any) -> list[dict[str, Any]]:
    kw.setdefault("column", "total")
    return list(timelapse(h.registry, h.name, **kw).to_dict()["frames"])


def test_one_frame_per_version_with_the_stored_statistics(step_history: History):
    d = timelapse(step_history.registry, "orders", column="total").to_dict()
    assert d["format"] == "shape-timelapse" and d["version"] == 1
    assert d["name"] == "orders" and d["column"] == "total"
    assert d["window"] is None
    frames = d["frames"]
    assert len(frames) == 31
    assert [f["date"] for f in frames] == step_history.dates
    log = LocalRegistry(step_history.registry).log("orders")
    for f, entry in zip(frames, log, strict=True):
        assert f["content_ids"] == [entry["content_id"]] and f["versions"] == 1
        assert f["gap"] is False and f["form"] == "raw"
    # the statistics are the stored profile's
    import tempfile

    reg = LocalRegistry(step_history.registry)
    with tempfile.TemporaryDirectory() as work:
        p = Path(work) / "v.shape"
        p.write_bytes(reg.checkout("orders", log[20]["content_id"]))
        stored = shape.load(p).tables["orders"]
    col = stored["columns"]["total"]
    f = frames[20]
    assert f["row_count"] == stored["row_count"] == 2000
    assert f["null_rate"] == col["null_rate"]
    assert f["cardinality"] == col["cardinality"]
    assert f["mean"] == col["mean"] and f["std"] == col["std"]
    assert set(f["quantiles"]) == set(QUANTILES)
    assert f["quantiles"]["p50"] == col["quantiles"]["p50"]
    assert f["top_values"] is None  # a continuous column: its values are not categories
    assert json.loads(json.dumps(d)) == d


def test_top_values_of_a_category_column(step_history: History):
    f = frames_of(step_history, column="status")[0]
    assert [t["value"] for t in f["top_values"]] == ["completed", "shipped", "cancelled"]
    assert abs(sum(t["share"] for t in f["top_values"]) - 1.0) < 1e-6
    assert f["mean"] is None and f["quantiles"] is None


def test_the_planted_step_is_the_change_point(amount_history: History):
    frames = frames_of(amount_history, column="amount")
    marked = [f["date"] for f in frames if f["change_point"]]
    assert marked == [amount_history.event["start"]]
    (point,) = [f for f in frames if f["change_point"]]
    assert set(point["changes"]) & set(amount_history.event["detected_as"])
    assert all(f["change_point"] is False for f in frames if f is not point)
    d = timelapse(amount_history.registry, "orders", column="amount").to_dict()
    assert d["change_points"] == [amount_history.event["start"]]
    # the other columns did not change
    for other in ("status", "note"):
        assert not any(f["change_point"] for f in frames_of(amount_history, column=other))


def test_since_and_until_are_inclusive(amount_history: History):
    frames = frames_of(amount_history, column="amount", since="2026-03-14", until="2026-03-16")
    assert [f["date"] for f in frames] == ["2026-03-14", "2026-03-15", "2026-03-16"]
    # the first frame has nothing before it to be compared with
    assert [f["change_point"] for f in frames] == [False, True, False]
    with pytest.raises(HistoryError, match="no versions of .* between"):
        frames_of(amount_history, since="2027-01-01")
    with pytest.raises(HistoryError, match="YYYY-MM-DD"):
        frames_of(amount_history, since="soon")
    with pytest.raises(HistoryError, match="after"):
        frames_of(amount_history, since="2026-03-20", until="2026-03-10")


def test_thresholds_of_the_project_decide_the_change_points(step_history: History, tmp_path: Path):
    # default thresholds flag the day-to-day range noise of a heavy-tailed column, and a source's
    # ignore list or a threshold flag quiets it
    plain = frames_of(step_history, column="total")
    assert "2026-03-05" in [f["date"] for f in plain if f["change_point"]]
    quiet = frames_of(
        step_history,
        column="total",
        column_thresholds={"total": {"min_severity": "high"}},
    )
    assert not any(f["change_point"] and f["date"] == "2026-03-05" for f in quiet)


def test_a_column_missing_from_a_version_is_a_gap(tmp_path: Path):
    event = {
        "kind": "add_column",
        "table": "orders",
        "column": "region",
        "start": 2,
        "definition": {
            "type": "string",
            "generator": {"strategy": "weighted_enum", "values": {"n": 1, "s": 1}},
        },
    }
    h = build_history(tmp_path, 5, [event])
    frames = frames_of(h, column="region")
    assert [f["gap"] for f in frames] == [True, True, False, False, False]
    for f in frames[:2]:
        assert f["row_count"] is None and f["mean"] is None and f["quantiles"] is None
        assert f["change_point"] is False and f["content_ids"]
    assert frames[2]["top_values"] and frames[2]["cardinality"] == 2


def test_a_column_that_is_in_no_version_is_an_error(step_history: History):
    with pytest.raises(HistoryError, match="no_such_column.*in none of"):
        frames_of(step_history, column="no_such_column")
    with pytest.raises(HistoryError, match="no_such_table"):
        frames_of(step_history, table="no_such_table")


def test_a_dataset_needs_a_table_when_the_column_is_in_several(tmp_path: Path):
    import pandas as pd

    reg = LocalRegistry(tmp_path / "reg")
    for i in range(3):
        orders = pd.DataFrame({"qty": [1, 2, 3, 4 + i], "id": range(4)})
        items = pd.DataFrame({"qty": [10, 20, 30], "sku": ["a", "b", "c"]})
        prof = shape.profile({"orders": orders, "items": items}, name="shop")
        shape.save(prof, tmp_path / f"{i}.shape")
        reg.commit(
            "shop",
            (tmp_path / f"{i}.shape").read_bytes(),
            {"business_date": add_days("2026-03-01", i)},
            allow_raw=True,
        )
    with pytest.raises(HistoryError, match=r"--table"):
        timelapse(tmp_path / "reg", "shop", column="qty")
    d = timelapse(tmp_path / "reg", "shop", column="qty", table="items").to_dict()
    assert d["table"] == "items" and {f["row_count"] for f in d["frames"]} == {3}
    d = timelapse(tmp_path / "reg", "shop", column="sku").to_dict()  # only one table has it
    assert d["table"] == "items"


# ---- windows ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("window", "frames"), [("day", 31), ("week", 6), ("month", 1)])
def test_windows_merge_the_versions_of_a_period(step_history: History, window: str, frames: int):
    # 2026-03-01 is a Sunday: ISO weeks 9 to 14 hold 1, 7, 7, 7, 7 and 2 of the 31 days
    d = timelapse(step_history.registry, "orders", column="total", window=window).to_dict()
    assert d["window"] == window
    assert len(d["frames"]) == frames
    assert sum(f["versions"] for f in d["frames"]) == 31
    assert sum(f["row_count"] for f in d["frames"]) == 31 * 2000


def test_a_merged_window_is_the_merge_of_its_versions(step_history: History):
    d = timelapse(step_history.registry, "orders", column="total", window="week").to_dict()
    week = d["frames"][1]  # 2026-03-02 .. 2026-03-08
    assert week["versions"] == 7 and week["row_count"] == 14000
    assert week["date"] == "2026-03-02"
    assert len(week["content_ids"]) == 7
    singles = frames_of(step_history)[1:8]
    mean = sum(f["mean"] * f["row_count"] for f in singles) / 14000
    assert week["mean"] == pytest.approx(mean, rel=1e-9)  # exact statistics merge exactly
    assert week["top_values"] is None  # the merged profile holds no value counts


def test_window_change_point_is_the_first_window_of_the_new_regime(step_history: History):
    d = timelapse(step_history.registry, "orders", column="total", window="week").to_dict()
    marked = [f["date"] for f in d["frames"] if f["change_point"]]
    # the step is on the last day of the week of 03-09: that window is still mostly old data, so
    # the next window, all new data, is where the merged statistics move
    assert marked == ["2026-03-16"]


def test_windows_need_sketch_state_when_versions_are_merged(tmp_path: Path):
    h = value_history(tmp_path, 10, {"v": 5}, sketches=False)
    with pytest.raises(HistoryError, match="--sketches"):
        timelapse(h.registry, "feed", column="v", window="week")
    # one version per window needs no merge
    assert len(timelapse(h.registry, "feed", column="v", window="day").to_dict()["frames"]) == 10
    with pytest.raises(HistoryError, match="day, week or month"):
        timelapse(h.registry, "feed", column="v", window="year")


# ---- share-safe versions ------------------------------------------------------------------------


def test_a_share_safe_version_contributes_only_what_its_safe_form_holds(tmp_path: Path):
    from shape.privacy.safe_profile import to_safe_profile

    h = value_history(tmp_path, 3, {})
    reg = LocalRegistry(h.registry)
    safe_json = to_safe_profile(shape.profile(value_table(1, {}), name="feed")).to_json()
    reg.commit("feed", safe_json, {"business_date": add_days(h.dates[-1], 1)})
    d = timelapse(h.registry, "feed", column="v").to_dict()
    raw, safe = d["frames"][0], d["frames"][3]
    assert safe["form"] == "safe" and raw["form"] == "raw"
    assert safe["gap"] is False
    assert safe["row_count"] == 301 and safe["cardinality"] == 301
    assert safe["quantiles"]["p50"] is not None and safe["mean"] is not None
    # a safe frame is not diffed, and the result says so
    assert safe["change_point"] is None
    assert any("share-safe" in n for n in d["notes"])
    # the safe form keeps no value counts for a continuous column, nothing is read from data
    assert safe["top_values"] is None


def test_a_share_safe_category_column_keeps_its_safe_weights(tmp_path: Path):
    import pandas as pd

    from shape.privacy.safe_profile import to_safe_profile

    reg = LocalRegistry(tmp_path / "reg")
    df = pd.DataFrame({"s": ["a"] * 60 + ["b"] * 30 + ["c"] * 10})
    reg.commit(
        "t", to_safe_profile(shape.profile(df, name="t")).to_json(), {"business_date": "2026-03-01"}
    )
    f = timelapse(tmp_path / "reg", "t", column="s").to_dict()["frames"][0]
    assert [t["value"] for t in f["top_values"]] == ["a", "b", "c"]
    assert f["top_values"][0]["share"] == pytest.approx(0.6)


def test_unknown_names_and_registries_are_errors(step_history: History, tmp_path: Path):
    with pytest.raises(HistoryError, match="no versions of 'nope'"):
        timelapse(step_history.registry, "nope", column="total")
    with pytest.raises(HistoryError, match="not a registry"):
        timelapse(tmp_path / "missing", "orders", column="total")


# ---- the text, JSON and HTML outputs ------------------------------------------------------------


def test_text_output_has_a_sparkline_per_statistic(step_history: History):
    text = render_text(timelapse(step_history.registry, "orders", column="total").to_dict())
    lines = text.splitlines()
    assert lines[0].startswith("orders.total")
    for stat in ("row_count", "null_rate", "cardinality", "mean", "std", "p1", "p50", "p99"):
        assert any(line.startswith(stat) for line in lines), stat
    mean_line = next(line for line in lines if line.startswith("mean"))
    assert re.search(r"[▁▂▃▄▅▆▇█]{10,}", mean_line)
    assert mean_line.index("█") > mean_line.index("▁")  # it rises with the step
    assert any(line.startswith("change") and "2026-03-15" in line for line in lines)


def test_text_shows_gaps_and_unknowns(tmp_path: Path):
    event = {
        "kind": "add_column",
        "table": "orders",
        "column": "region",
        "start": 2,
        "definition": {
            "type": "string",
            "generator": {"strategy": "weighted_enum", "values": {"n": 1, "s": 1}},
        },
    }
    h = build_history(tmp_path, 4, [event])
    d = timelapse(h.registry, "orders", column="region").to_dict()
    assert "·" in next(line for line in render_text(d).splitlines() if line.startswith("row_count"))


def data_of(html: str) -> dict[str, Any]:
    found = re.search(
        r'<script type="application/json" id="shape-timelapse-data">(.*?)</script>', html, re.S
    )
    assert found, "the frames are not embedded"
    doc: dict[str, Any] = json.loads(found.group(1))
    return doc


def test_html_is_one_self_contained_file_that_renders_from_its_inline_data(
    step_history: History,
):
    d = timelapse(step_history.registry, "orders", column="total").to_dict()
    html = render_html(d)
    assert html.startswith("<!doctype html>")
    # no network: no URL of any kind, no external resource
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", html, re.I)
    assert not re.search(r"<link\b|<script[^>]*\bsrc=|@import|url\(|\bfetch\(|XMLHttpRequest", html)
    assert not re.search(r"""\b(?:src|href)\s*=\s*["']?(?!#)""", html)
    # the frames come from the inline data, which is the JSON output
    assert data_of(html) == d
    assert len(data_of(html)["frames"]) == 31
    # the page's script reads that data and draws svg from it; play control, slider, change points
    assert "getElementById('shape-timelapse-data')" in html
    assert "<svg" in html or "svg" in html
    assert 'type="range"' in html and 'id="play"' in html
    assert "change_point" in html
    assert html.count("<script") == 2


def test_html_escapes_the_data_it_embeds(step_history: History):
    d = timelapse(step_history.registry, "orders", column="status").to_dict()
    d["name"] = "</script><b>x</b>"
    html = render_html(d)
    assert "</script><b>" not in html
    assert data_of(html)["name"] == "</script><b>x</b>"


# ---- the persisted format -----------------------------------------------------------------------


def test_json_declares_format_and_version_and_loads_back(step_history: History, tmp_path: Path):
    d = timelapse(step_history.registry, "orders", column="total").to_dict()
    out = tmp_path / "t.json"
    out.write_text(json.dumps(d))
    assert load_timelapse(out) == d


def test_a_newer_version_and_other_formats_are_refused(tmp_path: Path):
    out = tmp_path / "t.json"
    for doc, message in (
        ({"format": "shape-timelapse", "version": 2, "frames": []}, "newer"),
        ({"format": "shape-timelapse", "version": "1", "frames": []}, "version"),
        ({"format": "shape-timelapse", "version": 0, "frames": []}, "version"),
        ({"format": "other", "version": 1, "frames": []}, "not a timelapse"),
        ({"format": "shape-timelapse", "version": 1}, "frames"),
        ([1, 2], "not a timelapse"),
    ):
        out.write_text(json.dumps(doc))
        with pytest.raises(HistoryError, match=message):
            load_timelapse(out)
    out.write_text("not json")
    with pytest.raises(HistoryError, match="not valid JSON"):
        load_timelapse(out)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "history"


def test_every_frozen_version_still_loads():
    found = sorted(FIXTURES.glob("v*/timelapse.json"))
    assert found, "the time-capsule fixtures are missing"
    for path in found:
        doc = load_timelapse(path)
        assert doc["format"] == "shape-timelapse" and isinstance(doc["version"], int)
        # and the current renderers still draw it
        assert render_text(doc) and data_of(render_html(doc)) == doc


def test_v1_capsule_content():
    doc = load_timelapse(FIXTURES / "v1" / "timelapse.json")
    assert doc["version"] == 1
    assert doc["column"] == "v" and len(doc["frames"]) == 4
    assert [f["change_point"] for f in doc["frames"]] == [False, False, True, False]
    assert doc["frames"][1]["gap"] is False

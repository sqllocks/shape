"""DM-08 (talk kit): every quoted number traces to a committed measurement file."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "demo"
BASE = REPO / "benchmarks" / "baselines" / "2026-09-30-product"


def _load_sheet_builder():
    spec = importlib.util.spec_from_file_location("demo_sheet", DEMO / "build_benchmark_sheet.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_sheet"] = mod
    spec.loader.exec_module(mod)
    return mod


sheet = _load_sheet_builder()
TALK = (DEMO / "TALK.md").read_text(encoding="utf-8")
BENCH = (DEMO / "BENCHMARKS.md").read_text(encoding="utf-8")


def test_benchmark_sheet_is_up_to_date():
    assert BENCH == sheet.build()
    assert sheet.main(["--check"]) == 0


def _product():
    return json.loads((BASE / "product_bench.json").read_text(encoding="utf-8"))


def test_benchmark_sheet_matches_product_json():
    d = _product()
    for key in ("d1.parquet", "d2.parquet", "d2.csv", "d3.parquet", "d4.parquet"):
        r = d["profile"][key]
        assert r["output_identical_across_runs"] is True, key
        assert sheet.trunc(r["median_s"]) in BENCH, key
        assert sheet.trunc_int(r["rows_per_s"]) in BENCH, key
        assert sheet.trunc_int(r["peak_rss_mb"]) in BENCH, key
    assert f"{d['_meta']['cores']} cores" in BENCH
    assert sheet.trunc_int(d["startup"]["cli_version_cmd_median_s"] * 1000) in BENCH


def test_benchmark_sheet_truncates_never_rounds_up():
    assert sheet.trunc(1.969) == "1.96"
    assert sheet.trunc_int(509_495.99) == "509,495"


def test_product_measurements_are_complete():
    d = _product()
    assert d["_meta"]["runs"] >= 3 and d["startup"]["runs"] >= 5
    for key, r in d["profile"].items():
        assert len(r["runs_s"]) == d["_meta"]["runs"], key
        assert r["rows_per_s"] == r["rows"] / r["median_s"], key
        assert r["peak_rss_mb"] > 0 and r["output_identical_across_runs"], key


def test_talk_benchmark_numbers_come_from_the_sheet():
    section = TALK.split("## Benchmark sheet", 1)[1].split("Say exactly this", 1)[0]
    seconds = re.findall(r"(\d[\d,]*\.\d+) s\b", section)
    rates = re.findall(r"\| (\d[\d,]*) \|", section)
    memory = re.findall(r"(\d[\d,]*) MB", section)
    millis = re.findall(r"(\d+) ms", section)
    assert len(seconds) >= 4 and len(rates) >= 4 and len(memory) >= 4 and len(millis) == 2
    for value in seconds + rates + memory + millis:
        assert value in BENCH, value
    for source in ("product_bench.json", "4 cores", "not Fabric"):
        assert source in section


def test_talk_quoted_sentences_match_the_sheet():
    d2 = _product()["profile"]["d2.parquet"]
    d3 = _product()["profile"]["d3.parquet"]
    assert f"took {sheet.trunc(d2['median_s'], 1)} s" in TALK
    assert f"peaked at\n  {sheet.trunc_int(d2['peak_rss_mb'])} MB" in TALK
    assert f"took {sheet.trunc(d3['median_s'], 1)} s" in TALK
    assert f"{sheet.trunc_int(d3['peak_rss_mb'])} MB" in TALK
    assert "1M-row" in TALK and "20-column" in TALK


def test_nothing_in_the_kit_names_the_retired_baseline():
    # The owner's rule: no mention of the earlier project, in any form, on anything shown or
    # handed out. The kit files and the talk docs are scanned case-insensitively.
    word = "spin" + "dle"
    for path in [
        DEMO / n
        for n in (
            "TALK.md",
            "BENCHMARKS.md",
            "DRIFT.md",
            "LIVE_TIMINGS.md",
            "build_benchmark_sheet.py",
        )
    ]:
        assert word not in path.read_text(encoding="utf-8").lower(), path.name
    for path in (REPO / "docs" / "talks").rglob("*"):
        if path.is_file():
            assert word not in path.read_text(encoding="utf-8").lower(), str(path.relative_to(REPO))


def test_public_wording():
    for name in ("TALK.md", "BENCHMARKS.md", "DRIFT.md", "LIVE_TIMINGS.md"):
        text = (DEMO / name).read_text(encoding="utf-8").lower()
        for banned in (
            "retire",
            "successor",
            "rebuild",
            "replac",
            "predecessor",
            "sunset",
            "old tool",
            "previous tool",
            "faster than before",
            "vs ",
        ):
            assert banned not in text, (name, banned)


def test_the_kit_makes_no_comparison_claims():
    text = (TALK + BENCH).lower()
    for banned in ("speedup", "speed-up", "equivalen", "times faster"):
        assert banned not in text, banned
    assert not re.search(r"\bport\b", text)


def test_live_timings_is_a_marked_placeholder():
    text = (DEMO / "LIVE_TIMINGS.md").read_text(encoding="utf-8")
    assert "PLACEHOLDER" in text and "Not measured yet" in text
    rows = [r for r in text.splitlines() if r.startswith("| ") and "---" not in r][1:]
    assert rows and all("TBD" in r for r in rows)
    assert not re.search(r"\|\s*\d+(\.\d+)?\s*\|", "\n".join(rows))  # no timings filled in


def test_talk_references_integration_artifacts_and_they_exist():
    for path in (
        "integrations/fabric/RUNBOOK.md",
        "integrations/fabric/notebooks",
        "integrations/fabric/environment",
        "integrations/fabric/udf",
        "integrations/fabric/pipelines",
    ):
        assert (REPO / path).exists(), path
    assert "integrations/fabric/" in TALK
    for name in (
        "shape_profile",
        "shape_profile_spark",
        "shape_gate_notebook",
        "shape_gate_udf",
        "profileLakehouseFile",
        "checkProfile",
    ):
        assert name in TALK


def test_talk_covers_the_storyline_and_fallbacks():
    for beat in (
        "Profile in a notebook",
        "HTML report",
        "PySpark",
        "passes on day 1",
        "Day 2 fails",
        "UDF",
        "benchmark",
    ):
        assert beat.lower() in TALK.lower(), beat
    assert "Fallbacks" in TALK

"""DM-08 (talk kit): every quoted number traces to a committed baseline file."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEMO = REPO / "demo"
BASE = REPO / "benchmarks" / "baselines" / "2026-09-29"


def _load_sheet_builder():
    spec = importlib.util.spec_from_file_location("demo_sheet", DEMO / "build_benchmark_sheet.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_sheet"] = mod
    spec.loader.exec_module(mod)
    return mod


sheet = _load_sheet_builder()
TALK = (DEMO / "TALK.md").read_text()
BENCH = (DEMO / "BENCHMARKS.md").read_text()


def test_benchmark_sheet_is_up_to_date():
    assert BENCH == sheet.build()
    assert sheet.main(["--check"]) == 0


def test_benchmark_sheet_matches_baseline_json():
    prof = json.loads((BASE / "profile_bench.json").read_text())
    retail = json.loads((BASE / "retail_bench.json").read_text())
    assert f"{prof['d2.parquet']['spindle']['median_s']:.2f}" in BENCH
    assert f"{prof['d2.parquet']['port-mt']['median_s']:.2f}" in BENCH
    assert f"{retail['scales']['large']['summary']['spindle']['total_s']:.2f}" in BENCH
    assert f"{prof['_meta']['cores']} cores" in BENCH


def test_talk_benchmark_numbers_come_from_the_sheet():
    section = TALK.split("## Benchmark sheet", 1)[1].split("Say exactly this", 1)[0]
    seconds = re.findall(r"(\d[\d,]*\.\d+) s\b", section)
    speedups = re.findall(r"\((\d+\.\d)x\)", section)
    assert len(seconds) >= 12 and len(speedups) >= 8
    for value in seconds:
        assert value in BENCH, value
    for value in speedups:
        assert f"{value}x" in BENCH, value
    for source in ("profile_bench.json", "retail_bench.json", "4 cores"):
        assert source in section


def test_talk_quoted_sentences_round_the_sheet_values():
    prof = json.loads((BASE / "profile_bench.json").read_text())["d2.parquet"]
    assert f"{prof['spindle']['median_s']:.1f} s" in TALK
    assert f"{prof['port-mt']['median_s']:.1f} s" in TALK
    assert "1M-row" in TALK and "20-column" in TALK


def test_public_wording():
    for name in ("TALK.md", "BENCHMARKS.md", "DRIFT.md", "LIVE_TIMINGS.md"):
        text = (DEMO / name).read_text().lower()
        for banned in ("retire", "successor", "rebuild", "replac", "predecessor", "sunset"):
            assert banned not in text, (name, banned)


def test_live_timings_is_a_marked_placeholder():
    text = (DEMO / "LIVE_TIMINGS.md").read_text()
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

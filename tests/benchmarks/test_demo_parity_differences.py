"""The demo parity harness's named differences from DEMO-REHEARSAL (#304, #307) and HUNT2-scenario
(#701).

``shape demo run --estimate`` and ``--dry-run`` print and estimate the rows the scale preset
writes (#304), where the baseline prints ``--rows`` while its run writes the preset's rows; and
the ``adventureworks`` description names what the run generates (#307), where the baseline's names
four tables its run never makes; and ``cleanup --dry-run`` judges a local file by the real
cleanup's rules (#701), so a recorded file that is already gone is left alone, where the baseline
lists every artifact as one it would remove. ``demo_1to1/differences.py`` maps the baseline's
text to what Shape must print for each, exactly, and nothing else. Nothing here needs the
baseline's venv.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "benchmarks" / "vs_refengine" / "demo_1to1"
_spec = importlib.util.spec_from_file_location("demo_1to1_differences", HARNESS / "differences.py")
assert _spec is not None and _spec.loader is not None
differences = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = differences  # its dataclasses look their module up
_spec.loader.exec_module(differences)

BLOCK_1000 = (
    "  Rows to generate: 1,000\n  Targets: generated\n"
    "  Estimated CU-minutes: 0.0\n  Estimated duration: ~0.2 min\n"
)
BLOCK_21750 = (
    "  Rows to generate: 21,750\n  Targets: generated\n"
    "  Estimated CU-minutes: 0.0\n  Estimated duration: ~0.2 min\n"
)
BASELINE_DRY_RUN = (
    "\nCost estimate for retail (1,000 rows):\n"
    + BLOCK_1000
    + "\n[dry-run] Would write 1,000 rows to: generated via scale_mode=local\n"
)


def _rows(text: str, *, dry_run: bool = True) -> str:
    return differences.rows_as_run(
        text,
        scenario="retail",
        asked=1000,
        written=21750,
        scale="small",
        asked_block=BLOCK_1000,
        written_block=BLOCK_21750,
        dry_run=dry_run,
    )


def test_both_differences_are_named_with_a_probe() -> None:
    named = {d.name: d for d in differences.ALLOWED}
    assert named["rows-are-what-runs"].probe == "probe_estimate_rows"
    assert named["description-names-what-runs"].probe == "probe_descriptions"


def test_the_baseline_dry_run_becomes_the_preset_rows_shape_prints() -> None:
    assert _rows(BASELINE_DRY_RUN).splitlines() == [
        "",
        "Cost estimate for retail (small scale preset: 21,750 rows):",
        *BLOCK_21750.splitlines(),
        "",
        "[dry-run] Would write 21,750 rows (small scale preset) to: generated via scale_mode=local",
    ]


def test_an_estimate_has_no_dry_run_line() -> None:
    text = "\nCost estimate for retail (1,000 rows):\n" + BLOCK_1000
    assert "21,750 rows" in _rows(text, dry_run=False)


@pytest.mark.parametrize(
    "text",
    [
        BASELINE_DRY_RUN.replace("(1,000 rows)", "(2,000 rows)"),  # the header changed
        BASELINE_DRY_RUN.replace("Targets: generated", "Targets: lakehouse"),  # another block
        BASELINE_DRY_RUN.replace("Would write 1,000 rows", "Would write 1,000 records"),
    ],
)
def test_baseline_text_that_no_longer_matches_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        _rows(text)


def test_the_adventureworks_description_maps_to_shapes_and_nothing_else_does() -> None:
    old, new = differences.DESCRIPTIONS["adventureworks"]
    assert "DimCustomer" in old and "DimCustomer" not in new
    assert differences.describe(f"> {old}\n") == f"> {new}\n"
    assert differences.describe(old[:60], width=60) == new[:60]
    # a `demo list` line: name, modes, the description cut at 60 characters (here after a space)
    line = f"{'adventureworks':20} {'inference, seeding':30} {old[:60]}"
    assert old[:60].endswith(" ")
    mapped = line.replace(old[:60], new[:60]).rstrip()
    assert differences.describe(line, width=60).rstrip() == mapped
    other = "Retail scenario: the retail domain's tables."
    assert differences.describe(other) == other


DRY_RUN_LINES = [
    "  [dry-run] Would remove: file/a",
    "  [dry-run] Would remove: warehouse/b",
    "  [dry-run] Would remove: warehouse/f",
    "  [dry-run] Would remove: sql_db/c",
]


def test_the_dry_run_file_difference_is_named_with_a_probe() -> None:
    named = {d.name: d for d in differences.ALLOWED}
    assert named["dry-run-judges-files"].probe == "probe_cleanup_dry_run_files"
    assert "#701" in named["dry-run-judges-files"].reason


def test_a_gone_file_is_left_alone_after_what_would_be_removed() -> None:
    assert differences.gone_files_left_alone(DRY_RUN_LINES, ["a"]) == [
        "  [dry-run] Would remove: warehouse/b",
        "  [dry-run] Would remove: warehouse/f",
        "  [dry-run] Would remove: sql_db/c",
        "  Left alone: file/a (already gone)",
    ]


def test_with_no_gone_file_the_lines_are_unchanged() -> None:
    assert differences.gone_files_left_alone(DRY_RUN_LINES, []) == DRY_RUN_LINES


@pytest.mark.parametrize(
    ("lines", "gone"),
    [
        (DRY_RUN_LINES, ["b"]),  # only a local file is judged; a warehouse table is not
        (DRY_RUN_LINES, ["z"]),  # the baseline does not list it
        (DRY_RUN_LINES + ["  [dry-run] Would remove: file/a"], ["a"]),  # listed twice
        ([x.replace("Would remove", "Would delete") for x in DRY_RUN_LINES], ["a"]),  # new text
    ],
)
def test_baseline_dry_run_lines_that_no_longer_match_are_refused(
    lines: list[str], gone: list[str]
) -> None:
    with pytest.raises(ValueError):
        differences.gone_files_left_alone(lines, gone)

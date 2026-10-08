"""The nightly parity suite (P8-02): shards, merge, the green verdict, history and streak.

Nothing here needs the baseline checkout or its venv: shard results are built in the test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"
sys.path.insert(0, str(BENCH))


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load("vs_refengine_run_p802", BENCH / "run.py")
nightly = _load("vs_refengine_nightly_p802", BENCH / "nightly.py")

DOMAINS = ["hr", "retail", "telecom"]
PASS = {"status": "pass", "exit_code": 0, "command": "verify.py"}


def _rec(kind: str, wid: str, verifier: dict | None = PASS, median: float | None = 1.0) -> dict:
    rec: dict[str, Any] = {
        "kind": kind,
        "median_s": median,
        "runs_s": [] if median is None else [median],
        "verifier": verifier,
        "speedup_vs_refengine": None if median is None else 10.0,
    }
    if median is not None:
        rec["refengine_median_s"] = 10.0 * median
    if kind == "profile":
        rec["dataset"] = wid.split(":", 1)[1]
    else:
        _, rec["domain"], *rest = wid.split(":")
        rec["scale"] = rest[-1]
    return rec


def _shard_results(selection_args: list[str]) -> dict:
    """What run.py --full writes for one shard, every verifier passing."""
    only = selection_args[1]
    shard = None
    if "--shard" in selection_args:
        k, n = selection_args[selection_args.index("--shard") + 1].split("/")
        shard = (int(k), int(n))
    gen = run.select_units(run.generate_plan(True, DOMAINS), shard) if only == "generate" else []
    exp = run.expected_ids(run.FULL_PROFILE_DATASETS, gen, run.FULL_STREAM_SCALES, only)
    out: dict[str, Any] = {
        "schema_version": 1,
        "mode": "full",
        "runs": 5,
        "generated_utc": "2026-10-04T03:17:00Z",
        "machine": {"cores": 4},
        "refengine_commit": "422e78d",
        "shape_commit": "abc1234",
        "shape_tree_dirty": False,
        "refengine": {"workloads": {}},
        "reference_port": {"workloads": {}},
        "shape": {"workloads": {}},
        "selection": {
            "only": only,
            "shard": None if shard is None else f"{shard[0]}/{shard[1]}",
            "domains": None,
            "datasets": None,
            "baseline_domains": DOMAINS if only == "generate" else None,
            "expected": exp,
        },
    }
    for impl, ids in exp.items():
        for wid in ids:
            kind = wid.split(":", 1)[0]
            out[impl]["workloads"][wid] = _rec(kind, wid)
            out["refengine"]["workloads"].setdefault(
                wid, {**_rec(kind, wid, None, 10.0), "speedup_vs_refengine": None}
            )
    return out


def _write_night(tmp: Path, mutate: Any = None, skip: tuple[str, ...] = ()) -> Path:
    d = tmp / "night"
    for name, args in nightly.SHARDS.items():
        if name in skip:
            continue
        res = _shard_results(args)
        rc = 0
        if mutate is not None:
            rc = mutate(name, res) or 0
        (d / name).mkdir(parents=True, exist_ok=True)
        (d / name / "results.json").write_text(json.dumps(res))
        status = {"shard": name, "exit_code": rc, "run_py_args": args, "subset": False}
        (d / name / "status.json").write_text(json.dumps(status))
    return d


# ── the plan of the full suite ───────────────────────────────────────────────


def test_full_plan_covers_every_domain_and_every_profiling_dataset() -> None:
    gen = run.generate_plan(True, DOMAINS)
    exp = run.expected_ids(run.FULL_PROFILE_DATASETS, gen, run.FULL_STREAM_SCALES, "all")
    for impl in ("reference_port", "shape"):
        assert {f"profile:{d}" for d in run.FULL_PROFILE_DATASETS} <= set(exp[impl])
    assert {f"generate:{d}:medium" for d in DOMAINS} <= set(exp["shape"])
    assert "generate:retail:large" in exp["shape"]
    assert {"generate:retail:medium", "generate:retail:large"} <= set(exp["reference_port"])
    assert "stream:retail:order:medium" in exp["shape"]
    for d in ("d1", "d2", "d3", "d4"):
        assert {f"{d}.csv", f"{d}.parquet"} <= set(run.FULL_PROFILE_DATASETS)
    assert "mt" in run.FULL_PROFILE_DATASETS


def test_quick_plan_is_unchanged() -> None:
    assert run.generate_plan(False, None) == [("reference_port", "retail", ["small", "medium"])]


def test_generate_shards_partition_the_plan() -> None:
    units = run.generate_plan(True, DOMAINS + ["a", "b", "c", "d"])
    n = nightly.GENERATE_SHARDS
    parts = [run.select_units(units, (k, n)) for k in range(1, n + 1)]
    flat = [u for p in parts for u in p]
    assert sorted(map(str, flat)) == sorted(map(str, units))
    assert all(parts)
    assert {f"generate-{k}" for k in range(1, n + 1)} <= set(nightly.SHARDS)
    assert {"profile", "stream"} <= set(nightly.SHARDS)


@pytest.mark.parametrize("text", ["0/3", "4/3", "x", "1-3"])
def test_bad_shard_is_rejected(text: str) -> None:
    with pytest.raises(Exception):  # noqa: B017 - argparse type error
        run.parse_shard(text)


def test_dry_run_selects_a_subset(capsys: pytest.CaptureFixture[str]) -> None:
    assert run.main(["--full", "--only", "profile", "--dataset", "d1.csv", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "profile: ['d1.csv']" in out and "generate: -" in out and "stream: -" in out


def test_dataset_outside_the_mode_is_an_error() -> None:
    with pytest.raises(SystemExit):
        run.main(["--quick", "--dataset", "d3.csv", "--dry-run"])


# ── verifier rule ────────────────────────────────────────────────────────────


def test_verifier_passed_needs_exit_0_and_the_first_run_too() -> None:
    assert nightly.verifier_passed({"verifier": PASS})
    assert not nightly.verifier_passed({"verifier": None})
    assert not nightly.verifier_passed({"verifier": {**PASS, "exit_code": 1}})
    assert not nightly.verifier_passed({"verifier": {**PASS, "status": "fail"}})
    bad_first = {**PASS, "first_run": {"status": "fail", "exit_code": 1}}
    assert not nightly.verifier_passed({"verifier": bad_first})
    assert nightly.verifier_passed({"verifier": {**PASS, "first_run": dict(PASS)}})


# ── merge and verdict ────────────────────────────────────────────────────────


def test_a_complete_passing_night_is_green(tmp_path: Path) -> None:
    merged, verdict = nightly.merge(_write_night(tmp_path), run=run)
    assert verdict["green"], verdict["reasons"]
    assert verdict["full_suite"] and verdict["passed"] == verdict["required"] > 0
    assert merged is not None and merged["nightly"]["green"]
    assert run.validate(merged, json.loads(run.SCHEMA_FILE.read_text())) == []
    assert "generate:telecom:medium" in merged["shape"]["workloads"]
    assert set(merged["machine"]["shards"]) == set(nightly.SHARDS)


def test_a_failed_verifier_makes_the_night_red(tmp_path: Path) -> None:
    def mutate(name: str, res: dict) -> int:
        if name == "profile":
            res["shape"]["workloads"]["profile:d4.csv"]["verifier"] = {
                "status": "fail",
                "exit_code": 1,
                "command": "verify.py",
            }
            return 1
        return 0

    _, verdict = nightly.merge(_write_night(tmp_path, mutate), run=run)
    assert not verdict["green"]
    assert verdict["failed"] == ["shape profile:d4.csv (verifier fail)"]
    assert any("run.py exited 1" in r for r in verdict["reasons"])


def test_a_missing_shard_makes_the_night_red(tmp_path: Path) -> None:
    _, verdict = nightly.merge(_write_night(tmp_path, skip=("generate-2",)), run=run)
    assert not verdict["green"]
    assert any("generate-2" in r for r in verdict["reasons"])
    assert verdict["missing"]


def test_a_domain_left_out_makes_the_night_red(tmp_path: Path) -> None:
    def mutate(name: str, res: dict) -> None:
        res["shape"]["workloads"].pop("generate:telecom:medium", None)

    _, verdict = nightly.merge(_write_night(tmp_path, mutate), run=run)
    assert not verdict["green"]
    assert verdict["missing"] == ["shape generate:telecom:medium"]


def test_a_dataset_left_out_makes_the_night_red(tmp_path: Path) -> None:
    def mutate(name: str, res: dict) -> None:
        res["reference_port"]["workloads"].pop("profile:mt", None)

    _, verdict = nightly.merge(_write_night(tmp_path, mutate), run=run)
    assert verdict["missing"] == ["reference_port profile:mt"]


def test_a_dirty_tree_or_mixed_commits_make_the_night_red(tmp_path: Path) -> None:
    def dirty(name: str, res: dict) -> None:
        res["shape_tree_dirty"] = name == "stream"

    _, verdict = nightly.merge(_write_night(tmp_path, dirty), run=run)
    assert not verdict["green"]
    assert "measured from a modified Shape tree" in verdict["reasons"]

    def mixed(name: str, res: dict) -> None:
        if name == "stream":
            res["shape_commit"] = "def5678"

    _, verdict = nightly.merge(_write_night(tmp_path / "b", mixed), run=run)
    assert any("shape_commit" in r for r in verdict["reasons"])


def test_a_null_median_is_not_a_pass(tmp_path: Path) -> None:
    def mutate(name: str, res: dict) -> None:
        if name == "stream":
            res["shape"]["workloads"]["stream:retail:order:medium"]["median_s"] = None

    _, verdict = nightly.merge(_write_night(tmp_path, mutate), run=run)
    assert not verdict["green"]


def test_a_subset_is_never_green(tmp_path: Path) -> None:
    _, verdict = nightly.merge(_write_night(tmp_path), shards=["profile"], run=run)
    assert not verdict["green"] and not verdict["full_suite"]


# ── history and streak ───────────────────────────────────────────────────────


def _night(date: str, green: bool = True, event: str = "schedule", run_id: str = "") -> dict:
    return {
        "date": date,
        "event": event,
        "run_id": run_id or date,
        "green": green,
        "full_suite": True,
        "shape_commit": "abc1234",
        "required": 10,
        "passed": 10 if green else 9,
        "reasons": [],
    }


def test_streak_counts_consecutive_green_scheduled_nights() -> None:
    h = [_night(f"2026-10-{d:02d}") for d in range(1, 8)]
    assert len(nightly.streak(h)) == 7
    h.append(_night("2026-10-08", green=False))
    assert nightly.streak(h) == []
    h.append(_night("2026-10-09"))
    assert len(nightly.streak(h)) == 1


def test_a_missing_night_ends_the_streak() -> None:
    h = [_night(f"2026-10-{d:02d}") for d in (1, 2, 3, 5, 6)]
    assert [r["date"] for r in nightly.streak(h)] == ["2026-10-06", "2026-10-05"]


def test_manual_and_subset_runs_do_not_count() -> None:
    h = [_night("2026-10-01"), _night("2026-10-02", event="workflow_dispatch")]
    assert [r["date"] for r in nightly.streak(h)] == ["2026-10-01"]
    h.append({**_night("2026-10-02"), "full_suite": False})
    assert [r["date"] for r in nightly.streak(h)] == ["2026-10-01"]


def test_a_rerun_replaces_its_record(tmp_path: Path) -> None:
    hist = tmp_path / "h.jsonl"
    nightly.append_history(hist, _night("2026-10-01", green=False, run_id="42"))
    nightly.append_history(hist, _night("2026-10-01", green=True, run_id="42"))
    recs = nightly.read_history(hist)
    assert len(recs) == 1 and recs[0]["green"]


def test_streak_command_exit_code(tmp_path: Path) -> None:
    hist = tmp_path / "h.jsonl"
    for d in range(1, 7):
        nightly.append_history(hist, _night(f"2026-10-{d:02d}"))
    assert nightly.main(["streak", "--history", str(hist), "--need", "7"]) == 1
    nightly.append_history(hist, _night("2026-10-07"))
    assert nightly.main(["streak", "--history", str(hist), "--need", "7"]) == 0


def test_merge_command_writes_results_verdict_and_history(tmp_path: Path) -> None:
    night = _write_night(tmp_path)
    out, hist = tmp_path / "out" / "results.json", tmp_path / "h.jsonl"
    rc = nightly.main(
        [
            "merge",
            "--in-dir",
            str(night),
            "--out",
            str(out),
            "--history",
            str(hist),
            "--event",
            "schedule",
            "--run-id",
            "7",
            "--date",
            "2026-10-04",
        ]
    )
    assert rc == 0
    assert json.loads((out.parent / "nightly.json").read_text())["green"]
    assert nightly.read_history(hist)[0]["date"] == "2026-10-04"


def test_run_shard_records_the_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "fake_run.py"
    fake.write_text("import sys; sys.exit(1)\n")
    monkeypatch.setattr(nightly, "RUN_PY", fake)
    rc = nightly.run_shard("stream", tmp_path / "n", ["--dataset", "d1.csv"])
    st = json.loads((tmp_path / "n" / "stream" / "status.json").read_text())
    assert rc == 1 and st["exit_code"] == 1 and st["subset"]
    assert not (tmp_path / "n" / "stream" / "results.json").exists()


def test_the_workflow_matrix_lists_every_shard() -> None:
    """The nightly workflow's matrix (or, until the lead applies it, the exact diff in the
    lane status file) runs every shard of ``nightly.SHARDS``."""
    wf = ROOT / ".github" / "workflows" / "nightly.yml"
    status = ROOT / "docs" / "plans" / "lane_status" / "P8-02.md"
    text = wf.read_text() if "parity-suite:" in wf.read_text() else status.read_text()
    line = next(ln for ln in text.splitlines() if "shard: [" in ln)
    names = line.split("[", 1)[1].split("]", 1)[0].replace(" ", "").split(",")
    assert names == list(nightly.SHARDS)

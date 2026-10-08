"""The docs performance page (T-24, P8-02): only verified numbers are published, and the page
never names the baseline library (D-13)."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(ROOT / "scripts"))
from refengine_name import names_refengine  # noqa: E402

page = _load("gen_performance_page_p802", ROOT / "scripts" / "gen_performance_page.py")
BASE = page.BASELINE_KEY  # the renamed results key, "refengine"
PASS = {"status": "pass", "exit_code": 0, "command": "verify.py"}


def _results() -> dict[str, Any]:
    def rec(kind: str, median: float, speedup: float, **kw: Any) -> dict[str, Any]:
        return {
            "kind": kind,
            "median_s": median,
            "runs_s": [median],
            "verifier": dict(PASS),
            "speedup_vs_refengine": speedup,
            "refengine_median_s": median * speedup,
            **kw,
        }

    return {
        "schema_version": 1,
        "mode": "full",
        "runs": 5,
        "generated_utc": "2026-10-04T05:00:00Z",
        "machine": {"cores": 4, "cpu": "Test CPU", "python": "3.11.15"},
        BASE: {"workloads": {}},
        "refengine_commit": "422e78d",
        "shape_commit": "abc1234def5678",
        "shape_tree_dirty": False,
        "reference_port": {
            "workloads": {"profile:d1.csv": rec("profile", 0.777777, 4.44, dataset="d1.csv")}
        },
        "shape": {
            "workloads": {
                "profile:d1.csv": rec("profile", 0.123456, 12.34, dataset="d1.csv"),
                "profile:d2.parquet": rec("profile", 0.654321, 16.5, dataset="d2.parquet"),
                "generate:hr:medium": rec("generate", 0.251234, 21.7, domain="hr", scale="medium"),
                "stream:retail:order:medium": rec(
                    "stream", 0.561111, 15.8, domain="retail", scale="medium"
                ),
            }
        },
    }


def test_verified_numbers_are_published() -> None:
    text = page.render(_results())
    assert "| D1 (200k rows x 6 columns), CSV | pass | 1.523 | 0.123 | 12.3x |" in text
    assert "| hr, medium | pass | 5.452 | 0.251 | 21.7x |" in text
    assert "15.8x" in text and "16.5x" in text


def test_a_failed_verifier_publishes_no_number() -> None:
    r = _results()
    r["shape"]["workloads"]["generate:hr:medium"]["verifier"] = {
        "status": "fail",
        "exit_code": 1,
        "command": "verify.py",
    }
    text = page.render(r)
    assert "| hr, medium | verifier fail | - | - | - |" in text
    assert "21.7x" not in text and "0.251" not in text and "5.452" not in text


def test_a_failed_first_verifier_run_publishes_no_number() -> None:
    r = _results()
    r["shape"]["workloads"]["stream:retail:order:medium"]["verifier"] = {
        **PASS,
        "first_run": {"status": "fail", "exit_code": 1, "command": "verify.py"},
    }
    text = page.render(r)
    assert "15.8x" not in text and "0.561" not in text


@pytest.mark.parametrize("status,code", [("pass", 1), ("unavailable", 2), ("error", 0)])
def test_only_exit_0_with_pass_counts(status: str, code: int) -> None:
    r = _results()
    r["shape"]["workloads"]["profile:d1.csv"]["verifier"] = {
        "status": status,
        "exit_code": code,
        "command": "verify.py",
    }
    assert "12.3x" not in page.render(r)


def test_a_null_verifier_publishes_no_number() -> None:
    r = _results()
    r["shape"]["workloads"]["profile:d1.csv"]["verifier"] = None
    assert "12.3x" not in page.render(r)


def test_the_reference_port_is_never_published() -> None:
    text = page.render(_results())
    assert "4.4x" not in text and "0.778" not in text


def test_a_modified_tree_publishes_nothing() -> None:
    r = _results()
    r["shape_tree_dirty"] = True
    text = page.render(r)
    for n in ("12.3x", "16.5x", "21.7x", "15.8x", "0.123", "0.251"):
        assert n not in text
    assert "No verified measurement is published yet." in text


def test_the_baseline_median_falls_back_to_its_own_record() -> None:
    r = _results()
    rec = r["shape"]["workloads"]["profile:d1.csv"]
    del rec["refengine_median_s"]
    r[BASE]["workloads"]["profile:d1.csv"] = {"kind": "profile", "median_s": 2.0}
    assert "| pass | 2.000 | 0.123 | 12.3x |" in page.render(r)
    r[BASE]["workloads"]["profile:d1.csv"]["median_s"] = None
    assert "12.3x" not in page.render(r)


def test_the_page_never_names_the_baseline() -> None:
    text = page.render(_results())
    assert BASE not in text.lower() and not names_refengine(text)
    r = _results()
    r["shape"]["workloads"]["generate:x:medium"] = {
        **copy.deepcopy(r["shape"]["workloads"]["generate:hr:medium"]),
        "domain": BASE.capitalize() + "_demo",
    }
    with pytest.raises(page.PageError):
        page.render(r)


def test_nightly_status_and_history_are_shown() -> None:
    r = _results()
    r["nightly"] = {"green": True, "passed": 4, "required": 4}
    hist = [
        {"date": "2026-10-03", "event": "schedule", "full_suite": True, "green": False,
         "shape_commit": "1111111aaa", "passed": 3, "required": 4},
        {"date": "2026-10-04", "event": "schedule", "full_suite": True, "green": True,
         "shape_commit": "2222222bbb", "passed": 4, "required": 4},
        {"date": "2026-10-04", "event": "workflow_dispatch", "full_suite": True, "green": True,
         "shape_commit": "3333333ccc", "passed": 4, "required": 4},
    ]  # fmt: skip
    text = page.render(r, hist)
    assert "This run: **green**, 4 of 4 workloads verified." in text
    assert "| 2026-10-04 | `2222222` | green | 4/4 |" in text
    assert "| 2026-10-03 | `1111111` | red | 3/4 |" in text
    assert "3333333" not in text


def test_the_committed_page_matches_the_committed_results() -> None:
    assert page.main(["--check"]) == 0, "run: python scripts/gen_performance_page.py"


def test_main_writes_the_page(tmp_path: Path) -> None:
    res, out = tmp_path / "r.json", tmp_path / "PERF.md"
    res.write_text(json.dumps(_results()))
    assert page.main(["--results", str(res), "--out", str(out)]) == 0
    assert "12.3x" in out.read_text()
    assert page.main(["--results", str(res), "--out", str(out), "--check"]) == 0
    out.write_text("stale")
    assert page.main(["--results", str(res), "--out", str(out), "--check"]) == 1

"""scripts/baseline_diff_report.py: REL-092's review report for re-recorded baselines."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "baseline_diff_report",
    Path(__file__).resolve().parents[2] / "scripts" / "baseline_diff_report.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def table(dataset_id: str, sha: str = "a") -> dict[str, object]:
    return {
        "rows": 3,
        "columns": [{"name": "id", "type": "int64"}],
        "dataset_id": dataset_id,
        "parquet_sha256": sha,
        "parquet_bytes": 10,
    }


def store(root: Path, ids: dict[str, str]) -> Path:
    files = {}
    (root / "generate").mkdir(parents=True)
    for domain, ds in ids.items():
        rec = {
            "workload": f"generate:{domain}:medium",
            "seeds": {"42": {"tables": {"t": table(ds)}, "dataset_id": ds}},
            "identifier_forms": [],
        }
        p = root / "generate" / f"{domain}_medium.json"
        p.write_text(json.dumps(rec))
        files[f"generate/{domain}_medium.json"] = {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()
        }
    (root / "MANIFEST.json").write_text(
        json.dumps({"shape_version": "x", "shape_commit": "c", "recorded_utc": "t", "files": files})
    )
    return root


def test_reports_changed_and_unchanged_domains(tmp_path: Path) -> None:
    old = store(tmp_path / "old", {"healthcare": "1", "retail": "9"})
    new = store(tmp_path / "new", {"healthcare": "2", "retail": "9"})
    text, changed = mod.report(old, new, ("healthcare",))
    assert changed == {"healthcare"}
    assert "### healthcare: CHANGED" in text and "### retail: unchanged" in text
    assert mod.main([str(old), str(new), "--out", str(tmp_path / "r.md")]) == 0
    assert mod.main([str(old), str(new), "--domains", "retail", "--fail-on-unexpected"]) == 1


def test_tampered_record_is_refused(tmp_path: Path) -> None:
    old = store(tmp_path / "old", {"healthcare": "1"})
    (old / "generate" / "healthcare_medium.json").write_text("{}")
    with pytest.raises(SystemExit):
        mod.load_store(old)

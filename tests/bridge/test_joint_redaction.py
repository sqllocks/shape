"""#533: a diff change or a check violation about several columns (the joint analysis) withholds
the raw values of a classified one, as an entry about one column does."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

SECRET = "@secret.example"


def _write(path: Path, broken: bool) -> Path:
    """``email`` (40 addresses, a classified pattern) determines ``region``; ``broken`` makes 10
    addresses map to two regions, so the dependency breaks and the analysis names them."""
    with open(path, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["id", "email", "region"])
        for i in range(3000):
            k = i % 40
            region = "nsew"[k % 4]
            if broken and k < 10 and (i // 40) % 2:
                region = "nsew"[(k + 1) % 4]
            out.writerow([i, f"person{k}{SECRET}", region])
    return path


@pytest.fixture
def profiles(api, tmp_path):
    out = []
    for name, broken in (("before", False), ("after", True)):
        src = _write(tmp_path / f"{name}.csv", broken)
        api.ok("profile", source=str(src), output=str(tmp_path / f"{name}.shape"))
        out.append(str(tmp_path / f"{name}.shape"))
    return out


def _fd_contract(tmp_path: Path) -> str:
    path = tmp_path / "fd.json"
    rule = {"determinant": "email", "dependent": "region", "min_confidence": 0.99}
    path.write_text(json.dumps({"fd": [rule]}))
    return str(path)


def test_a_broken_dependency_on_a_classified_column_withholds_its_values(api, profiles):
    before, after = profiles
    result = api.ok("diff", before=before, after=after)
    joint = [c for c in result["changes"] if c["kind"] == "dependency_broken"]
    assert joint, result["changes"]
    assert SECRET not in json.dumps(result)
    assert all(c["redacted"] is True for c in joint)
    assert all(c["message"] is None and c["detail"] is None for c in joint)


def test_include_raw_values_returns_the_joint_detail(api, profiles):
    before, after = profiles
    result = api.ok("diff", {"include_raw_values": True}, before=before, after=after)
    assert SECRET in json.dumps(result)  # the values are there to withhold


def test_a_check_fd_violation_on_a_classified_column_withholds_its_values(api, profiles, tmp_path):
    result = api.ok("check", profile=profiles[1], contract=_fd_contract(tmp_path))
    (violation,) = result["violations"]
    assert violation["rule"] == "fd" and violation["redacted"] is True
    assert violation["observed"] is None
    assert SECRET not in json.dumps(result)
    raw = api.ok(
        "check", {"include_raw_values": True}, profile=profiles[1], contract=_fd_contract(tmp_path)
    )
    assert SECRET in json.dumps(raw)


@pytest.mark.parametrize(
    "label, detail, involved",
    [
        ("t.email -> region", {"determinant": ["email"], "dependent": "region"}, {"email"}),
        ("a, b -> c", None, {"a", "b", "c"}),
        ("t.a ~ t.b", {"measure": "pearson"}, {"t.a", "t.b"}),
        ("t.(a, b) in zip", {"columns": ["a", "b"]}, {"a", "b"}),
        ("t.(rows)", {}, set()),
        ("x='v' => y='w'", None, {"x", "y"}),
    ],
)
def test_the_columns_an_entry_is_about(label, detail, involved):
    from shape.bridge.handlers.flow import entry_columns

    entry = {"column": label, "detail": detail}
    assert involved <= entry_columns(entry)

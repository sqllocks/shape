"""SAC-01: a committed .shape gives a clean, readable, noise-free git workflow (real git)."""

from __future__ import annotations

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shape.cli.gitcmds import python_textconv_command

SHAPE = [sys.executable, "-m", "shape.cli.main"]
GIT_OK = shutil.which("git") is not None


def _shape(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONHASHSEED": str(random.randrange(1, 1000))}
    r = subprocess.run(
        [*SHAPE, *args], cwd=cwd, capture_output=True, text=True, env=env, check=False
    )
    if check:
        assert r.returncode == 0, r.stderr
    return r


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "core.autocrlf=false",
         *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert r.returncode == 0, f"git {' '.join(args)} exited {r.returncode}: {r.stderr}"
    return r.stdout


def _orders(path: Path, week: int) -> None:
    rng = random.Random(7)
    statuses = ["new", "paid", "shipped"] + (["lost"] if week >= 2 else [])
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["order_id", "email", "status", "total"])
        for i in range(2000):
            email = (
                ""
                if rng.random() < (0.2 if week >= 2 else 0.05)
                else f"u{rng.randint(1, 99)}@example.com"
            )
            total = round(rng.uniform(5, 500) * (1.4 if week >= 2 else 1), 2)
            w.writerow([i, email, rng.choice(statuses), total])


def _profile(cwd: Path, src: str) -> None:
    _shape(cwd, "profile", src, "-o", "orders.shape", "--name", "orders")
    _shape(cwd, "profile", "safe", "orders.shape", "-o", "orders.safe.json")


@pytest.mark.skipif(not GIT_OK, reason="git is not installed")
def test_git_workflow_end_to_end(tmp_path):
    repo = tmp_path
    _git(repo, "init", "-q", ".")
    _orders(repo / "w1.csv", 1)
    _orders(repo / "w2.csv", 2)
    _shape(
        repo,
        "git-setup",
        "--command",
        python_textconv_command(),
        "--pattern",
        "*.shape",
        "--pattern",
        "*.safe.json",
    )

    _profile(repo, "w1.csv")
    _git(repo, "add", "orders.shape", "orders.safe.json", ".gitattributes")
    _git(repo, "commit", "-qm", "week 1")

    # Re-profile unchanged data in separate processes: nothing to commit.
    time.sleep(2.1)
    _profile(repo, "w1.csv")
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    # A real change: one changed line per changed property, the path visible.
    _profile(repo, "w2.csv")
    diff = _git(repo, "diff", "--", "orders.shape")
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert "Binary files" not in diff
    assert any(ln.startswith("-columns.email.null_rate: ") for ln in removed)
    assert any(ln.startswith("+columns.email.null_rate: ") for ln in added)
    assert len(removed) == len({ln.split(":")[0] for ln in removed})  # one line per property
    assert not any("manifest.name" in ln for ln in removed + added)  # the name did not move
    assert any(ln.startswith("+columns.status.enum_values.lost: ") for ln in added)

    sdiff = _git(repo, "diff", "--", "orders.safe.json")
    changed = [ln for ln in sdiff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
    # *.safe.json is diffed through `shape cat` too, so its lines carry the full path.
    assert any(ln.startswith("-tables.orders.columns.email.null_rate: ") for ln in changed)
    assert any(".categorical_weights.lost: " in ln for ln in changed)

    # The safe artifact carries no real value, and validates.
    safe = (repo / "orders.safe.json").read_text()
    assert "example.com" not in safe
    assert _shape(repo, "profile", "validate", "--safe", "orders.safe.json").returncode == 0


def test_name_is_kept_on_overwrite_and_overridable(tmp_path):
    _orders(tmp_path / "w1.csv", 1)
    _orders(tmp_path / "w2.csv", 2)
    _shape(tmp_path, "profile", "w1.csv", "-o", "o.shape")
    first = json.loads(_shape(tmp_path, "inspect", "o.shape").stdout)["name"]
    assert first == "w1"  # no --name, no existing file: the input's name
    _shape(tmp_path, "profile", "w2.csv", "-o", "o.shape")
    assert json.loads(_shape(tmp_path, "inspect", "o.shape").stdout)["name"] == "w1"  # kept
    _shape(tmp_path, "profile", "w2.csv", "-o", "o.shape", "--name", "orders")
    assert json.loads(_shape(tmp_path, "inspect", "o.shape").stdout)["name"] == "orders"
    cat = _shape(tmp_path, "cat", "o.shape").stdout
    assert 'manifest.name: "orders"\n' in cat and 'name: "orders"\n' in cat


def test_name_follows_input_when_output_is_not_a_profile(tmp_path):
    _orders(tmp_path / "w1.csv", 1)
    (tmp_path / "o.shape").write_bytes(b"not an artifact")
    _shape(tmp_path, "profile", "w1.csv", "-o", "o.shape")
    assert json.loads(_shape(tmp_path, "inspect", "o.shape").stdout)["name"] == "w1"


def test_cat_is_stable_sorted_one_property_per_line(tmp_path):
    _orders(tmp_path / "w1.csv", 1)
    _shape(tmp_path, "profile", "w1.csv", "-o", "a.shape", "--name", "orders")
    out = _shape(tmp_path, "cat", "a.shape").stdout
    assert out.endswith("\n") and "\n\n" not in out
    paths = [ln.split(": ", 1)[0] for ln in out.splitlines()]
    assert len(paths) == len(set(paths))
    assert "columns.email.null_rate" in paths
    assert not any("content" in p for p in paths)  # no hashes: nothing volatile
    pretty = json.loads(_shape(tmp_path, "inspect", "--pretty", "a.shape").stdout)
    assert pretty["profile"]["columns"]["email"]["null_rate"] == pytest.approx(0.05, abs=0.03)
    # A git textconv filter is handed a file without the .shape extension.
    (tmp_path / "tmpfile").write_bytes((tmp_path / "a.shape").read_bytes())
    assert _shape(tmp_path, "cat", "tmpfile").stdout == out


def test_safe_json_is_byte_stable_and_sorted(tmp_path):
    _orders(tmp_path / "w1.csv", 1)
    _shape(tmp_path, "profile", "w1.csv", "-o", "a.shape", "--name", "orders")
    _shape(tmp_path, "profile", "safe", "a.shape", "-o", "s1.json")
    time.sleep(1.1)
    _shape(tmp_path, "profile", "safe", "a.shape", "-o", "s2.json")
    text = (tmp_path / "s1.json").read_text()
    assert text == (tmp_path / "s2.json").read_text()
    assert text.endswith("}\n")
    assert json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n" == text


@pytest.mark.skipif(not GIT_OK, reason="git is not installed")
def test_git_setup_is_idempotent_and_local(tmp_path):
    _git(tmp_path, "init", "-q", ".")
    (tmp_path / ".gitattributes").write_text("*.csv text")
    for _ in range(2):
        _shape(tmp_path, "git-setup", "--command", "shape cat")
    attrs = (tmp_path / ".gitattributes").read_text()
    assert attrs == "*.csv text\n*.shape diff=shape\n"
    cfg = _git(tmp_path, "config", "--local", "--get", "diff.shape.textconv").strip()
    assert cfg == "shape cat"
    sub = tmp_path / "x"
    sub.mkdir()
    out = json.loads(_shape(sub, "git-setup", "--command", "shape cat").stdout)
    assert out["added"] == [] and Path(out["repository"]).samefile(tmp_path)


def test_git_setup_outside_a_repository_exits_2(tmp_path):
    if not GIT_OK:
        pytest.skip("git is not installed")
    env_dir = tmp_path / "nogit"
    env_dir.mkdir()
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)}
    r = subprocess.run(
        [*SHAPE, "git-setup", "--repo", str(env_dir)], capture_output=True, text=True, env=env
    )
    assert r.returncode == 2 and "git" in r.stderr

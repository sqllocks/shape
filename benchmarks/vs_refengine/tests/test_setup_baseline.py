"""FRZ-01 setup contract with stand-in Python/pip; never installs a baseline."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "setup_baseline.sh"


def standins(tmp_path):
    bins = tmp_path / "bin"
    bins.mkdir()
    boot = bins / "python3"
    boot.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import os,sys,json,pathlib
args=sys.argv[1:]
if args[:2]==['-m','venv']:
 p=pathlib.Path(args[2])/'bin';p.mkdir(parents=True)
 worker=pathlib.Path(os.environ['WORKER'])
 for name in ('python','shape'):(p/name).write_text(worker.read_text());(p/name).chmod(0o755)
elif args and args[0]=='-c':
 sys.argv=['-c',*args[2:]];exec(args[1])
"""
    )
    boot.chmod(0o755)
    worker = tmp_path / "worker"
    worker.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import os,sys,json
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[:3]==['-m','pip','install']:
 if os.environ.get('FAIL_INDEX')=='1' and '--find-links' in args:sys.exit(1)
 if os.environ.get('FAIL_ALL')=='1' and not any('.source' in a for a in args):sys.exit(1)
elif args[:3]==['-m','pip','freeze']:
 print('sqllocks-shape==0.9.1\\nsqllocks-shape-domains==0.9.1\\npyarrow==25.0.1')
elif args[:2]==['doctor','--json']:print(json.dumps({'kernel':os.environ.get('KERNEL','rust')}))
elif args and args[0]=='-c':print(os.environ.get('FOUND_VERSION','0.9.1'))
"""
    )
    worker.chmod(0o755)
    git = bins / "git"
    git.write_text(
        '#!/bin/sh\nif [ "$1" = rev-parse ]; then pwd; '
        'else mkdir -p "$4/plugins/shape-domains"; fi\n'
    )
    git.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(bins) + os.pathsep + os.environ["PATH"],
        WORKER=str(worker),
        CALLS=str(tmp_path / "calls"),
        BASELINE_VENV=str(tmp_path / "venv"),
        BENCH_OUT_DIR=str(tmp_path / "out"),
    )
    for name in ("BASELINE_INDEX", "BASELINE_PURE", "PYTHON"):
        env.pop(name, None)
    return env


@pytest.mark.parametrize(
    "mode", ["pypi", "directory", "url", "fallback_index", "worktree", "pure_allowed"]
)
def test_install_routes_and_freeze(tmp_path, mode):
    env = standins(tmp_path)
    if mode in ("directory", "fallback_index"):
        index = tmp_path / "wheels"
        index.mkdir()
        env["BASELINE_INDEX"] = str(index)
    if mode == "url":
        env["BASELINE_INDEX"] = "https://packages.example/simple"
    if mode == "fallback_index":
        env["FAIL_INDEX"] = "1"
    if mode == "worktree":
        env["FAIL_ALL"] = "1"
    if mode == "pure_allowed":
        env.update(KERNEL="python", BASELINE_PURE="1")
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    freeze = (tmp_path / "out" / "baseline_freeze.txt").read_text()
    assert "sqllocks-shape==0.9.1" in freeze and "pyarrow==25.0.1" in freeze
    calls = (tmp_path / "calls").read_text()
    if mode == "directory":
        assert "--find-links" in calls
    if mode == "url":
        assert "--index-url" in calls
    if mode == "worktree":
        assert ".source" in calls


@pytest.mark.parametrize("setting,value", [("FOUND_VERSION", "0.9.2"), ("KERNEL", "python")])
def test_wrong_version_or_kernel_is_refused(tmp_path, setting, value):
    env = standins(tmp_path)
    env[setting] = value
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 2
    assert value in r.stderr


def test_no_key_or_engine_configuration():
    text = SCRIPT.read_text()
    assert "REFENGINE" not in text and "ssh" not in text.lower() and "DEPLOY_KEY" not in text

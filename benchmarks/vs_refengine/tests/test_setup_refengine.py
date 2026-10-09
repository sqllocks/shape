"""Private reference setup uses an ephemeral deploy key and pinned GitHub host keys."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "benchmarks" / "vs_refengine" / "setup_refengine.sh"
PIN = "422e78df2267e73bb2fa976267e48cb437861e2f"
KEY = "synthetic-deploy-key\nsecond-line"


def _standins(tmp_path):
    import sys

    tools = tmp_path / "bin"
    tools.mkdir()
    git = tools / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import json, os, shlex, sys\n"
        "from pathlib import Path\n"
        "record = {'args': sys.argv[1:]}\n"
        "if sys.argv[1] == 'clone':\n"
        "    command = shlex.split(os.environ.get('GIT_SSH_COMMAND', ''))\n"
        "    record['ssh'] = command\n"
        "    record['secret_in_env'] = 'REFENGINE_DEPLOY_KEY' in os.environ\n"
        "    if '-i' in command:\n"
        "        key = Path(command[command.index('-i') + 1])\n"
        "        record.update(key=str(key), mode=key.stat().st_mode & 0o777,\n"
        "                      content=key.read_text())\n"
        "    Path(sys.argv[3], '.git').mkdir(parents=True)\n"
        "with open(os.environ['SETUP_CALLS'], 'a') as out:\n"
        "    out.write(json.dumps(record) + '\\n')\n"
        "sys.exit(int(os.environ.get('CLONE_EXIT', '0')) if sys.argv[1] == 'clone' else 0)\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    python = tools / "python3"
    python.write_text(
        '#!/bin/sh\nmkdir -p "$3/bin"\n'
        "printf '#!/bin/sh\\nexit 0\\n' > \"$3/bin/python\"\n"
        'chmod +x "$3/bin/python"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        REFENGINE_NAME="fixture",
        REFENGINE_DEPLOY_KEY=KEY,
        REFENGINE_ROOT=str(tmp_path / "checkout"),
        REFENGINE_VENV=str(tmp_path / "venv"),
        BENCH_OUT_DIR=str(tmp_path / "out"),
        TMPDIR=str(tmp_path),
        SETUP_CALLS=str(tmp_path / "calls.jsonl"),
    )
    return env


@pytest.mark.parametrize("clone_exit", [0, 128])
def test_deploy_key_is_private_pinned_and_removed(tmp_path, clone_exit):
    env = _standins(tmp_path)
    env["CLONE_EXIT"] = str(clone_exit)
    run = subprocess.run(["bash", "-x", str(SCRIPT)], env=env, text=True, capture_output=True)
    assert run.returncode == clone_exit, run.stderr
    assert KEY not in run.stdout + run.stderr
    calls = [json.loads(line) for line in Path(env["SETUP_CALLS"]).read_text().splitlines()]
    clone = calls[0]
    assert clone["args"] == ["clone", "git@github.com:sqllocks/fixture.git", env["REFENGINE_ROOT"]]
    assert clone["mode"] == 0o600 and clone["content"] == KEY + "\n"
    assert clone["secret_in_env"] is False
    assert not Path(clone["key"]).exists()
    assert not list(tmp_path.glob("shape-refengine-key.*"))
    assert clone["ssh"][:3] == ["ssh", "-F", "/dev/null"]
    options = shlex.join(clone["ssh"])
    for required in ["IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes"]:
        assert required in options
    host_option = next(part for part in clone["ssh"] if part.startswith("UserKnownHostsFile="))
    hosts = Path(host_option.split("=", 1)[1])
    assert hosts == SCRIPT.with_name("github_known_hosts")
    assert "GlobalKnownHostsFile=/dev/null" in options
    assert hosts.read_text().count("github.com ") == 3
    if clone_exit == 0:
        assert calls[1]["args"] == ["-C", env["REFENGINE_ROOT"], "checkout", PIN]
        assert (Path(env["BENCH_OUT_DIR"]) / "refengine_freeze.txt").is_file()
    else:
        assert len(calls) == 1


@pytest.mark.parametrize("key", [None, ""])
def test_missing_deploy_key_fails_before_clone(tmp_path, key):
    env = _standins(tmp_path)
    if key is None:
        del env["REFENGINE_DEPLOY_KEY"]
    else:
        env["REFENGINE_DEPLOY_KEY"] = key
    run = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True)
    assert run.returncode != 0
    assert "REFENGINE_DEPLOY_KEY" in run.stderr
    assert not Path(env["SETUP_CALLS"]).exists()
    assert not list(tmp_path.glob("shape-refengine-key.*"))

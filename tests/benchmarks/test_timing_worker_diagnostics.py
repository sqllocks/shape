"""Failed fresh-process timing workers retain their actionable output."""

import subprocess

import pytest

from .test_bench_scripts import BENCH, _load


@pytest.mark.parametrize("family", ["profile", "generate", "stream", "stream-cli"])
def test_failed_timing_worker_retains_command_exit_and_tail(family, monkeypatch, tmp_path, capsys):
    relative = {
        "profile": "profile_1to1/bench.py",
        "generate": "domain_1to1/bench.py",
        "stream": "stream_1to1/bench.py",
        "stream-cli": "stream_1to1/bench.py",
    }[family]
    mod = _load("failed_timing_" + family.replace("-", "_"), BENCH / relative)
    monkeypatch.setattr(mod, "wait_for_quiet", lambda: 0.0)
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(
            command,
            11,
            "\n".join(f"timing-line-{i}" for i in range(240)),
            "TypeError: actionable timing worker failure",
        ),
    )
    if family == "profile":
        with pytest.raises(subprocess.CalledProcessError) as caught:
            mod.run_once("baseline", "d1.csv")
        assert caught.value.returncode == 11
    elif family == "generate":
        with pytest.raises(RuntimeError, match="exit 11"):
            mod.run_once("baseline", "shape", "retail", "small")
    elif family == "stream":
        with pytest.raises(RuntimeError, match="exit 11"):
            mod.run_once("baseline", "small", None)
    else:
        monkeypatch.setattr(
            mod, "cli_command", lambda *a: ["baseline-shape", "-o", str(tmp_path / "events")]
        )
        with pytest.raises(RuntimeError, match="exit 11"):
            mod.run_cli("baseline", "small", None)
    error = capsys.readouterr().err
    assert "command" in error and "exit 11" in error
    assert "timing-line-239" in error and "timing-line-0\n" not in error
    assert "TypeError: actionable timing worker failure" in error

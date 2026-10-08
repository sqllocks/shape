"""#240: ``shape cat`` (the git textconv) writes UTF-8 even when the pipe's code page is not."""

from __future__ import annotations

import os
import subprocess
import sys


def test_cat_survives_a_cp1252_pipe(tmp_path):
    (tmp_path / "u.csv").write_text("id,name\n1,Zoë\n2,Łukasz\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    run = [sys.executable, "-m", "shape"]
    made = subprocess.run(
        # full capture: a safe capture withholds the two one-row names (W1-11)
        [*run, "profile", "u.csv", "--capture", "full", "-o", "u.shape"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    assert made.returncode == 0, made.stderr
    cat = subprocess.run([*run, "cat", "u.shape"], cwd=tmp_path, env=env, capture_output=True)
    assert cat.returncode == 0, cat.stderr
    assert "Łukasz".encode() in cat.stdout

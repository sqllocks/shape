"""#309: the healthcare demo's inference path needs ``faker``; without it the error names the extra.

Moved here from ``tests/plugins/test_optional_plugin_pieces.py``: the run needs this plugin's
healthcare domain, so it belongs in the plugin's own suite, where the plugin is installed. The CLI
runs in a subprocess where ``faker`` cannot be imported, as in a venv that does not have it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: run in a subprocess before anything else: these modules behave as if not installed
_ABSENT = """
import importlib.abc, sys
_NAMES = tuple(sys.argv.pop(1).split(","))
class _Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == n or name.startswith(n + ".") for n in _NAMES):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, _Absent())
"""


def _run_without(absent: str, code: str, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHAPE_")}
    env["SHAPE_HOME"] = str(cwd / "home")
    return subprocess.run(
        [sys.executable, "-c", _ABSENT + code, absent, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=600,
    )


def test_healthcare_inference_without_faker_fails_and_names_the_extra(tmp_path: Path) -> None:
    code = "import sys; from shape.cli.main import main; sys.exit(main())"
    r = _run_without("faker", code, tmp_path, "demo", "run", "healthcare", "--rows", "1000")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pip install 'sqllocks-shape[faker]'" in r.stderr, r.stderr

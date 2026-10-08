"""The pure wheel's license metadata must match pyproject (D-15: MIT)."""

from __future__ import annotations

import importlib.util
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _build(tmp_path: Path) -> Path:
    spec = importlib.util.spec_from_file_location("bpw", ROOT / "scripts" / "build_pure_wheel.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.build(tmp_path)


def test_wheel_license_matches_pyproject(tmp_path: Path) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wheel = _build(tmp_path)
    with zipfile.ZipFile(wheel) as z:
        meta_name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        meta = z.read(meta_name).decode()
        names = z.namelist()
    assert f"License-Expression: {project['license']}" in meta
    assert project["license"] == "MIT"
    assert "Apache" not in meta.split("\n\n", 1)[0]
    for f in project["license-files"]:
        assert f"License-File: {f}" in meta
        assert any(n.endswith(f".dist-info/licenses/{f}") for n in names)

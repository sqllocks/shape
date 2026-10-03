"""scripts/check_user_facing.py, the D-13 gate: which files it scans and what it reports.

The baseline library's name is assembled at run time, so this file passes the scan itself.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_user_facing", ROOT / "scripts" / "check_user_facing.py"
)
assert _spec and _spec.loader
cuf = importlib.util.module_from_spec(_spec)
sys.modules["check_user_facing"] = cuf
_spec.loader.exec_module(cuf)

NAME = "spin" + "dle"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(cuf, "ROOT", tmp_path)

    def write(rel: str, text: str = "clean\n") -> Path:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
        return p

    return write


def test_hits_name_each_line_in_any_case():
    data = f"ok\nuse {NAME.upper()}\nfine\nthe {NAME.title()} way\n".encode()
    assert cuf.hits("f.md", data) == ["f.md:2", "f.md:4"]
    assert cuf.hits("f.md", b"nothing here") == []


@pytest.mark.parametrize(
    "rel",
    [
        "src/shape/x.py",
        "rust/k/lib.rs",
        "plugins/p/README.md",
        "integrations/f/a.json",
        "demo/TALK.md",
        "README.md",
        "pyproject.toml",
        "docs/X.md",
        "docs/talks/t/SCRIPT.md",
    ],
)
def test_user_facing_files_are_scanned(tree, rel):
    tree(rel, f"a {NAME} mention\n")
    assert cuf.main([]) == 1


@pytest.mark.parametrize(
    "rel",
    [
        "docs/plans/PLAN.md",
        "THIRD_PARTY_NOTICES.md",
        "benchmarks/vs/x.py",
        "tests/t.py",
        "src/shape/__pycache__/x.pyc",
        "rust/target/release/x",
        "src/shape/_kernel.abi3.so",
    ],
)
def test_planning_notices_tests_and_build_output_are_not_scanned(tree, rel, capsys):
    tree(rel, f"a {NAME} mention\n")
    tree("README.md")
    assert cuf.main([]) == 0
    assert "clean" in capsys.readouterr().out


def test_wheel_and_sdist_members_are_scanned(tree, tmp_path, capsys):
    tree("README.md")
    whl = tmp_path / "x-1-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("shape/ok.py", "x = 1\n")
        z.writestr("shape/bad.py", f"# {NAME}\n")
    sdist = tmp_path / "x-1.tar.gz"
    with tarfile.open(sdist, "w:gz") as t:
        data = f"{NAME}\n".encode()
        info = tarfile.TarInfo("x-1/PKG-INFO")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    assert cuf.main(["--wheel", str(whl), "--wheel", str(sdist)]) == 1
    out = capsys.readouterr().out
    assert f"{whl.name}!shape/bad.py:1" in out
    assert f"{sdist.name}!x-1/PKG-INFO:1" in out
    assert "ok.py" not in out


def test_the_repository_is_clean():
    assert cuf.ROOT == ROOT
    assert cuf.main([]) == 0

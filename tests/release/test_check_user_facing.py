"""scripts/check_user_facing.py, the D-13 gate: which files it scans and what it reports.

The word under test is ``REFENGINE_NAME`` when set (the real name, matched by the stored digest)
and a dummy word with its own digest otherwise; this file never spells the name.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
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

# The word under test: the reference engine's short name when REFENGINE_NAME is set (matched by
# the stored digest); otherwise a dummy word, with the scanner pointed at the dummy's digest.
NAME = os.environ.get("REFENGINE_NAME") or "zebrafy"
WORD = NAME.encode()


@pytest.fixture(autouse=True)
def _word_digest(request, monkeypatch):
    # the scans of the real repository always use the stored digest
    clean = request.node.name in {"test_repository_is_clean", "test_the_repository_is_clean"}
    if not os.environ.get("REFENGINE_NAME") and not clean:
        digest = hashlib.sha256(WORD).hexdigest()
        real = cuf.names_refengine
        monkeypatch.setattr(cuf, "names_refengine", lambda t: real(t, digest, len(WORD)))


def test_hits_reports_each_line_any_case():
    data = b"ok\n" + WORD.upper() + b" here\nfine\nx" + WORD.title() + b"\n"
    assert cuf.hits("f", data) == ["f:2", "f:4"]
    assert cuf.hits("f", b"nothing to see") == []


def test_wheel_and_sdist_members_are_scanned(tmp_path):
    whl = tmp_path / "a-1-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("pkg/", b"")
        z.writestr("pkg/m.py", b"x = '" + WORD + b"'\n")
        z.writestr("pkg/ok.py", b"x = 1\n")
    sdist = tmp_path / "a-1.tar.gz"
    with tarfile.open(sdist, "w:gz") as t:
        body = b"# " + WORD + b"\n"
        info = tarfile.TarInfo("a-1/README.md")
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
        folder = tarfile.TarInfo("a-1/dir")
        folder.type = tarfile.DIRTYPE
        t.addfile(folder)
    members = dict(cuf.archive_members(whl))
    assert set(members) == {f"{whl.name}!pkg/m.py", f"{whl.name}!pkg/ok.py"}
    assert [label for label, _ in cuf.archive_members(sdist)] == [f"{sdist.name}!a-1/README.md"]


def test_main_on_archives(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cuf, "tree_files", lambda: [])
    clean = tmp_path / "c.whl"
    with zipfile.ZipFile(clean, "w") as z:
        z.writestr("m.py", b"x = 1\n")
    assert cuf.main(["--wheel", str(clean)]) == 0
    assert "clean" in capsys.readouterr().out
    dirty = tmp_path / "d.whl"
    with zipfile.ZipFile(dirty, "w") as z:
        z.writestr("m.py", b"\n" + WORD + b"\n")
    assert cuf.main(["--wheel", str(dirty)]) == 1
    assert "d.whl!m.py:2" in capsys.readouterr().out


def test_tree_files_skips_plans_and_build_output(tmp_path, monkeypatch):
    for rel in (
        "src/a.py",
        "src/__pycache__/a.pyc",
        "rust/target/x",
        "src/k.so",
        "docs/plans/p.md",
        "docs/guide.md",
        "README.md",
        "CHANGELOG.md",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    monkeypatch.setattr(cuf, "ROOT", tmp_path)
    got = sorted(p.relative_to(tmp_path).as_posix() for p in cuf.tree_files())
    assert got == ["README.md", "docs/guide.md", "src/a.py"]


def test_repository_is_clean():
    assert cuf.main([]) == 0


def test_missing_archive_is_a_clear_usage_error(tmp_path, capsys):
    # an unmatched `dist/*.whl` glob reaches the script as a literal path
    missing = tmp_path / "dist" / "*.whl"
    try:
        cuf.main(["--wheel", str(missing)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected a usage error")
    assert "no such archive" in capsys.readouterr().err


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


def test_wheel_and_sdist_findings_name_the_member_and_line(tree, tmp_path, capsys):
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

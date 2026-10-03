"""scripts/check_user_facing.py (P1-14, D-13): scanning text, wheels and sdists."""

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_user_facing", ROOT / "scripts" / "check_user_facing.py"
)
assert _spec and _spec.loader
cuf = importlib.util.module_from_spec(_spec)
sys.modules["check_user_facing"] = cuf
_spec.loader.exec_module(cuf)

WORD = b"spin" + b"dle"  # split so this file does not name it


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

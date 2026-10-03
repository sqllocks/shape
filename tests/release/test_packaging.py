"""Packaging of the core distribution and the first-party plugins: what each archive holds,
its licences and notices, its metadata, and version lockstep."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tarfile
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

# What the core sdist may hold: the build inputs and the files a user of the archive needs.
SDIST_TOP_LEVEL = {
    "PKG-INFO",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "src",
    "rust",
}


def _maturin() -> list[str]:
    exe = shutil.which("maturin")
    if exe:
        return [exe]
    if importlib.util.find_spec("maturin") is not None:
        return [sys.executable, "-m", "maturin"]
    pytest.skip("maturin is not installed (it is in the [dev] extra)")


@pytest.fixture(scope="module")
def core_sdist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("sdist")
    run = subprocess.run(
        [*_maturin(), "sdist", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    (sdist,) = out.glob("sqllocks_shape-*.tar.gz")
    return sdist


def _members(sdist: Path) -> list[str]:
    with tarfile.open(sdist) as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
    prefix = names[0].split("/", 1)[0] + "/"
    assert all(n.startswith(prefix) for n in names)
    return [n[len(prefix) :] for n in names]


def test_core_sdist_holds_only_build_inputs_and_user_files(core_sdist: Path) -> None:
    members = _members(core_sdist)
    extra = sorted({m.split("/", 1)[0] for m in members} - SDIST_TOP_LEVEL)
    assert extra == [], f"the sdist ships dev files: {extra}"
    assert not [m for m in members if m.startswith("src/") and not m.startswith("src/shape/")]
    assert not [m for m in members if m.startswith("rust/") and "/target/" in m]


def test_core_sdist_has_what_a_build_needs(core_sdist: Path) -> None:
    members = set(_members(core_sdist))
    for required in (
        "pyproject.toml",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "README.md",
        "src/shape/__init__.py",
        "src/shape/py.typed",
        "src/shape/schemas/shape-v2.schema.json",
        "rust/shape-kernel/Cargo.toml",
        "rust/shape-kernel/Cargo.lock",
        "rust/shape-kernel/src/lib.rs",
    ):
        assert required in members, required


def test_core_sdist_passes_the_user_facing_check(core_sdist: Path) -> None:
    hits = [h for label, data in cuf.archive_members(core_sdist) for h in cuf.hits(label, data)]
    assert hits == [], f"{len(hits)} user-facing hits, first: {hits[:5]}"

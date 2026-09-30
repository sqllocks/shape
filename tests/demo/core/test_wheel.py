"""The pure wheel: contents, tag, metadata; and `import shape` without cryptography."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "build_pure_wheel.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_pure_wheel", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_pure_wheel"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    builder = _load_builder()
    return builder.build(tmp_path_factory.mktemp("dist"))


def test_wheel_passes_the_script_checks(wheel: Path):
    builder = _load_builder()
    assert builder.check(wheel) == []
    assert wheel.name.endswith("-py3-none-any.whl")
    assert wheel.name.startswith("sqllocks_shape-")
    assert wheel.stat().st_size < 28_600_000


def test_wheel_has_no_compiled_code(wheel: Path):
    with zipfile.ZipFile(wheel) as z:
        suffixes = {Path(n).suffix for n in z.namelist()}
    assert not suffixes & {".so", ".pyd", ".dylib", ".dll", ".c", ".rs", ".pyc"}
    assert ".py" in suffixes


def test_wheel_metadata_declares_the_demo_dependencies(wheel: Path):
    with zipfile.ZipFile(wheel) as z:
        meta_name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        header = z.read(meta_name).decode().split("\n\n", 1)[0]
        wheel_name = next(n for n in z.namelist() if n.endswith(".dist-info/WHEEL"))
        wheel_meta = z.read(wheel_name).decode()
    assert "Name: sqllocks-shape" in header
    assert "Requires-Dist: numpy>=2.0,<3" in header
    assert "Requires-Dist: pyarrow>=14" in header
    assert "cryptography" not in header and "pydantic" not in header
    assert "Tag: py3-none-any" in wheel_meta and "Root-Is-Purelib: true" in wheel_meta


def test_wheel_contains_the_public_modules(wheel: Path):
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
    for required in (
        "shape/__init__.py",
        "shape/api.py",
        "shape/profile/reference/profile.py",
        "shape/contracts/v1.py",
        "shape/report/html.py",
        "shape/cli/main.py",
    ):
        assert required in names


def test_import_shape_does_not_need_cryptography():
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'cryptography' or name.startswith('cryptography.'):\n"
        "            raise ImportError('blocked: ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import shape\n"
        "assert callable(shape.profile) and callable(shape.check) and callable(shape.diff)\n"
        "assert not any(m.startswith('cryptography') for m in sys.modules)\n"
        "assert 'shape.security.crypto' not in sys.modules\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_crypto_names_resolve_lazily_and_fail_clearly_without_cryptography():
    import shape.security as sec

    if importlib.util.find_spec("cryptography") is None:
        with pytest.raises(ModuleNotFoundError, match="cryptography"):
            sec.sign_ed25519  # noqa: B018
    else:
        assert sec.EncryptedPayload and sec.sign_ed25519
    with pytest.raises(AttributeError):
        sec.no_such_name  # noqa: B018

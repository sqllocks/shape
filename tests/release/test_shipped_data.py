"""Every reference data file a feature reads ships in the wheel; nothing downloads at run time."""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_shipped_data", ROOT / "scripts" / "check_shipped_data.py"
)
assert _spec and _spec.loader
csd = importlib.util.module_from_spec(_spec)
sys.modules["check_shipped_data"] = csd
_spec.loader.exec_module(csd)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / "src" / "shape" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
    return tmp_path


def test_the_repository_passes():
    assert csd.check_tree(ROOT) == []


def test_inventory_lists_data_files_only(tmp_path):
    root = _tree(tmp_path, {"a.py": "", "d/x.txt": "x", "d/y.json": "{}", "s.pyi": ""})
    assert sorted(p.name for p in csd.data_files(root)) == ["x.txt", "y.json"]


def test_reference_to_a_missing_data_file_is_reported(tmp_path):
    code = 'from pathlib import Path\nP = Path(__file__).with_name("gone.json")\n'
    root = _tree(tmp_path, {"m.py": code})
    problems = csd.check_tree(root)
    assert any("gone.json" in p and "m.py" in p for p in problems)


def test_reference_to_a_shipped_data_file_passes(tmp_path):
    code = 'from pathlib import Path\nP = Path(__file__).with_name("here.json")\n'
    root = _tree(tmp_path, {"m.py": code, "here.json": "{}"})
    assert csd.check_tree(root) == []


def test_resources_reference_is_checked(tmp_path):
    code = (
        "from importlib import resources\n"
        'T = resources.files("shape").joinpath("pools/names.txt").read_text()\n'
    )
    assert any("names.txt" in p for p in csd.check_tree(_tree(tmp_path, {"m.py": code})))
    ok = _tree(tmp_path, {"m.py": code, "pools/names.txt": "a"})
    assert csd.check_tree(ok) == []


def test_dynamic_name_is_covered_by_the_inventory_only(tmp_path):
    code = (
        "from importlib import resources\n"
        "def f(name):\n"
        '    return resources.files("shape").joinpath("pools", f"{name}.txt")\n'
    )
    assert csd.check_tree(_tree(tmp_path, {"m.py": code, "pools/a.txt": "a"})) == []


def test_modules_that_load_no_package_data_are_not_scanned(tmp_path):
    root = _tree(tmp_path, {"m.py": 'NAME = "profile.json"\n'})
    assert csd.check_tree(root) == []


def test_runtime_download_is_reported(tmp_path):
    for code in (
        "import urllib.request\nurllib.request.urlopen('http://x')\n",
        "from urllib.request import urlretrieve\n",
        "import requests\n",
        "import httpx\n",
        "import http.client\n",
    ):
        root = _tree(tmp_path, {"m.py": code})
        assert any("m.py" in p and "network" in p for p in csd.check_tree(root)), code


def test_urllib_parse_is_not_a_download(tmp_path):
    root = _tree(tmp_path, {"m.py": "from urllib.parse import urlparse\n"})
    assert csd.check_tree(root) == []


def test_explicit_fetch_modules_are_allowed(tmp_path):
    code = "import urllib.request\n"
    root = _tree(tmp_path, {"scale/http.py": code})
    assert csd.check_tree(root) == []


def test_wheel_must_contain_every_data_file(tmp_path):
    root = _tree(tmp_path, {"a.py": "", "d/x.txt": "x", "d/y.json": "{}"})
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("shape/a.py", "")
        z.writestr("shape/d/x.txt", "x")
    problems = csd.check_wheel(root, wheel)
    assert problems == ["shape/d/y.json: reference data file is not in the wheel"]
    with zipfile.ZipFile(wheel, "a") as z:
        z.writestr("shape/d/y.json", "{}")
    assert csd.check_wheel(root, wheel) == []


def test_git_ignored_data_file_is_reported(tmp_path):
    root = _tree(tmp_path, {"d/x.key": "x"})
    (root / ".gitignore").write_text("*.key\n")
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    assert any("ignored" in p for p in csd.check_tree(root))


@pytest.mark.parametrize(
    "code",
    [
        "from urllib import request\n",
        "from http import client\n",
        "from urllib import parse, request as r\n",
        "import urllib3\n",
        "from urllib3 import PoolManager\n",
        "import socket\n",
    ],
)
def test_every_import_form_of_a_network_client_is_reported(tmp_path, code):
    """#261: the `from PACKAGE import MODULE` form names the same network client."""
    root = _tree(tmp_path, {"m.py": code})
    assert any("m.py" in p and "network" in p for p in csd.check_tree(root)), code


def test_from_urllib_import_parse_is_not_a_download(tmp_path):
    root = _tree(tmp_path, {"m.py": "from urllib import parse\nfrom http import HTTPStatus\n"})
    assert csd.check_tree(root) == []

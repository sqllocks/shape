"""Execute editable-install examples without allowing a shared native rebuild."""

from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("INSTALL", "SIGNING", "PROJECT", "TESTING_WITH_SHAPE", "DBT")
NATIVE = Path("src/shape/_kernel.abi3.so")


@pytest.mark.parametrize("page", PAGES)
def test_editable_example_rebuilds_only_its_private_checkout(page, tmp_path):
    shared = tmp_path / "shared-checkout"
    private = tmp_path / "page-checkout"
    for root in (shared, private):
        (root / NATIVE).parent.mkdir(parents=True)
        (root / NATIVE).write_bytes(b"loaded-native-library")
        (root / "pyproject.toml").write_text("[project]\nname='example'\n")
        for plugin in ("shape-domains", "shape-dbt"):
            target = root / "plugins" / plugin
            target.mkdir(parents=True)
            (target / "pyproject.toml").write_text("[project]\nname='plugin'\n")
    # The stub performs the installer's relevant filesystem effect: rebuilding the
    # extension in each editable core target. No actual pip or compiler is run.
    binary = private / ".venv/bin"
    binary.mkdir(parents=True)
    installer = binary / "pip"
    installer.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['-m', 'pip']: args = args[2:]\n"
        "assert args[0] == 'install', args\n"
        "targets = [Path(args[i+1]).resolve() for i,a in enumerate(args) if a == '-e']\n"
        "assert targets\n"
        "for target in targets:\n"
        "    assert (target / 'pyproject.toml').is_file(), target\n"
        "    native = target / 'src/shape/_kernel.abi3.so'\n"
        "    if native.exists(): native.write_bytes(b'rebuilt-native-library')\n"
        "metadata = Path(sys.argv[0]).resolve().parent.parent / 'editable-metadata.json'\n"
        "metadata.write_text(json.dumps([str(p) for p in targets]))\n"
        "assert os.environ['VIRTUAL_ENV'] == str(metadata.parent)\n"
        "Path(os.environ['INSTALL_TARGETS']).write_text(json.dumps([str(p) for p in targets]))\n"
    )
    installer.chmod(0o755)
    (binary / "python").symlink_to(installer)
    shared_binary = shared / ".venv/bin"
    shared_binary.mkdir(parents=True)
    shared_installer = shared_binary / "pip"
    shared_installer.write_text(installer.read_text())
    shared_installer.chmod(0o755)
    (shared_binary / "python").symlink_to(shared_installer)
    shared_metadata = shared / ".venv/editable-metadata.json"
    shared_metadata.write_text("global-registration")
    source = (ROOT / "docs" / f"{page}.md").read_text()
    blocks = re.findall(r"```bash \{\.runnable-reference\}\n(.*?)\n```", source, re.S)
    command = next(
        line
        for block in blocks
        for line in block.splitlines()
        if line.startswith(("pip install ", "python -m pip install "))
        and "--no-build-isolation" in line
        and ('-e "$SHAPE_DOCS_REPO"' in line or "-e ." in line)
    )
    log = tmp_path / "targets.json"
    env = {
        **os.environ,
        "SHAPE_DOCS_REPO": str(shared),
        "INSTALL_TARGETS": str(log),
        "PATH": str(shared_binary) + os.pathsep + os.environ["PATH"],
        "VIRTUAL_ENV": str(shared / ".venv"),
    }
    activate = runpy.run_path(str(ROOT / "tests/docs/test_reference_examples.py"))[
        "_activate_page_environment"
    ]
    activate(env, private)
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", command], cwd=private, env=env, check=True
    )
    assert (shared / NATIVE).read_bytes() == b"loaded-native-library"
    assert (private / NATIVE).read_bytes() == b"rebuilt-native-library"
    targets = [Path(p) for p in json.loads(log.read_text())]
    plugins = {"TESTING_WITH_SHAPE": "shape-domains", "DBT": "shape-dbt"}
    expected = [private]
    if page in plugins:
        expected.append(private / "plugins" / plugins[page])
    assert targets == expected
    assert shared_metadata.read_text() == "global-registration"
    assert json.loads((private / ".venv/editable-metadata.json").read_text()) == [
        str(target) for target in expected
    ]
    assert env["SHAPE_DOCS_REPO"] == str(shared)

"""DEMO-LIVE F-2: RUNBOOK section 2's local dry run installs what the tests need.

The section used to give a hand-picked ``pip install`` list without ``fsspec`` (which the ADF gate
script imports) and without the dbt plugin, so ``pytest tests/demo/fabric`` failed in a clean
venv built as it said (28 failed and 4 errors in ``test_adf.py`` and ``test_generate_adf.py``).
It now gives CI's ``fabric-demo`` install command, and this test keeps the two the same.
"""

from __future__ import annotations

import re

from fabric_helpers import REPO

RUNBOOK = REPO / "integrations" / "fabric" / "RUNBOOK.md"
CI = REPO / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = REPO / "tests" / "demo" / "fabric" / "requirements.txt"


def _section_2() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.index("## 2. ")
    return text[start : text.index("\n## 3. ", start)]


def _ci_fabric_demo_install() -> str:
    text = CI.read_text(encoding="utf-8")
    job = text[text.index("  fabric-demo:") :]
    return re.search(r"- run: (pip install -e .*requirements\.txt)", job).group(1)


def test_section_2_installs_exactly_what_ci_installs():
    assert _ci_fabric_demo_install() in _section_2()


def test_section_2_has_no_hand_picked_package_list():
    for line in _section_2().splitlines():
        if line.strip().startswith("pip install"):
            assert "-r tests/demo/fabric/requirements.txt" in line, line


def test_the_requirements_name_what_the_adf_gate_script_imports():
    packages = {
        re.split(r"[<>=\[ ]", ln.strip())[0].lower()
        for ln in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith(("#", "-"))
    }
    assert {"fsspec", "jsonschema", "fabric-user-data-functions", "deltalake"} <= packages


def test_section_2_names_the_system_packages():
    section = _section_2()
    assert "unixodbc" in section and "Java 17" in section

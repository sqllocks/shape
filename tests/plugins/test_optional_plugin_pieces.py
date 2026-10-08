"""#309 and #310: what a clean install from the release wheels needs, and says when it is missing.

* #309: learned schemas use ``faker`` strategies, so the inference path needs ``faker``. It is
  the core's ``[faker]`` extra, the domains plugin (which the demo scenarios need) depends on it,
  and the error without it names the extra (the healthcare demo run without ``faker`` is in
  ``plugins/shape-domains/tests/test_healthcare_without_faker.py``, where the plugin is installed).
* #310: the Event Hubs and SQL Server plugins are the ``[eventhubs]`` and ``[sqlserver]`` extras
  of the fabric plugin, not hard requirements. Without them the plugin still loads (the Lakehouse
  target, the notebooks, the commands), and the pieces that need them say what to install
  (run in ``plugins/shape-fabric/tests/test_optional_extras.py``, where the plugin is installed).

"Clean install" is checked two ways: the wheels are built and their metadata read (what pip
resolves from), and the plugin is run in a subprocess where the optional packages cannot be
imported, as in a venv that does not have them (in the plugins' own suites, named above).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
VERSION = CORE["version"]


def _plugin_project(name: str) -> dict:
    path = ROOT / "plugins" / name / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def _pip_args() -> list[str]:
    # as tests/plugins/test_plugin_kit_install.py: offline, build with the installed setuptools
    if importlib.util.find_spec("setuptools") is None:
        return []
    major = importlib.metadata.version("setuptools").split(".")[0]
    return ["--no-build-isolation"] if major.isdigit() and int(major) >= 77 else []


def _wheel_requires(plugin: str, tmp_path: Path) -> list[str]:
    """``Requires-Dist`` of the wheel ``pip wheel`` builds for ``plugins/<plugin>`` (from a copy,
    so no build/ folder is left in the repository)."""
    src = tmp_path / plugin
    shutil.copytree(
        ROOT / "plugins" / plugin,
        src,
        ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__", "tests"),
    )
    out = tmp_path / "wheels"
    done = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(out)]
        + _pip_args()
        + [str(src)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    (wheel,) = out.glob("*.whl")
    with zipfile.ZipFile(wheel) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        meta = Parser().parsestr(zf.read(name).decode("utf-8"))
    return [" ".join(r.split()) for r in meta.get_all("Requires-Dist") or []]


# ---- #309: faker ---------------------------------------------------------------------------


def test_core_has_a_faker_extra_and_all_includes_it() -> None:
    extras = CORE["optional-dependencies"]
    assert extras["faker"] == ["faker>=24"]
    assert "faker>=24" in extras["all"]


def test_the_domains_plugin_depends_on_faker() -> None:
    deps = _plugin_project("shape-domains")["dependencies"]
    assert CORE["optional-dependencies"]["faker"][0] in deps


def test_the_built_domains_wheel_requires_faker(tmp_path: Path) -> None:
    requires = _wheel_requires("shape-domains", tmp_path)
    assert "faker>=24" in requires, requires


def test_the_faker_error_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    from shape.builtins.strategies import providers

    monkeypatch.setitem(sys.modules, "faker", None)  # import faker raises ImportError
    providers._faker_pool.cache_clear()
    with pytest.raises(ImportError) as info:
        providers._faker_pool("en_US", "state", "{}", 1, 4)
    assert "pip install 'sqllocks-shape[faker]'" in str(info.value)


# ---- #310: the fabric plugin's optional pieces -----------------------------------------------


def test_fabric_plugin_needs_only_core_and_offers_the_two_plugins_as_extras() -> None:
    proj = _plugin_project("shape-fabric")
    assert proj["dependencies"] == [f"sqllocks-shape=={VERSION}"]
    extras = proj["optional-dependencies"]
    assert extras["eventhubs"] == [f"sqllocks-shape-eventhubs=={VERSION}"]
    assert extras["sqlserver"] == [f"sqllocks-shape-sqlserver=={VERSION}"]


def test_the_built_fabric_wheel_requires_the_two_plugins_only_as_extras(tmp_path: Path) -> None:
    requires = _wheel_requires("shape-fabric", tmp_path)
    assert f"sqllocks-shape=={VERSION}" in requires
    hubs = [r for r in requires if r.startswith("sqllocks-shape-eventhubs")]
    sql = [r for r in requires if r.startswith("sqllocks-shape-sqlserver")]
    assert hubs == [f'sqllocks-shape-eventhubs=={VERSION}; extra == "eventhubs"'], requires
    assert sql == [f'sqllocks-shape-sqlserver=={VERSION}; extra == "sqlserver"'], requires


def test_the_pure_wheel_provides_the_faker_extra() -> None:
    """The demo installs the core from the pure wheel: ``sqllocks-shape[faker]`` must resolve."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_pure_wheel
    finally:
        sys.path.pop(0)
    meta = build_pure_wheel._metadata(CORE, VERSION)
    assert "Provides-Extra: faker" in meta
    assert "Requires-Dist: faker>=24; extra == 'faker'" in meta


def test_the_demo_notebook_installs_the_fabric_plugin_with_both_extras() -> None:
    """The notebook's targets include the SQL database and Warehouse: with the extras optional,
    its ``%pip`` line must install the plugins they name (Event Hubs and SQL Server) too. Every
    wheel goes by file path (DEMO-LIVE F-4): PyPI's older 0.9.0 must never win."""
    from shape.demo.notebook import install_line

    line = install_line(VERSION)
    pip = next(ln for ln in line.splitlines() if ln.startswith("%pip install"))
    for dist in ("shape", "shape_domains", "shape_fabric", "shape_eventhubs", "shape_sqlserver"):
        assert f" builtin/sqllocks_{dist}-{VERSION}-py3-none-any.whl" in pip
    assert "==" not in pip and "--find-links" not in pip

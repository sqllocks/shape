"""P2-04: built-ins are plugins: same entry points, same host, import contract enforced."""

import json
import shutil
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from shape.builtins.catalog import BUILTINS
from shape.plugins import registry
from shape.plugins.api import v1
from shape.plugins.host import PluginHost, default_host, reset_default_host

ROOT = Path(__file__).resolve().parents[2]


def _fresh_host() -> PluginHost:
    host = PluginHost(entry_points=lambda: [])  # no installed metadata: the fallback route
    registry.register_builtins(host)
    return host


def test_catalog_matches_pyproject_entry_points():
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    table = declared["project"]["entry-points"]
    # `pytest11` is pytest's own entry-point group (W5-05, the Shape pytest plugin), not a Shape one
    from_pyproject = sorted(
        (g, n, t) for g, items in table.items() if g != "pytest11" for n, t in items.items()
    )
    assert from_pyproject == sorted(BUILTINS)
    assert table["pytest11"] == {"shape": "shape.testdata.pytest_plugin"}
    assert {g for g, _, _ in BUILTINS} <= set(v1.GROUPS)


def test_installed_metadata_carries_every_builtin():
    """Needs ``pip install -e .`` after pyproject changes, like any entry-point change."""
    installed = {
        (ep.group, ep.name, ep.value)
        for g in v1.GROUPS
        for ep in metadata.entry_points(group=g)
        if ep.value.startswith("shape.builtins.")
    }
    assert installed == set(BUILTINS)


def test_every_builtin_is_registered_and_loads_as_its_protocol():
    host = _fresh_host()
    records = host.records()
    assert len(records) == len(BUILTINS)
    for rec in host.load_all():
        assert rec.status == "ok", (rec.group, rec.name, rec.error)
        assert rec.api == v1.SHAPE_API
        proto = v1.PROTOCOLS[v1.GROUPS[rec.group]]
        assert isinstance(rec.obj, proto)
        assert rec.obj.name == rec.name


def test_builtins_are_found_through_entry_points_when_installed():
    host = PluginHost()  # real discovery only, no fallback
    assert registry.register_builtins(host) == 0
    assert {(r.group, r.name) for r in host.records()} >= {(g, n) for g, n, _ in BUILTINS}
    assert host.record("shape.sources", "csv").source == "sqllocks-shape"


def test_default_host_lists_builtins_and_core_never_imports_them_until_used():
    reset_default_host()
    try:
        host = default_host()
        listed = {(r.group, r.name) for r in host.records()}
        assert {(g, n) for g, n, _ in BUILTINS} <= listed
        assert all(r.status == "unloaded" for r in host.records())
    finally:
        reset_default_host()


def test_a_third_party_plugin_cannot_shadow_a_builtin_silently():
    host = _fresh_host()
    with pytest.raises(ValueError, match="already registered"):
        host.register("shape.sources", "csv", object())


def test_plugins_doctor_reports_every_builtin_ok():
    out = subprocess.run(
        [sys.executable, "-m", "shape.cli.main", "plugins", "doctor", "--json"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    report = json.loads(out.stdout)
    names = {(p["group"], p["name"]) for p in report["plugins"] if p["status"] == "ok"}
    assert {(g, n) for g, n, _ in BUILTINS} <= names


def test_removed_connector_registry_and_file_connectors():
    import shape.connectors

    for gone in ("ConnectorRegistry", "JSONLSource", "JSONLSink"):
        assert not hasattr(shape.connectors, gone)
    for mod in ("shape.connectors.registry", "shape.connectors.files", "shape.packs.address"):
        with pytest.raises(ModuleNotFoundError):
            __import__(mod)


# -- the import-linter contract ---------------------------------------------------------------

LINT = shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports")


def _lint(cwd: Path, config: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [LINT] + (["--config", str(config)] if config else [])
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _ini(items):
    return "".join(f"\n    {i}" for i in items)


def test_import_contract_passes_on_the_tree():
    out = _lint(ROOT)
    assert out.returncode == 0, out.stdout + out.stderr


def test_contract_names_every_top_level_module_but_builtins():
    contract = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "importlinter"
    ]["contracts"][0]
    top = {
        f"shape.{p.stem if p.suffix == '.py' else p.name}"
        for p in (ROOT / "src" / "shape").iterdir()
        if (p.is_dir() and (p / "__init__.py").exists()) or p.suffix == ".py"
    } - {"shape.__init__", "shape.builtins"}
    assert set(contract["source_modules"]) == top


def test_import_contract_catches_a_stray_import_of_shape_builtins(tmp_path):
    """The same contract, on a toy package, fails for any importer but the registry."""
    pkg = tmp_path / "shape"
    for sub in ("builtins", "plugins"):
        (pkg / sub).mkdir(parents=True)
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "builtins" / "catalog.py").write_text("BUILTINS = ()\n")
    (pkg / "builtins" / "csv.py").write_text("from shape.builtins import catalog\n")
    (pkg / "plugins" / "registry.py").write_text("from shape.builtins.catalog import BUILTINS\n")
    (pkg / "cli.py").write_text("")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contract = pyproject["tool"]["importlinter"]["contracts"][0]
    cfg = tmp_path / ".importlinter"
    cfg.write_text(
        "[importlinter]\nroot_package = shape\n\n[importlinter:contract:b]\n"
        f"name = {contract['name']}\ntype = forbidden\n"
        f"source_modules = {_ini(['shape.cli', 'shape.plugins'])}\n"
        f"forbidden_modules = {_ini(contract['forbidden_modules'])}\n"
        "allow_indirect_imports = true\n"
        f"ignore_imports = {_ini(contract['ignore_imports'])}\n"
    )
    env_cwd = tmp_path
    ok = subprocess.run(
        [LINT, "--config", str(cfg)],
        cwd=env_cwd,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(tmp_path), "PATH": ""},
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (pkg / "cli.py").write_text("from shape.builtins import csv\n")
    bad = subprocess.run(
        [LINT, "--config", str(cfg)],
        cwd=env_cwd,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(tmp_path), "PATH": ""},
    )
    assert bad.returncode != 0
    assert "shape.cli" in bad.stdout


def test_docs_page_lists_every_builtin():
    page = (ROOT / "docs" / "plugins" / "builtins.md").read_text(encoding="utf-8")
    for group, name, _ in BUILTINS:
        assert f"| `{group}` | `{name}` |" in page

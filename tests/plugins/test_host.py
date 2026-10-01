"""P2-02: the plugin host, with real entry-point metadata on a temporary sys.path."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shape.plugins import doctor
from shape.plugins.api import v1
from shape.plugins.host import PluginHost, PluginLoadError, api_major, check_api

GOOD = """
SHAPE_API = "1.0"

class Det:
    name = "good"
    def detect(self, values, column):
        return None
"""

PLUGINS = {
    "good_plugin": GOOD,
    "broken_import": 'SHAPE_API = "1.0"\nraise RuntimeError("boom at import")\n',
    "future_api": GOOD.replace('"1.0"', '"2.0"'),
    "no_api": GOOD.replace('SHAPE_API = "1.0"', ""),
    "wrong_shape": 'SHAPE_API = "1.3"\nclass Det:\n    name = "x"\n',
    "bad_factory": 'SHAPE_API = "1.0"\ndef Det():\n    raise ValueError("cannot build")\n',
    "exits": 'SHAPE_API = "1.0"\nimport sys\nsys.exit(3)\n',
}


def _install(root: Path, dist: str, module: str, group: str, name: str, target: str) -> None:
    (root / f"{module}.py").write_text(PLUGINS[module], encoding="utf-8")
    info = root / f"{dist.replace('-', '_')}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text(f"[{group}]\n{name} = {module}:{target}\n")


@pytest.fixture
def site(tmp_path, monkeypatch):
    for mod in PLUGINS:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _third_party() -> PluginHost:
    """Installed-package discovery without core's own built-ins (P2-04), so these tests see
    only the plugins they install."""
    from importlib import metadata

    def eps():
        return [
            ep
            for g in v1.GROUPS
            for ep in metadata.entry_points(group=g)
            if not ep.value.startswith("shape.builtins.")
        ]

    return PluginHost(entry_points=eps)


def _all_plugins(site: Path) -> None:
    for mod in PLUGINS:
        _install(site, f"dist-{mod.replace('_', '-')}", mod, "shape.detectors", mod, "Det")


def test_good_plugin_loads_lazily(site):
    _install(site, "dist-good", "good_plugin", "shape.detectors", "good", "Det")
    host = _third_party()
    (rec,) = host.records("shape.detectors")
    assert (
        rec.status == "unloaded" and "good_plugin" not in sys.modules
    )  # discovery imports nothing
    obj = host.get("shape.detectors", "good")
    assert isinstance(obj, v1.SemanticDetector)
    assert rec.status == "ok" and rec.api == "1.0" and rec.source == "dist-good"
    assert host.get("shape.detectors", "good") is obj  # cached


def test_every_failure_mode_is_isolated(site):
    _all_plugins(site)
    host = _third_party()
    recs = {r.name: r for r in host.load_all()}
    assert recs["good_plugin"].status == "ok"
    expected = {
        "broken_import": "RuntimeError: boom at import",
        "future_api": "major versions must match",
        "no_api": "does not declare SHAPE_API",
        "wrong_shape": "does not implement SemanticDetector",
        "bad_factory": "ValueError: cannot build",
        "exits": "SystemExit",
    }
    for name, text in expected.items():
        assert recs[name].status == "error", name
        assert text in (recs[name].error or ""), (name, recs[name].error)
    assert host.get("shape.detectors", "good_plugin") is not None  # others still work


def test_get_raises_for_one_plugin_only(site):
    _all_plugins(site)
    host = _third_party()
    with pytest.raises(PluginLoadError, match="boom at import"):
        host.get("shape.detectors", "broken_import")
    assert host.try_get("shape.detectors", "broken_import") is None
    assert host.try_get("shape.detectors", "missing") is None
    with pytest.raises(KeyError):
        host.get("shape.detectors", "missing")
    # A failure is remembered, not retried, until reload().
    assert host.record("shape.detectors", "broken_import").status == "error"


def test_duplicate_names_report_the_loser(site):
    _install(site, "dist-a", "good_plugin", "shape.detectors", "same", "Det")
    other = site / "other_plugin.py"
    other.write_text(GOOD)
    info = site / "dist_b-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: dist-b\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text("[shape.detectors]\nsame = other_plugin:Det\n")
    host = _third_party()
    recs = host.records("shape.detectors")
    assert [r.status for r in recs].count("error") == 1
    assert host.names("shape.detectors") == ["same"]
    assert host.get("shape.detectors", "same").name == "good"


def test_unknown_groups_are_ignored(site):
    _install(site, "dist-x", "good_plugin", "shape.not_a_group", "x", "Det")
    assert _third_party().records() == []


def test_register_and_protocol_check():
    class Det:
        name = "mine"

        def detect(self, values, column):
            return None

    host = PluginHost(entry_points=lambda: [])
    host.register("shape.detectors", "mine", Det)
    host.register("shape.detectors", "inst", Det())
    host.register("shape.detectors", "old", Det, api="2.0")
    host.register("shape.detectors", "bad", object)
    assert host.get("shape.detectors", "mine").name == "mine"
    assert host.get("shape.detectors", "inst").name == "mine"
    assert host.try_get("shape.detectors", "old") is None
    assert host.try_get("shape.detectors", "bad") is None
    with pytest.raises(ValueError):
        host.register("shape.detectors", "mine", Det)
    with pytest.raises(ValueError):
        host.register("shape.nope", "x", Det)
    assert host.names("shape.detectors") == ["bad", "inst", "mine", "old"]


def test_broken_metadata_is_isolated():
    def boom():
        raise OSError("unreadable site-packages")

    host = PluginHost(entry_points=boom)
    (rec,) = host.records()
    assert rec.status == "error" and "unreadable" in (rec.error or "")
    assert doctor.diagnose(host)["ok"] is False


def test_reload_rediscovers(site):
    host = _third_party()
    assert host.records() == []
    _install(site, "dist-good", "good_plugin", "shape.detectors", "good", "Det")
    assert host.records() == []  # discovery is cached
    host.reload()
    assert host.names("shape.detectors") == ["good"]


def test_api_version_helpers():
    assert api_major("1.7") == 1
    assert check_api("1.9") == "1.9"
    for bad in ("2.0", "x", "", None, 1.0):
        with pytest.raises(PluginLoadError):
            check_api(bad)


def test_doctor_report(site):
    _all_plugins(site)
    report = doctor.diagnose(_third_party())
    assert report["ok"] is False and report["api"] == "1.0"
    json.dumps(report)  # JSON-safe: no live objects
    text = doctor.format_report(report)
    assert "error shape.detectors:broken_import" in text and "boom at import" in text
    assert "ok    shape.detectors:good_plugin" in text


def _shape(site: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(site))
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_broken_plugin_does_not_affect_profile_and_doctor_reports_it(site, tmp_path):
    _all_plugins(site)
    csv = tmp_path / "t.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,x\n", encoding="utf-8")
    out = tmp_path / "t.shape"
    r = _shape(site, "profile", str(csv), "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.exists() and "boom" not in r.stdout + r.stderr

    r = _shape(site, "plugins", "doctor")
    assert r.returncode == 1
    assert "broken_import" in r.stdout and "boom at import" in r.stdout
    r = _shape(site, "plugins", "doctor", "--json")
    assert r.returncode == 1
    report = json.loads(r.stdout)
    by_name = {p["name"]: p for p in report["plugins"]}
    assert by_name["broken_import"]["status"] == "error"
    assert by_name["good_plugin"]["status"] == "ok"


def test_doctor_is_clean_without_plugins(tmp_path):
    r = _shape(tmp_path, "plugins", "doctor")
    assert r.returncode == 0 and "all plugins load" in r.stdout

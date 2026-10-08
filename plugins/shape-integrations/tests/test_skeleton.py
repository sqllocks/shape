"""Item 1: the skeleton, the extras, and the missing-extra error."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import shape_integrations
from shape_integrations.extras import EXTRAS, MissingExtraError, require

from shape.plugins import kit

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = (
    "openlineage",
    "mlflow",
    "presidio_analyzer",
    "sdmetrics",
    "anonymeter",
    "ibis",
    "duckdb",
)


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_integrations) == "1.0"


def test_version_equals_core_and_pins_it():
    core = tomllib.loads((ROOT.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    version = core["project"]["version"]
    proj = _project()
    assert proj["name"] == "sqllocks-shape-integrations"
    assert proj["version"] == version
    assert f"sqllocks-shape=={version}" in proj["dependencies"]
    assert len(proj["dependencies"]) == 1  # every third-party library is an extra


def test_one_extra_per_integration_each_pins_a_major_range():
    extras = _project()["optional-dependencies"]
    assert (
        set(extras)
        == set(EXTRAS)
        == {
            "openlineage",
            "mlflow",
            "presidio",
            "sdmetrics",
            "anonymeter",
            "ibis",
        }
    )
    for name, reqs in extras.items():
        assert reqs, name
        for req in reqs:
            assert "<" in req and ">=" in req, f"{name}: {req} must pin a version range"


def test_entry_points_cover_commands_detector_and_source():
    eps = _project()["entry-points"]
    assert set(eps["shape.commands"]) == {"lineage", "mlflow", "evaluate"}
    assert set(eps["shape.detectors"]) == {"presidio"}
    assert set(eps["shape.sources"]) == {"duckdb"}


def test_importing_the_package_imports_no_third_party_library():
    code = (
        "import sys, shape_integrations\n"
        "import shape_integrations.extras, shape_integrations.manifest_view\n"
        f"bad = [m for m in {THIRD_PARTY!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_missing_extra_message_names_the_extra_and_the_pip_command():
    with pytest.raises(MissingExtraError) as info:
        require("shape_integrations_no_such_library", "mlflow", name="MLflow")
    assert str(info.value) == (
        "MLflow needs the 'mlflow' extra: pip install 'sqllocks-shape-integrations[mlflow]'"
    )
    assert info.value.extra == "mlflow"


def test_a_broken_dependency_inside_an_installed_library_is_not_reported_as_missing(tmp_path):
    (tmp_path / "half_installed.py").write_text("import not_a_real_dependency_xyz\n")
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(ModuleNotFoundError) as info:
            require("half_installed", "mlflow")
        assert not isinstance(info.value, MissingExtraError)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("half_installed", None)


def test_the_installed_distribution_passes_the_conformance_kit_for_every_entry_point():
    lines = kit.check_installed("sqllocks-shape-integrations")
    assert len(lines) == 5
    joined = "\n".join(lines)
    for ref in (
        "shape.commands:lineage",
        "shape.commands:mlflow",
        "shape.commands:evaluate",
        "shape.detectors:presidio",
        "shape.sources:duckdb",
    ):
        assert ref in joined

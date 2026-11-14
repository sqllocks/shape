"""Fabric-native defaults and an offline Git layout preflight."""

import importlib.util
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from shape_fabric.git_layout import check_profile_folder

ROOT = Path(__file__).resolve().parents[3]
FABRIC = ROOT / "integrations/fabric"


def test_environment_pins_match_both_version_sources() -> None:
    libraries = yaml.safe_load((FABRIC / "environment/environment.yml").read_text())[
        "dependencies"
    ][0]["pip"]
    for name, source in (
        ("sqllocks-shape", ROOT),
        ("sqllocks-shape-fabric", ROOT / "plugins/shape-fabric"),
    ):
        version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
        assert f"{name}=={version}" in libraries


def test_default_spark_notebooks_rely_on_environment() -> None:
    for name in ("shape_profile_spark", "shape_profile_distributed"):
        nb = json.loads((FABRIC / f"notebooks/{name}.ipynb").read_text())
        assert nb["metadata"]["kernelspec"]["name"] == "synapse_pyspark"
        assert not any(
            line.lstrip().startswith(("%pip", "!pip"))
            for cell in nb["cells"]
            for line in cell["source"]
        )
    pipeline = json.loads(
        (FABRIC / "pipelines/shape_gate_spark.DataPipeline/pipeline-content.json").read_text()
    )
    params = pipeline["properties"]["activities"][0]["typeProperties"]["parameters"]
    assert "_inlineInstallationEnabled" not in params
    assert "variableLibrary" in params


def test_profiles_are_separate_from_fabric_item_definitions(tmp_path: Path) -> None:
    item = tmp_path / "p3-check.Notebook"
    item.mkdir()
    (item / ".platform").write_text('{"metadata":{"type":"Notebook","displayName":"p3-check"}}')
    (item / "notebook-content.py").write_text("print('p3-check')\n")
    profiles = tmp_path / "shape"
    profiles.mkdir()
    import pyarrow as pa  # type: ignore[import-untyped]

    import shape

    artifact = profiles / "orders.shape"
    shape.save(shape.profile(pa.table({"id": [1, 2]})), artifact)
    original = artifact.read_bytes()
    assert check_profile_folder(tmp_path) == [artifact]
    # An item-only change has no path overlap with the ordinary profiles folder.
    (item / "notebook-content.py").write_text("print('p3-updated')\n")
    assert artifact.read_bytes() == original
    assert check_profile_folder(tmp_path) == [artifact]


@pytest.mark.parametrize("marker", [".platform", "item.metadata.json", "item.config.json"])
def test_profiles_cannot_be_an_item_definition(tmp_path: Path, marker: str) -> None:
    folder = tmp_path / "shape"
    folder.mkdir()
    (folder / "orders.shape").write_bytes(b"artifact")
    (folder / marker).write_text("{}")
    with pytest.raises(ValueError, match="Fabric item"):
        check_profile_folder(tmp_path)


def test_profiles_cannot_be_nested_in_an_item(tmp_path: Path) -> None:
    item = tmp_path / "p3-check.Notebook"
    item.mkdir()
    (item / ".platform").write_text("{}")
    (item / "shape").mkdir()
    with pytest.raises(ValueError, match="beside"):
        check_profile_folder(tmp_path, "p3-check.Notebook/shape")


@pytest.mark.parametrize("mode,passed", [("report", True), ("enforce", False)])
def test_notebook_gate_uses_library_thresholds_and_mode(mode: str, passed: bool) -> None:
    spec = importlib.util.spec_from_file_location(
        "p3_notebooks", FABRIC / "notebooks/build_notebooks.py"
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    from shape_fabric.gate_settings import load_gate_settings

    values = {
        "gateMode": mode,
        "thresholds": '{"mean_shift_std": 0.25}',
        "baselinePath": "shape/prod.shape",
    }
    utils = SimpleNamespace(
        variableLibrary=SimpleNamespace(
            getLibrary=lambda _: SimpleNamespace(getVariable=values.__getitem__)
        )
    )
    settings = load_gate_settings("shape_gate", utils=utils)
    calls: list[Any] = []

    def diff(old: str, new: str, **kwargs: Any) -> SimpleNamespace:
        calls.append((old, new, kwargs))
        return SimpleNamespace(drifted=True, changes=[{"rule": "mean_shift"}])

    cell = next(
        source for kind, source in builder.SPARK_CELLS if source.startswith("violations, changes")
    )
    ns = {
        "contractPath": "",
        "baselinePath": settings.baseline_path,
        "load_baseline": lambda path: path,
        "profile": "current",
        "failOnDrift": True,
        "gate_settings": settings,
        "shape": SimpleNamespace(diff=diff),
    }
    exec(cell, ns)
    assert ns["passed"] is passed
    assert ns["violations"]
    assert calls == [("shape/prod.shape", "current", {"thresholds": {"mean_shift_std": 0.25}})]


def test_python_fallback_installs_the_fabric_plugin_wheel() -> None:
    notebook = json.loads((FABRIC / "notebooks/shape_profile.ipynb").read_text())
    source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
    version = tomllib.loads((ROOT / "plugins/shape-fabric/pyproject.toml").read_text())["project"][
        "version"
    ]
    assert f"%pip install builtin/sqllocks_shape_fabric-{version}-py3-none-any.whl" in source


def test_profiles_folder_itself_cannot_be_a_symlink(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "orders.shape").write_bytes(b"artifact")
    (tmp_path / "shape").symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        check_profile_folder(tmp_path)

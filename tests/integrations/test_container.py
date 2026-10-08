"""PF-05: the Dockerfile and its CI workflow say what the plan requires.

These are static checks, plus `scripts/image_size.py` on a synthetic `docker save` archive. The
image itself is built, size-checked and run (`shape profile` on D1 mounted from the host) by
`.github/workflows/container.yml`; builder sessions usually have no Docker daemon, so the build
is verified by CI, not here.
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import re
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")


def _stages() -> list[str]:
    return re.split(r"(?m)^(?=FROM )", DOCKERFILE)[1:]


def test_runtime_image_is_python_311_slim():
    runtime = _stages()[-1]
    assert runtime.startswith("FROM python:3.11-slim\n")


def test_the_wheel_with_the_azure_extra_is_installed_and_slimmed_in_the_build_stage():
    build, runtime = _stages()[0], _stages()[-1]
    assert re.search(r"pip install .*--prefix=/install .*sqllocks_shape-\*\.whl\)\[azure\]", build)
    assert "strip --strip-unneeded" in build  # the 500 MB gate needs it (523 MB unstripped)
    assert "-path '*.libs' -prune" in build  # auditwheel-grafted libraries are left alone
    # Only the installed prefix reaches the runtime image: never the wheel, never the toolchain.
    copies = [ln for ln in runtime.splitlines() if ln.startswith("COPY")]
    assert copies == ["COPY --from=build /install /usr/local"]


def test_a_broken_trim_or_strip_fails_the_build():
    runtime = _stages()[-1]
    assert "ctypes.CDLL" in runtime and "rglob('*.so*')" in runtime  # every shared object loads
    assert "import adlfs, aiohttp, azure.identity" in runtime
    assert "deltalake" in runtime and "kernel_name() == 'rust'" in runtime


def test_runtime_runs_as_a_non_root_user_and_ends_on_it():
    runtime = _stages()[-1]
    assert re.search(r"useradd .*--uid 10001", runtime)
    user_lines = [ln for ln in runtime.splitlines() if ln.startswith("USER ")]
    assert user_lines and user_lines[-1] == "USER shape"
    assert runtime.rstrip().splitlines()[-1].startswith("CMD ")  # nothing after USER runs as root


def test_the_build_stage_builds_the_rust_wheel_from_the_checkout():
    build = _stages()[0]
    assert "maturin build --release" in build
    for needed in ("pyproject.toml", "rust", "src", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        assert re.search(rf"COPY .*\b{re.escape(needed)}\b", build), needed


def test_no_entrypoint_so_the_command_line_is_the_whole_command():
    code = [ln for ln in DOCKERFILE.splitlines() if not ln.lstrip().startswith("#")]
    assert not any(ln.startswith("ENTRYPOINT") for ln in code)


def test_dockerignore_keeps_the_context_small_and_the_sources_in():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert {".git", "tests", "benchmarks", "rust/**/target"} <= set(ignore)
    for needed in ("src", "rust", "pyproject.toml", "README.md", "LICENSE"):
        assert needed not in ignore


def test_workflow_builds_checks_size_and_profiles_d1_in_the_image():
    assert "docker/build-push-action" in WORKFLOW
    assert "-lt 500000000" in WORKFLOW
    assert "datasets.py D1" in WORKFLOW
    assert re.search(r"-v \"\$BENCH_DATA_DIR/profile:/data:ro\"", WORKFLOW)
    assert "shape profile" in WORKFLOW


def test_workflow_publishes_to_ghcr_only_on_version_tags():
    assert re.search(r"if: startsWith\(github.ref, 'refs/tags/v'\)", WORKFLOW)
    assert "ghcr.io/sqllocks/shape" in WORKFLOW
    assert WORKFLOW.count("push: true") == 1
    publish = WORKFLOW[WORKFLOW.index("  publish:") :]
    assert "push: true" in publish and "packages: write" in publish
    assert "packages: write" not in WORKFLOW[: WORKFLOW.index("  publish:")]


@pytest.mark.parametrize("name", ["Dockerfile", ".dockerignore"])
def test_files_exist(name):
    assert (ROOT / name).is_file()


def _image_size_module():
    spec = importlib.util.spec_from_file_location("image_size", ROOT / "scripts" / "image_size.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _layer(files: dict[str, int], compress: bool) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as layer:
        directory = tarfile.TarInfo("usr")
        directory.type = tarfile.DIRTYPE
        layer.addfile(directory)  # a directory entry is not counted
        for name, size in files.items():
            info = tarfile.TarInfo(name)
            info.size = size
            layer.addfile(info, io.BytesIO(b"x" * size))
    return gzip.compress(raw.getvalue()) if compress else raw.getvalue()


def test_image_size_sums_uncompressed_files_of_plain_and_gzip_layers(tmp_path):
    saved = tmp_path / "image.tar"
    blobs = {
        "blobs/sha256/a": _layer({"usr/a": 3000, "usr/b": 1000}, compress=True),
        "blobs/sha256/b": _layer({"usr/c": 500}, compress=False),
        "blobs/sha256/config": b"{}",  # not a layer
    }
    manifest = json.dumps([{"Layers": ["blobs/sha256/a", "blobs/sha256/b"]}]).encode()
    with tarfile.open(saved, mode="w") as archive:
        for name, data in {**blobs, "manifest.json": manifest}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    assert _image_size_module().uncompressed_layer_bytes(saved) == 4500

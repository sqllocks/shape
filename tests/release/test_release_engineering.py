"""P8-04 release engineering: version agreement, the version change, the release set, the
per-archive SBOMs, the post-publish smoke check and the T-25 workflow invariants."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
WORKFLOWS = Path(__file__).resolve().parent / "data" / "release_workflows"


def _script(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cv = _script("check_versions")
sv = _script("set_version")
brd = _script("build_release_dist")
sbom = _script("release_sbom")
smoke = _script("release_smoke")
crw = _script("check_release_workflows")
ric = _script("release_index_check")

VERSION = cv.check(ROOT)[0]


def _mini_tree(tmp: Path) -> Path:
    """A copy of every file that carries the release version, plus README and CHANGELOG."""
    files = [
        "pyproject.toml",
        "README.md",
        "src/shape/__init__.py",
        "rust/shape-kernel/Cargo.toml",
        "rust/shape-kernel/Cargo.lock",
    ]
    files += [str(p.relative_to(ROOT) / "pyproject.toml") for p in cv.plugin_dirs(ROOT)]
    for rel in files:
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp / rel)
    (tmp / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n", "utf-8")
    return tmp


def _edit(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text("utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, count), "utf-8")


# -- check_versions -----------------------------------------------------------------------------


def test_the_repository_versions_agree() -> None:
    version, problems = cv.check(ROOT)
    assert problems == []
    assert version == cv.collect(ROOT)[0][0].value
    wheres = [loc.where for loc in cv.collect(ROOT)[0]]
    assert "src/shape/__init__.py __version__" in wheres
    assert "rust/shape-kernel/Cargo.lock shape-kernel" in wheres
    # every plugin's version, and the healthcare-* pins whose names have two hyphenated parts
    assert sum(w.endswith("[project] version") for w in wheres) == 1 + len(cv.plugin_dirs(ROOT))
    assert any("sqllocks-shape-healthcare-codes==" in w for w in wheres)


@pytest.mark.parametrize(
    ("rel", "old", "new", "named"),
    [
        (
            "plugins/shape-kafka/pyproject.toml",
            'version = "{v}"',
            'version = "9.9.9"',
            "shape-kafka",
        ),
        (
            "pyproject.toml",
            '"sqllocks-shape-healthcare-codes=={v}"',
            '"sqllocks-shape-healthcare-codes==9.9.9"',
            "healthcare-codes",
        ),
        ("src/shape/__init__.py", '__version__ = "{v}"', '__version__ = "9.9.9"', "__version__"),
        ("rust/shape-kernel/Cargo.toml", 'version = "{v}"', 'version = "9.9.9"', "Cargo.toml"),
        (
            "rust/shape-kernel/Cargo.lock",
            'name = "shape-kernel"\nversion = "{v}"',
            'name = "shape-kernel"\nversion = "9.9.9"',
            "Cargo.lock",
        ),
        (
            "plugins/shape-fabric/pyproject.toml",
            '"sqllocks-shape[azure]=={v}"',
            '"sqllocks-shape[azure]==9.9.9"',
            "shape-fabric",
        ),
    ],
)
def test_a_location_that_drifts_is_named(
    tmp_path: Path, rel: str, old: str, new: str, named: str
) -> None:
    root = _mini_tree(tmp_path)
    assert cv.check(root)[1] == []
    _edit(root / rel, old.format(v=VERSION), new)
    problems = cv.check(root)[1]
    assert len(problems) == 1 and named in problems[0] and "'9.9.9'" in problems[0], problems


def test_a_first_party_requirement_must_be_an_exact_pin(tmp_path: Path) -> None:
    root = _mini_tree(tmp_path)
    _edit(
        root / "plugins/shape-dbt/pyproject.toml",
        f'"sqllocks-shape=={VERSION}"',
        '"sqllocks-shape>=0.9"',
    )
    problems = cv.check(root)[1]
    assert len(problems) == 1 and "shape-dbt" in problems[0] and "must be pinned" in problems[0]
    # a plugin that does not require core at all
    _edit(root / "plugins/shape-dbt/pyproject.toml", '"sqllocks-shape>=0.9", ', "")
    assert cv.check(root)[1] == [
        "plugins/shape-dbt/pyproject.toml: dependencies must pin sqllocks-shape=={version}"
    ]


def test_tag_and_expect_must_match(tmp_path: Path) -> None:
    assert cv.check(ROOT, tag=f"v{VERSION}")[1] == []
    assert cv.check(ROOT, tag=VERSION)[1] == [
        f"tag {VERSION!r} does not match the version: it must be 'v{VERSION}'"
    ]
    assert cv.check(ROOT, expect="0.0.1")[1] == [f"version is {VERSION!r}, expected '0.0.1'"]
    assert cv.main(["--tag", "v0.0.0"]) == 1
    assert cv.main(["--expect", VERSION]) == 0


def test_a_release_needs_a_final_version_a_changelog_section_and_for_0x_early_access(
    tmp_path: Path,
) -> None:
    root = _mini_tree(tmp_path)
    sv.set_version(root, "1.0.0.dev3")
    problems = cv.check(root, release=True)[1]
    assert any("pre-release or dev" in p for p in problems)
    assert any("no '## 1.0.0.dev3' section" in p for p in problems)
    sv.set_version(root, "1.0.0")
    assert cv.check(root, release=True)[1] == ["CHANGELOG.md has no '## 1.0.0' section"]
    _edit(root / "CHANGELOG.md", "## Unreleased\n", "## Unreleased\n\n## 1.0.0\n")
    assert cv.check(root, release=True)[1] == []
    assert cv.check(root, release=True, tag="v1.0.0")[1] == []
    # below 1.0 the README must say early access
    sv.set_version(root, "0.9.5")
    _edit(root / "CHANGELOG.md", "## 1.0.0\n", "## [0.9.5] - 2026-10-05\n")
    assert cv.check(root, release=True)[1] == []
    (root / "README.md").write_text("# Shape\n", "utf-8")
    assert cv.check(root, release=True)[1] == [
        "README.md must say 'early access' while the version is 0.9.5"
    ]
    for pre in ("1.0.0rc1", "1.0.0a1", "1.0.0b2"):
        assert not cv.is_final(pre)
    assert cv.is_final("1.0.0") and cv.is_final("1.0.0.post1")


# -- set_version --------------------------------------------------------------------------------


def test_set_version_rewrites_every_location_and_nothing_else(tmp_path: Path) -> None:
    root = _mini_tree(tmp_path)
    before = {p: p.read_text("utf-8") for p in root.rglob("*") if p.is_file()}
    assert sv.set_version(root, "1.0.0", dry_run=True) and all(
        p.read_text("utf-8") == t for p, t in before.items()
    )
    changed = sv.set_version(root, "1.0.0")
    assert cv.check(root, expect="1.0.0")[1] == []
    assert len(changed) == 4 + len(cv.plugin_dirs(ROOT))  # core, __init__, Cargo.toml/.lock
    assert {root / "README.md", root / "CHANGELOG.md"}.isdisjoint(changed)
    for path, text in before.items():
        new = path.read_text("utf-8")
        if path in changed:
            # only version strings changed: line count and every other line are the same
            assert len(new.splitlines()) == len(text.splitlines())
            diff = [
                (a, b) for a, b in zip(text.splitlines(), new.splitlines(), strict=True) if a != b
            ]
            assert diff and all(a.replace(VERSION, "1.0.0") == b for a, b in diff), diff
        else:
            assert new == text
    # and back again: the round trip restores the tree byte for byte
    sv.set_version(root, VERSION)
    assert all(p.read_text("utf-8") == t for p, t in before.items())


def test_set_version_refuses_a_bad_version_or_a_tree_that_already_disagrees(tmp_path: Path) -> None:
    root = _mini_tree(tmp_path)
    with pytest.raises(ValueError, match="not a canonical PEP 440"):
        sv.set_version(root, "v1.0.0")
    _edit(root / "src/shape/__init__.py", f'"{VERSION}"', '"0.0.1"')
    snapshot = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="disagree before"):
        sv.set_version(root, "1.0.0")
    assert all(p.read_bytes() == b for p, b in snapshot.items())
    assert sv.main(["1.0.0", "--root", str(root)]) == 1


# -- the release set ----------------------------------------------------------------------------


def _platform_wheels(version: str) -> list[str]:
    tags = [
        "manylinux_2_28_x86_64",
        "manylinux_2_28_aarch64",
        "musllinux_1_2_x86_64",
        "musllinux_1_2_aarch64",
        "macosx_11_0_arm64",
        "macosx_10_12_x86_64",
        "win_amd64",
    ]
    return [f"sqllocks_shape-{version}-cp311-abi3-{t}.whl" for t in tags]


def test_expected_archives_cover_core_and_every_plugin() -> None:
    names = brd.expected_archives("1.0.0")
    assert len(names) == 2 * (1 + len(cv.plugin_dirs(ROOT)))
    assert "sqllocks_shape-1.0.0-py3-none-any.whl" in names
    assert "sqllocks_shape-1.0.0.tar.gz" in names
    assert "sqllocks_shape_healthcare_codes-1.0.0-py3-none-any.whl" in names
    assert "sqllocks_shape_healthcare_codes-1.0.0.tar.gz" in names


def test_a_release_set_needs_one_wheel_per_t04_target_and_nothing_else(tmp_path: Path) -> None:
    names = [*brd.expected_archives("1.0.0"), *_platform_wheels("1.0.0")]
    for name in names:
        (tmp_path / name).touch()
    assert brd.release_set_problems(tmp_path, "1.0.0") == []
    assert sorted(brd.T04_TARGETS) == sorted(
        e["name"] for e in _workflow("wheels.yml")["jobs"]["wheel"]["strategy"]["matrix"]["include"]
    )
    (tmp_path / "sqllocks_shape-1.0.0-cp311-abi3-win_amd64.whl").unlink()
    (tmp_path / "sqllocks_shape-1.0.0-cp311-abi3-manylinux_2_34_x86_64.whl").touch()
    (tmp_path / "sqllocks_shape_kafka-1.0.0.tar.gz").unlink()
    assert brd.release_set_problems(tmp_path, "1.0.0") == [
        "missing sqllocks_shape_kafka-1.0.0.tar.gz",
        "T-04 target windows-x64: 0 platform wheels []",
        "unexpected file sqllocks_shape-1.0.0-cp311-abi3-manylinux_2_34_x86_64.whl",
    ]


# -- SBOMs --------------------------------------------------------------------------------------

PURE_META = (
    "Metadata-Version: 2.4\nName: sqllocks-shape\nVersion: 1.0.0\nLicense-Expression: MIT\n"
    "Requires-Dist: numpy>=2.0,<3\nRequires-Dist: pyarrow>=14.0.1\n"
    "Requires-Dist: tzdata ; sys_platform == 'win32'\n"
    "Requires-Dist: scipy>=1.11 ; extra == 'scipy'\n"
    "Requires-Dist: sqllocks-shape-kafka==1.0.0 ; extra == 'all'\n"
)
NOTICES = (ROOT / "THIRD_PARTY_NOTICES_RUST.md").read_text("utf-8")
LOCK = (ROOT / "rust" / "shape-kernel" / "Cargo.lock").read_text("utf-8")


def _wheel(dist: Path, name: str, meta: str, extra: dict[str, str] | None = None) -> Path:
    path = dist / name
    stem = "-".join(name.split("-")[:2])
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("shape/__init__.py", "")
        z.writestr(f"{stem}.dist-info/METADATA", meta)
        for member, text in (extra or {}).items():
            z.writestr(member, text)
    return path


def _sdist(dist: Path, name: str, meta: str, extra: dict[str, str]) -> Path:
    path = dist / name
    top = name[: -len(".tar.gz")]
    with tarfile.open(path, "w:gz") as tar:
        for member, text in {"PKG-INFO": meta, **extra}.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{member}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    _wheel(d, "sqllocks_shape-1.0.0-py3-none-any.whl", PURE_META)
    _wheel(
        d,
        "sqllocks_shape-1.0.0-cp311-abi3-manylinux_2_28_x86_64.whl",
        PURE_META,
        {
            "shape/_kernel.abi3.so": "\x7fELF",
            "sqllocks_shape-1.0.0.dist-info/licenses/THIRD_PARTY_NOTICES_RUST.md": NOTICES,
        },
    )
    _sdist(
        d,
        "sqllocks_shape-1.0.0.tar.gz",
        PURE_META,
        {"rust/shape-kernel/Cargo.lock": LOCK, "THIRD_PARTY_NOTICES_RUST.md": NOTICES},
    )
    _wheel(
        d,
        "sqllocks_shape_kafka-1.0.0-py3-none-any.whl",
        "Metadata-Version: 2.4\nName: sqllocks-shape-kafka\nVersion: 1.0.0\n"
        "Requires-Dist: sqllocks-shape==1.0.0\nRequires-Dist: confluent-kafka>=2.5,<4\n",
    )
    return d


def _bom(sboms: Path, archive: str) -> dict[str, Any]:
    return json.loads((sboms / f"{archive}.cdx.json").read_text("utf-8"))


def test_every_archive_gets_a_checked_sbom_and_sha256sums(dist: Path, tmp_path: Path) -> None:
    out = tmp_path / "sbom"
    assert sbom.build(dist, out) == []
    assert sbom.check(dist, out) == []
    sums = (out / "SHA256SUMS").read_text("utf-8").splitlines()
    assert len(sums) == 4
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest

    pure = _bom(out, "sqllocks_shape-1.0.0-py3-none-any.whl")
    subject = pure["metadata"]["component"]
    assert (subject["name"], subject["version"]) == ("sqllocks-shape", "1.0.0")
    assert subject["purl"].startswith("pkg:pypi/sqllocks-shape@1.0.0?file_name=")
    assert subject["licenses"] == [{"expression": "MIT"}]
    scopes = {c["name"]: c["scope"] for c in pure["components"]}
    assert scopes == {
        "numpy": "required",
        "pyarrow": "required",
        "tzdata": "required",
        "scipy": "optional",
        "sqllocks-shape-kafka": "optional",
    }
    assert not any(c["purl"].startswith("pkg:cargo/") for c in pure["components"])

    locked = {(n, v) for n, v in (tuple(x) for x in _script("rust_notices").locked_crates())}
    for kernel in (
        "sqllocks_shape-1.0.0-cp311-abi3-manylinux_2_28_x86_64.whl",
        "sqllocks_shape-1.0.0.tar.gz",
    ):
        crates = [c for c in _bom(out, kernel)["components"] if c["purl"].startswith("pkg:cargo/")]
        assert {(c["name"], c["version"]) for c in crates} == locked
        assert all(c["licenses"][0]["expression"] for c in crates)
    kafka = _bom(out, "sqllocks_shape_kafka-1.0.0-py3-none-any.whl")
    root_dep = kafka["dependencies"][0]
    assert root_dep["ref"] == kafka["metadata"]["component"]["bom-ref"]
    assert sorted(root_dep["dependsOn"]) == ["pkg:pypi/confluent-kafka", "pkg:pypi/sqllocks-shape"]


def test_sboms_are_deterministic_and_take_the_source_date(
    dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    sbom.build(dist, a)
    sbom.build(dist, b)
    for f in a.iterdir():
        assert f.read_bytes() == (b / f.name).read_bytes()
    assert "timestamp" not in _bom(a, "sqllocks_shape-1.0.0.tar.gz")["metadata"]
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    c = tmp_path / "c"
    sbom.build(dist, c)
    assert _bom(c, "sqllocks_shape-1.0.0.tar.gz")["metadata"]["timestamp"] == "2026-09-21T14:13:20Z"


def test_check_catches_a_changed_archive_a_doctored_sbom_and_a_stale_list(
    dist: Path, tmp_path: Path
) -> None:
    out = tmp_path / "sbom"
    assert sbom.build(dist, out) == []
    wheel = dist / "sqllocks_shape_kafka-1.0.0-py3-none-any.whl"
    original = wheel.read_bytes()
    with zipfile.ZipFile(wheel, "a") as z:
        z.writestr("shape_kafka/extra.py", "")
    problems = sbom.check(dist, out)
    assert any("the SHA-256 is not the archive's" in p for p in problems)
    assert any("SHA256SUMS does not list" in p for p in problems)
    wheel.write_bytes(original)
    assert sbom.check(dist, out) == []

    path = out / "sqllocks_shape-1.0.0.tar.gz.cdx.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["components"] = [c for c in doc["components"] if c["name"] != "pyarrow"]
    path.write_text(json.dumps(doc), "utf-8")
    problems = sbom.check(dist, out)
    assert any("dependency 'pkg:pypi/pyarrow' names no component" in p for p in problems)
    assert any("requirements missing: ['pkg:pypi/pyarrow']" in p for p in problems)

    sbom.build(dist, out)
    (dist / "sqllocks_shape_kafka-1.0.0-py3-none-any.whl").unlink()
    problems = sbom.check(dist, out)
    assert any("SBOMs for archives that are not in" in p for p in problems)


def test_a_kernel_archive_without_the_rust_notices_is_refused(tmp_path: Path) -> None:
    d = tmp_path / "dist"
    d.mkdir()
    _sdist(d, "sqllocks_shape-1.0.0.tar.gz", PURE_META, {"rust/shape-kernel/Cargo.lock": LOCK})
    problems = sbom.build(d, tmp_path / "sbom")
    assert any("carries the kernel but not THIRD_PARTY_NOTICES_RUST.md" in p for p in problems)
    assert any("crates without a licence" in p for p in problems)


def test_an_archive_whose_name_and_metadata_disagree_is_refused(tmp_path: Path) -> None:
    d = tmp_path / "dist"
    d.mkdir()
    _wheel(d, "sqllocks_shape-1.0.1-py3-none-any.whl", PURE_META)
    assert sbom.build(d, tmp_path / "sbom") == [
        "sqllocks_shape-1.0.1-py3-none-any.whl: sqllocks_shape-1.0.1-py3-none-any.whl: file name "
        "does not match its metadata sqllocks-shape 1.0.0"
    ]


def test_the_schema_check_validates_or_says_it_cannot(dist: Path, tmp_path: Path) -> None:
    out = tmp_path / "sbom"
    sbom.build(dist, out)
    if importlib.util.find_spec("cyclonedx") is None:
        assert sbom.check(dist, out, schema=True)[0].startswith("--schema needs cyclonedx")
        return
    assert sbom.check(dist, out, schema=True) == []
    path = out / "sqllocks_shape-1.0.0.tar.gz.cdx.json"
    doc = json.loads(path.read_text("utf-8"))
    doc["components"][0]["scope"] = "sometimes"
    path.write_text(json.dumps(doc), "utf-8")
    assert any("'sometimes' is not one of" in p for p in sbom.schema_problems(path))


def test_audit_requirements_pin_third_party_components_only() -> None:
    doc = {
        "components": [
            {"name": "PyYAML", "version": "6.0.3"},
            {"name": "numpy", "version": "2.4.6"},
            {"name": "sqllocks-shape-kafka", "version": "1.0.0"},
            {"name": "no-version"},
        ]
    }
    assert sbom.audit_requirements(doc) == ["numpy==2.4.6", "pyyaml==6.0.3"]


# -- the post-publish smoke check ---------------------------------------------------------------


def test_first_party_is_core_and_every_plugin() -> None:
    names = smoke.first_party()
    assert names[0] == "sqllocks-shape" and len(names) == 1 + len(cv.plugin_dirs(ROOT))
    assert "sqllocks-shape-healthcare-standards" in names


def test_install_commands_per_source(tmp_path: Path) -> None:
    py = tmp_path / "python"
    (testpypi,) = smoke.install_commands(py, "1.0.0", "testpypi")
    assert testpypi[-5:] == [
        "--index-url",
        "https://test.pypi.org/simple/",
        "--extra-index-url",
        "https://pypi.org/simple/",
        "sqllocks-shape[all]==1.0.0",
    ]
    (pypi,) = smoke.install_commands(py, "1.0.0", "pypi")
    assert pypi[-3:] == ["--index-url", "https://pypi.org/simple/", "sqllocks-shape[all]==1.0.0"]
    first, second = smoke.install_commands(py, "1.0.0", str(tmp_path))
    # first-party only from the directory, never an index
    assert "--no-index" in first and "--no-deps" in first
    assert {a for a in first if "==" in a} == {f"{n}==1.0.0" for n in smoke.first_party()}
    assert second[-1] == "sqllocks-shape[all]==1.0.0" and str(tmp_path) in second


def test_plugin_list_problems() -> None:
    entries = [
        {"group": "shape.sources", "name": n, "source": n, "error": None}
        for n in smoke.first_party()
    ]
    ok = json.dumps({"payload": entries})
    assert smoke.missing_plugins(ok) == []
    entries[3]["error"] = "ImportError: no module named x"
    bad = [e for e in entries if e["source"] != "sqllocks-shape-kafka"]
    problems = smoke.missing_plugins(json.dumps({"payload": bad}))
    assert any("ImportError" in p for p in problems)
    assert "no plugin entry from sqllocks-shape-kafka" in problems


def test_probe_problems() -> None:
    good = {
        "file": "/v/lib/python3.11/site-packages/shape/__init__.py",
        "version": "1.0.0",
        "kernel": "rust",
        "installed": {n: "1.0.0" for n in smoke.first_party()},
    }
    assert smoke.probe_problems(good, "1.0.0", "rust") == []
    assert smoke.probe_problems({**good, "kernel": "python"}, "1.0.0", "any") == []
    bad = {
        **good,
        "file": "/home/me/shape/src/shape/__init__.py",
        "kernel": "python",
        "installed": {**good["installed"], "sqllocks-shape-dbt": None},  # type: ignore[dict-item]
    }
    assert smoke.probe_problems(bad, "1.0.0", "rust") == [
        "shape was imported from /home/me/shape/src/shape/__init__.py, not the installed release",
        "the kernel is 'python', expected 'rust'",
        "sqllocks-shape-dbt is installed at None, expected '1.0.0'",
    ]


# -- the T-25 workflows -------------------------------------------------------------------------


def _workflow(name: str) -> dict[str, Any]:
    return crw._load(WORKFLOWS / name)


def _write(directory: Path, workflows: dict[str, dict[str, Any]]) -> Path:
    directory.mkdir(exist_ok=True)
    for name, wf in workflows.items():
        (directory / name).write_text(yaml.safe_dump(wf, sort_keys=False), "utf-8")
    return directory


def _all() -> dict[str, dict[str, Any]]:
    return {n: _workflow(n) for n in crw.CHECKS}


def test_the_proposed_release_workflows_keep_t25_and_t04() -> None:
    assert crw.check(WORKFLOWS) == []


def test_a_yaml_round_trip_is_clean_and_a_missing_workflow_is_reported(tmp_path: Path) -> None:
    # the mutation tests below rewrite the workflows through yaml.safe_dump
    assert crw.check(_write(tmp_path / "wf", _all())) == []
    (tmp_path / "wf" / "wheels.yml").unlink()
    assert crw.check(tmp_path / "wf") == ["wheels.yml: missing"]


def _mutated(tmp_path: Path, mutate: Any) -> list[str]:
    wfs = _all()
    mutate(wfs)
    return crw.check(_write(tmp_path / "wf", wfs))


def _publish_step(wfs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    steps = wfs["publish.yml"]["jobs"]["publish"]["steps"]
    return next(s for s in steps if "gh-action-pypi-publish" in str(s.get("uses")))


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda w: (
                _publish_step(w).setdefault("with", {}).update(password="${{ secrets.PYPI }}")
            ),
            ["publish.yml: reads a repository secret", "must use trusted publishing, no token"],
        ),
        (
            lambda w: _publish_step(w).update(uses="pypa/gh-action-pypi-publish@v1.12.0"),
            ["must be @release/v1"],
        ),
        (
            lambda w: w["publish.yml"]["jobs"]["publish"].update(environment="release"),
            ["must run in the environment pypi or testpypi"],
        ),
        (
            lambda w: w["publish.yml"]["on"].update(pull_request={}),
            ["triggers must be workflow_dispatch and tag pushes"],
        ),
        (
            lambda w: w["publish.yml"]["on"]["push"].update(branches=["main"]),
            ["pushes must trigger on v* tags only"],
        ),
        (
            lambda w: w["publish.yml"]["jobs"]["installed"].update(
                permissions={"id-token": "write"}
            ),
            ["job installed must not hold id-token: write"],
        ),
        (
            lambda w: w["publish.yml"]["jobs"]["publish"]["steps"].insert(
                0, {"uses": "actions/checkout@v4"}
            ),
            ["must publish the built archives, not a checkout"],
        ),
        (
            lambda w: w["publish.yml"]["jobs"]["build"].update(uses="./.github/workflows/x.yml"),
            ["one job must call ./.github/workflows/release.yml"],
        ),
        (
            lambda w: w["release.yml"]["on"].update(push={"branches": ["main"]}),
            ["release.yml: must not run on a push or a pull request"],
        ),
        (
            lambda w: w["release.yml"]["jobs"]["sbom"]["steps"].pop(
                next(
                    i
                    for i, s in enumerate(w["release.yml"]["jobs"]["sbom"]["steps"])
                    if str(s.get("uses", "")).startswith("actions/attest-sbom")
                )
            ),
            ["one job must run attest-build-provenance and attest-sbom"],
        ),
        (
            lambda w: w["release.yml"]["jobs"]["smoke"].update(
                permissions={"attestations": "write"}
            ),
            ["job smoke must not hold id-token or attestations: write"],
        ),
        (
            lambda w: w["release.yml"]["jobs"]["sbom"]["steps"].append(
                {"uses": "pypa/gh-action-pypi-publish@release/v1"}
            ),
            ["job sbom publishes; only publish.yml may"],
        ),
        (
            lambda w: [
                s.update(run=s["run"].replace(" --schema", ""))
                for s in w["release.yml"]["jobs"]["sbom"]["steps"]
                if "release_sbom.py check" in str(s.get("run", ""))
            ],
            ["checked against the CycloneDX schema"],
        ),
        (
            lambda w: w["wheels.yml"]["jobs"]["wheel"]["strategy"]["matrix"]["include"].pop(),
            ["the wheel matrix is"],
        ),
        (
            lambda w: w["wheels.yml"]["jobs"]["wheel"]["strategy"]["matrix"]["include"][0].update(
                manylinux="2014"
            ),
            ["manylinux-x86_64 must build with manylinux: '2_28'"],
        ),
        (
            lambda w: w["wheels.yml"]["on"].pop("workflow_call"),
            ["wheels.yml: must be callable"],
        ),
        (
            lambda w: w["publish.yml"]["jobs"].pop("index-hashes"),
            ["release_index_check.py must check the index's SHA-256"],
        ),
        (
            lambda w: w["publish.yml"]["jobs"]["installed"].update(needs=["build"]),
            ["release_smoke.py must install from the index after publishing"],
        ),
    ],
)
def test_each_t25_invariant_is_enforced(tmp_path: Path, mutate: Any, expected: list[str]) -> None:
    problems = _mutated(tmp_path, mutate)
    for needle in expected:
        assert any(needle in p for p in problems), (needle, problems)


# -- the index serves what was built ------------------------------------------------------------

SUMS = (
    f"{'a' * 64}  sqllocks_shape-1.0.0-py3-none-any.whl\n"
    f"{'b' * 64}  sqllocks_shape-1.0.0.tar.gz\n"
    f"{'c' * 64}  sqllocks_shape-1.0.0-cp311-abi3-win_amd64.whl\n"
    f"{'d' * 64} *sqllocks_shape_healthcare_codes-1.0.0-py3-none-any.whl\n"
)


def _index(files: dict[str, dict[str, str]]) -> Any:
    calls: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        project = url.split("/pypi/")[1].split("/")[0]
        if project not in files:
            raise OSError("HTTP Error 404: Not Found")
        return {
            "urls": [{"filename": n, "digests": {"sha256": d}} for n, d in files[project].items()]
        }

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_index_check_passes_only_when_the_index_serves_exactly_the_built_files() -> None:
    sums = ric.parse_sums(SUMS)
    assert ric.projects(sums) == {
        "sqllocks-shape": "1.0.0",
        "sqllocks-shape-healthcare-codes": "1.0.0",
    }
    core = {
        "sqllocks_shape-1.0.0-py3-none-any.whl": "a" * 64,
        "sqllocks_shape-1.0.0.tar.gz": "b" * 64,
        "sqllocks_shape-1.0.0-cp311-abi3-win_amd64.whl": "c" * 64,
    }
    plugin = {"sqllocks_shape_healthcare_codes-1.0.0-py3-none-any.whl": "d" * 64}
    fetch = _index({"sqllocks-shape": core, "sqllocks-shape-healthcare-codes": plugin})
    assert ric.index_problems(sums, "testpypi", fetch) == []
    assert fetch.calls == [
        "https://test.pypi.org/pypi/sqllocks-shape/1.0.0/json",
        "https://test.pypi.org/pypi/sqllocks-shape-healthcare-codes/1.0.0/json",
    ]
    changed = {**core, "sqllocks_shape-1.0.0.tar.gz": "e" * 64}
    extra = {**plugin, "sqllocks_shape_healthcare_codes-1.0.0.tar.gz": "f" * 64}
    problems = ric.index_problems(
        sums, "pypi", _index({"sqllocks-shape": changed, "sqllocks-shape-healthcare-codes": extra})
    )
    assert problems == [
        f"sqllocks_shape-1.0.0.tar.gz: pypi serves sha256 {'e' * 64}, the release built {'b' * 64}",
        "sqllocks_shape_healthcare_codes-1.0.0.tar.gz: on pypi but not built by this release",
    ]
    problems = ric.index_problems(sums, "pypi", _index({"sqllocks-shape": core}))
    assert problems[0].startswith("sqllocks-shape-healthcare-codes 1.0.0: not on pypi (")
    assert problems[1:] == ["sqllocks_shape_healthcare_codes-1.0.0-py3-none-any.whl: not on pypi"]


def test_index_check_refuses_a_malformed_sums_file() -> None:
    with pytest.raises(ValueError, match="not a sha256sum line"):
        ric.parse_sums("abc  file.whl\n")
    with pytest.raises(ValueError, match="not a wheel or sdist"):
        ric.projects({"SHA256SUMS": "a" * 64})


def test_pypi_publishes_only_from_a_release_tag() -> None:
    """#266: a manual run with repository = pypi, or a push that is not a v* tag, fails in the
    guard job before anything is built or uploaded; the real workflow and its copy agree."""
    import yaml

    real = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish.yml"
    assert real.read_text(encoding="utf-8") == (WORKFLOWS / "publish.yml").read_text(
        encoding="utf-8"
    )
    jobs = yaml.safe_load(real.read_text(encoding="utf-8"))["jobs"]
    step = jobs["guard"]["steps"][0]
    assert step["if"] == "github.event_name != 'workflow_dispatch' || inputs.repository == 'pypi'"
    assert "refs/tags/v*)" in step["run"] and "exit 1" in step["run"]
    assert jobs["build"]["needs"] == "guard"
    assert jobs["publish"]["needs"] == "build"


def test_every_project_publishes_from_its_own_environment() -> None:
    """PyPI takes a pending trusted publisher for one project name only, so the publish job runs
    once per project: core in pypi/testpypi (its publisher since 0.9.0), each first-party plugin
    in pypi-<plugin>/testpypi-<plugin>, uploading only that project's archives."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    job = yaml.safe_load((WORKFLOWS / "publish.yml").read_text(encoding="utf-8"))["jobs"]["publish"]
    rows = job["strategy"]["matrix"]["include"]
    plugins = sorted(
        p.name.removeprefix("shape-") for p in (root / "plugins").iterdir() if p.is_dir()
    )
    expected = [{"project": "sqllocks-shape", "files": "sqllocks_shape-", "env": ""}] + [
        {
            "project": f"sqllocks-shape-{p}",
            "files": f"sqllocks_shape_{p.replace('-', '_')}-",
            "env": f"-{p}",
        }
        for p in plugins
    ]
    assert rows == expected
    assert job["strategy"]["fail-fast"] is False
    assert job["environment"].endswith("}${{ matrix.env }}")
    assert "'testpypi'" in job["environment"] and "'pypi'" in job["environment"]
    for step in job["steps"]:
        if "pypa/gh-action-pypi-publish" in str(step.get("uses", "")):
            assert step["with"]["packages-dir"] == "upload/"

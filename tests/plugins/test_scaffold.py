"""W6-01 deliverable 6: ``shape plugins new``."""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import tomllib
import venv
from pathlib import Path

import pytest
import yaml

from shape.cli.main import main
from shape.plugins import scaffold
from shape.plugins.api import v1

GROUPS = sorted(v1.GROUPS)
MAIN_JOB = ("shape.sources", "shape.detectors")  # the others run in the nightly job
FILES = {
    "pyproject.toml",
    "README.md",
    "tests/conftest.py",
    "tests/test_conformance.py",
    ".github/workflows/ci.yml",
}


def _name(group: str) -> str:
    return "acme-" + group.removeprefix("shape.").replace("_", "")


def _render(group: str, name: str | None = None, author: str | None = None) -> dict[str, str]:
    return scaffold.render(name or _name(group), group, author)


# ---- one template per group ----------------------------------------------------------------


@pytest.mark.parametrize("group", GROUPS)
def test_every_group_has_a_template(group):
    assert scaffold.has_template(group), (
        f"no template for {group}: add it under shape/plugins/template"
    )


def test_no_group_is_missing_a_template_and_no_template_is_orphaned():
    assert scaffold.missing_templates() == []


def test_a_new_group_without_a_template_is_caught(monkeypatch):
    monkeypatch.setitem(v1.GROUPS, "shape.newthings", "Thing")
    assert scaffold.missing_templates() == ["shape.newthings"]
    with pytest.raises(ValueError, match="no template"):
        scaffold.render("acme-thing", "shape.newthings")


def test_every_group_has_a_class_suffix():
    assert set(scaffold._CLASS_SUFFIX) == set(v1.GROUPS)


@pytest.mark.parametrize("group", GROUPS)
def test_the_rendered_files(group):
    files = _render(group)
    package = _name(group).replace("-", "_")
    assert set(files) == FILES | {f"src/{package}/__init__.py"}
    for rel, text in files.items():
        assert "{{" not in text.replace("${{", ""), rel  # every token is filled
        assert text.endswith("\n")
    meta = tomllib.loads(files["pyproject.toml"])
    cls = scaffold.class_name(_name(group), group)
    assert meta["project"]["entry-points"] == {group: {_name(group): f"{package}:{cls}"}}
    assert meta["project"]["name"] == _name(group)
    assert "sqllocks-shape" in meta["project"]["dependencies"]
    module = files[f"src/{package}/__init__.py"]
    compile(module, "module", "exec")
    assert 'SHAPE_API = "1.0"' in module and f"class {cls}" in module
    assert f'name = "{_name(group)}"' in module
    compile(files["tests/test_conformance.py"], "test", "exec")
    assert "kit.check_" in files["tests/test_conformance.py"]
    workflow = yaml.safe_load(files[".github/workflows/ci.yml"])
    runs = [s.get("run", "") for s in workflow["jobs"]["test"]["steps"]]
    assert "pytest" in runs and f"python -m shape.plugins.kit {_name(group)}" in runs
    readme = files["README.md"]
    assert group in readme and v1.GROUPS[group] in readme and _name(group) in readme


@pytest.mark.parametrize("group", GROUPS)
def test_the_rendered_plugin_passes_its_own_conformance_test(group, tmp_path):
    target = tmp_path / "plugin"
    assert main(["plugins", "new", _name(group), "--group", group, "-o", str(target)]) == 0
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=target,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed" in done.stdout


def _install_and_run_kit(
    plugin: Path, dist: str, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=False, clear=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site_dir = Path(
        subprocess.run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    # the new environment sees the packages of this one (shape and what it needs), .pth files too
    lines = [f"import site; site.addsitedir({p!r})" for p in site.getsitepackages()]
    lines += [f"import site; site.addsitedir({site.getusersitepackages()!r})"]
    (site_dir / "parent.pth").write_text("\n".join(lines) + "\n", encoding="utf-8")
    install = subprocess.run(
        [sys.executable, "-m", "pip", "--python", str(python), "install", "--quiet", "--no-deps",
         "--no-build-isolation", "--no-index", "--disable-pip-version-check", str(plugin)],
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert install.returncode == 0, install.stdout + install.stderr
    return subprocess.run(
        [str(python), "-m", "shape.plugins.kit", dist], capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "group",
    [g if g in MAIN_JOB else pytest.param(g, marks=pytest.mark.nightly) for g in GROUPS],
)
def test_the_rendered_plugin_installs_into_a_virtual_environment_and_passes_the_kit(
    group, tmp_path
):
    plugin = tmp_path / "plugin"
    assert main(["plugins", "new", _name(group), "--group", group, "-o", str(plugin)]) == 0
    done = _install_and_run_kit(plugin, _name(group), tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{group}:{_name(group)}: ok" in done.stdout
    assert done.stdout.strip().endswith("conform to plugin API 1.0")


def test_the_installed_plugin_is_found_by_the_host_and_listed(tmp_path):
    plugin = tmp_path / "plugin"
    assert (
        main(["plugins", "new", "acme-listed", "--group", "shape.detectors", "-o", str(plugin)])
        == 0
    )
    done = _install_and_run_kit(plugin, "acme-listed", tmp_path)
    assert done.returncode == 0


# ---- the command ---------------------------------------------------------------------------


def test_a_default_folder_is_named_after_the_package(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["plugins", "new", "acme-here", "--group", "shape.sources"]) == 0
    assert (tmp_path / "acme-here" / "pyproject.toml").is_file()
    summary = json.loads(capsys.readouterr().out)
    assert summary["folder"] == "acme-here" and len(summary["files"]) == 6


def test_an_empty_existing_folder_is_used(tmp_path):
    target = tmp_path / "empty"
    target.mkdir()
    assert main(["plugins", "new", "acme-x", "--group", "shape.sources", "-o", str(target)]) == 0
    assert (target / "src" / "acme_x" / "__init__.py").is_file()


@pytest.mark.parametrize(
    "name",
    [
        "", "Acme", "acme_x", "1acme", "-acme", "acme-", "acme--x", "acme x", "acme.x", "ac/me",
        "../acme", "import", "shape", "tests", "x" * 49, "ünï",
    ],
)  # fmt: skip
def test_an_invalid_package_name_exits_two_and_writes_nothing(name, tmp_path, capsys):
    target = tmp_path / "out"
    try:
        code = main(["plugins", "new", name, "--group", "shape.sources", "-o", str(target)])
    except SystemExit as exc:  # a name that starts with a hyphen is read as an option
        code = exc.code
    assert code == 2
    assert not target.exists() and list(tmp_path.iterdir()) == []
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["a", "acme", "acme-x1", "a1-b2-c3", "x" * 48])
def test_valid_package_names(name, tmp_path):
    assert (
        main(["plugins", "new", name, "--group", "shape.sources", "-o", str(tmp_path / "o")]) == 0
    )


@pytest.mark.parametrize(
    "group", ["shape.nothing", "sources", "", "shape.sources ", "SHAPE.SOURCES"]
)
def test_an_unknown_group_exits_two_and_writes_nothing(group, tmp_path, capsys):
    target = tmp_path / "out"
    assert main(["plugins", "new", "acme-x", "--group", group, "-o", str(target)]) == 2
    assert not target.exists()
    assert "unknown group" in capsys.readouterr().err


def test_a_non_empty_folder_exits_two_and_is_left_as_it_was(tmp_path, capsys):
    target = tmp_path / "busy"
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")
    assert main(["plugins", "new", "acme-x", "--group", "shape.sources", "-o", str(target)]) == 2
    assert [p.name for p in target.iterdir()] == ["keep.txt"]
    assert "not empty" in capsys.readouterr().err


def test_a_file_in_place_of_the_folder_exits_two(tmp_path):
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")
    assert main(["plugins", "new", "acme-x", "--group", "shape.sources", "-o", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == "x"


def test_the_author_is_written_as_a_toml_string(tmp_path):
    for author in ["Ada Lovelace", 'Q "Quote" \\ Back', "Zoë Ünï <z@example.test>"]:
        files = _render("shape.sources", author=author)
        meta = tomllib.loads(files["pyproject.toml"])
        assert meta["project"]["authors"] == [{"name": author}]
    assert "authors" not in tomllib.loads(_render("shape.sources")["pyproject.toml"])["project"]


@pytest.mark.parametrize("author", ["", "  ", "two\nlines", "bell\x07"])
def test_an_invalid_author_exits_two_and_writes_nothing(author, tmp_path):
    target = tmp_path / "out"
    assert (
        main(
            [
                "plugins",
                "new",
                "acme-x",
                "--group",
                "shape.sources",
                "--author",
                author,
                "-o",
                str(target),
            ]
        )
        == 2
    )
    assert not target.exists()


def test_dry_run_lists_the_files_and_writes_nothing(tmp_path, capsys):
    target = tmp_path / "out"
    assert (
        main(
            [
                "plugins",
                "new",
                "acme-x",
                "--group",
                "shape.sinks",
                "-o",
                str(target),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-dry-run" and len(doc["actions"]) == 6
    assert {a["action"] for a in doc["actions"]} == {"create"}
    assert not target.exists()


def test_dry_run_rejects_what_the_command_rejects(tmp_path):
    assert main(["plugins", "new", "Bad_Name", "--group", "shape.sinks", "--dry-run"]) == 2
    assert main(["plugins", "new", "acme-x", "--group", "shape.zzz", "--dry-run"]) == 2
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "f").write_text("x", encoding="utf-8")
    assert (
        main(["plugins", "new", "acme-x", "--group", "shape.sinks", "-o", str(busy), "--dry-run"])
        == 2
    )


def test_the_json_flag_wraps_the_summary(tmp_path, capsys):
    assert (
        main(
            [
                "plugins",
                "new",
                "acme-x",
                "--group",
                "shape.sinks",
                "-o",
                str(tmp_path / "o"),
                "--json",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    assert (
        doc["format"] == "shape-result"
        and doc["command"] == "plugins new"
        and doc["group"] == "shape.sinks"
    )


def test_the_package_data_ships_in_the_wheel_layout():
    """The templates are package data of `shape.plugins`, so an installed Shape has them."""
    from importlib import resources

    root = resources.files("shape").joinpath("plugins/template")
    assert root.joinpath("common/pyproject.toml.tmpl").is_file()
    for group in GROUPS:
        assert root.joinpath(group.removeprefix("shape."), "module.py.tmpl").is_file()

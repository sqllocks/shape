"""Frozen registry subprocesses coexist with ordinary installed extension discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from docs_reference_environment import (  # noqa: E402
    is_doctor_command,
    reference_environment,
    validated_doctor_pandas,
)


@pytest.fixture
def extension_environment(tmp_path: Path) -> dict[str, str]:
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "example_doc_extension.py").write_text(
        'SHAPE_API = "1.0"\n'
        "class ExampleCommand:\n"
        '    name = "docs-example"\n'
        '    help = "an installed example extension"\n'
        "    def configure(self, parser): pass\n"
        "    def run(self, args):\n"
        '        print("example extension ran")\n'
        "        return 0\n"
    )
    distribution = installed / "example_doc_extension-1.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: example-doc-extension\nVersion: 1.0\n"
    )
    (distribution / "entry_points.txt").write_text(
        "[shape.commands]\ndocs-example = example_doc_extension:ExampleCommand\n"
    )
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(installed), str(ROOT / "src")]),
    }


def cli(
    environment: dict[str, str], directory: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shape", *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def registry(environment: dict[str, str], directory: Path) -> list[dict[str, object]]:
    result = cli(environment, directory, "plugins", "list", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["format"] == "shape-result" and document["exit_code"] == 0
    return cast(list[dict[str, object]], document["payload"])


def test_normal_cli_discovers_installed_extension(
    extension_environment: dict[str, str], tmp_path: Path
) -> None:
    entries = registry(extension_environment, tmp_path)
    assert any(entry["name"] == "docs-example" for entry in entries)
    result = cli(extension_environment, tmp_path, "docs-example")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "example extension ran\n"


def test_frozen_registry_is_scoped_and_does_not_leak(
    extension_environment: dict[str, str], tmp_path: Path
) -> None:
    original = dict(extension_environment)
    frozen = reference_environment(extension_environment, tmp_path / "startup")
    entries = registry(frozen, tmp_path)
    assert len(entries) == 191
    assert not any(entry["name"] == "docs-example" for entry in entries)
    assert extension_environment == original
    assert any(entry["name"] == "docs-example" for entry in registry(original, tmp_path))
    result = cli(original, tmp_path, "docs-example")
    assert result.returncode == 0 and result.stdout == "example extension ran\n"


def test_explicit_fixture_conformance_remains_available(
    extension_environment: dict[str, str], tmp_path: Path
) -> None:
    frozen = reference_environment(extension_environment, tmp_path / "startup")
    result = subprocess.run(
        [sys.executable, "-m", "shape.plugins.kit", "example-doc-extension"],
        cwd=tmp_path,
        env=frozen,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK example-doc-extension: 1 plugin(s) conform" in result.stdout


@pytest.mark.parametrize(
    ("arguments", "original", "expected"),
    [
        (["/env/bin/shape", "doctor"], ["python", "/env/bin/shape", "doctor"], True),
        (["-m", "doctor"], ["python", "-m", "shape", "doctor"], True),
        (["/env/bin/shape", "plugins", "doctor"], ["python", "shape"], False),
        (["/env/bin/shape", "profile", "doctor"], ["python", "shape"], False),
        (["-m", "doctor"], ["python", "-m", "unrelated", "doctor"], False),
    ],
)
def test_doctor_receipts_identify_only_the_actual_command(
    arguments: list[str], original: list[str], expected: bool
) -> None:
    assert is_doctor_command(arguments, original) is expected


def test_doctor_output_is_unchanged_and_version_is_validated(
    extension_environment: dict[str, str], tmp_path: Path
) -> None:
    from importlib import metadata

    normal = cli(extension_environment, tmp_path, "doctor")
    startup = tmp_path / "startup"
    frozen = cli(reference_environment(extension_environment, startup), tmp_path, "doctor")
    assert normal.returncode == frozen.returncode == 0
    assert normal.stdout == frozen.stdout
    version = metadata.version("pandas")
    assert f"pandas        {version}" in frozen.stdout
    recorded = frozen.stdout.replace(f"pandas        {version}", "pandas        0.1.0")
    actual, shown = validated_doctor_pandas(frozen.stdout, recorded, startup)
    assert actual == shown
    forged = frozen.stdout.replace(f"pandas        {version}", "pandas        0.2.0")
    with pytest.raises(AssertionError, match="installed metadata"):
        validated_doctor_pandas(forged, recorded, startup)
    with pytest.raises(AssertionError, match="fresh command-interpreter receipt"):
        validated_doctor_pandas(frozen.stdout, recorded, tmp_path / "missing")
    with pytest.raises(FileExistsError):
        reference_environment(extension_environment, startup)
    changed = recorded.replace("Result: OK", "Result: FAILED")
    actual, shown = validated_doctor_pandas(frozen.stdout, changed, startup)
    assert actual != shown
    with pytest.raises(AssertionError, match="fresh command-interpreter receipt"):
        validated_doctor_pandas(frozen.stdout * 2, recorded * 2, startup)


def test_console_doctor_command_has_its_own_receipt(
    extension_environment: dict[str, str], tmp_path: Path
) -> None:
    startup = tmp_path / "startup"
    result = subprocess.run(
        [str(Path(sys.executable).parent / "shape"), "doctor"],
        cwd=tmp_path,
        env=reference_environment(extension_environment, startup),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipts = list(startup.glob("doctor-*.json"))
    assert len(receipts) == 1
    assert (
        validated_doctor_pandas(result.stdout, result.stdout, startup)[0]
        == (validated_doctor_pandas(result.stdout, result.stdout, startup)[1])
    )


def test_doctor_version_is_from_child_metadata_not_parent(
    extension_environment: dict[str, str], tmp_path: Path
) -> None:
    from importlib import metadata

    child_packages = tmp_path / "child-packages"
    distribution = child_packages / "pandas-9.8.7.dist-info"
    distribution.mkdir(parents=True)
    (distribution / "METADATA").write_text("Metadata-Version: 2.1\nName: pandas\nVersion: 9.8.7\n")
    assert metadata.version("pandas") != "9.8.7"
    child = {
        **extension_environment,
        "PYTHONPATH": str(child_packages) + os.pathsep + extension_environment["PYTHONPATH"],
    }
    startup = tmp_path / "startup"
    result = cli(reference_environment(child, startup), tmp_path, "doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pandas        9.8.7" in result.stdout
    shown = result.stdout.replace("pandas        9.8.7", "pandas        0.1.0")
    actual, recorded = validated_doctor_pandas(result.stdout, shown, startup)
    assert actual == recorded


def test_reference_commands_bind_caller_then_activate_private_page(tmp_path: Path) -> None:
    import runpy

    checkout = tmp_path / "checkout"
    wrong = checkout / ".venv/bin/python"
    wrong.parent.mkdir(parents=True)
    wrong.write_text('#!/bin/sh\nprintf "wrong checkout interpreter\\n"\nexit 99\n')
    wrong.chmod(0o755)
    page = checkout / "docs/example.md"
    page.parent.mkdir()
    page.write_text(
        textwrap.dedent(
            """\
            <!-- example: 999 -->

            ```bash {.runnable-reference}
            python - <<'PY_PAGE'
            from pathlib import Path
            target = Path('.venv/bin/python')
            target.parent.mkdir(parents=True)
            target.write_text('#!/bin/sh\\nprintf "private page interpreter\\\\n"\\n')
            target.chmod(0o755)
            print('active caller interpreter')
            PY_PAGE
            ```

            ??? info "Output (exit 0)"

                ```text {.expected}
                active caller interpreter
                ```

            <!-- example: 0 -->

            ```bash {.runnable-reference}
            python
            ```

            ??? info "Output (exit 0)"

                ```text {.expected}
                private page interpreter
                ```
            """
        )
    )
    namespace = runpy.run_path(str(ROOT / "tests/docs/test_reference_examples.py"))
    invoke = namespace["test_reference_page"]
    invoke.__globals__["ROOT"] = checkout
    work = tmp_path / "page"
    work.mkdir()
    invoke("docs/example.md", work, "unused")

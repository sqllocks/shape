"""ISS-cli #30 and #27.4: expected errors are one line and exit 2, never a traceback.

``--debug`` (before the command) or ``SHAPE_DEBUG=1`` brings the traceback back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shape.cli.main import main


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    (tmp_path / "people.csv").write_text("id,age\n1,40\n2,41\n3,42\n")
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / "obj.json").write_text('{"a": 1}')
    (tmp_path / "junk.shape").write_text("x")
    assert main(["profile", "people.csv", "-o", "p.shape"]) == 0
    assert main(["registry", "reg", "commit", "people", "p.shape", "--safe"]) == 0
    return tmp_path


COMMANDS = {
    "fidelity-bad-json": ["fidelity", "bad.json", "people.csv"],
    "fidelity-profile-reference": ["fidelity", "p.shape", "people.csv"],
    "fidelity-missing": ["fidelity", "nope.csv", "people.csv"],
    "compatibility-profiles": ["compatibility", "p.shape", "p.shape"],
    "compatibility-missing": ["compatibility", "nope.shape", "nope.shape"],
    "compatibility-bad-json": ["compatibility", "bad.json", "bad.json"],
    "registry-checkout-unknown-ref": ["registry", "reg", "checkout", "people", "nope"],
    "registry-commit-missing-file": ["registry", "reg", "commit", "x", "nope.shape"],
    "registry-tag-unknown-ref": ["registry", "reg", "tag", "people", "v1", "nope"],
    "registry-promote-unknown-ref": ["registry", "reg", "promote", "people", "nope", "prod"],
    "registry-bad-name": ["registry", "reg", "commit", "bad/name", "p.shape"],
    "query-not-an-archive": ["query", "junk.shape", "x"],
    "query-bad-json": ["query", "bad.json", "x"],
    "check-not-an-archive": ["check", "junk.shape", "obj.json"],
    "check-missing": ["check", "nope.shape", "obj.json"],
    "diff-not-an-archive": ["diff", "junk.shape", "junk.shape"],
    "diff-bad-json": ["diff", "bad.json", "bad.json"],
    "inspect-not-an-archive": ["inspect", "junk.shape"],
    "show-missing": ["show", "nope.shape"],
    "quality-missing": ["quality", "nope.csv"],
    "quality-bad-reference": ["quality", "people.csv", "--reference", "bad.json"],
    "key-missing": ["key", "nope.csv", "id"],
    "fd-missing": ["fd", "nope.csv", "--determinant", "id", "--dependent", "age"],
    "privacy-k-missing": ["privacy-k", "nope.csv", "id"],
    "certify-shapes-bad-json": ["certify-shapes", "bad.json", "bad.json"],
    "plan-bad-json": ["plan", "bad.json"],
    "capture-missing": ["capture", "nope.csv"],
    "profile-missing": ["profile", "nope.csv", "-o", "o.shape"],
}


@pytest.mark.parametrize("args", COMMANDS.values(), ids=COMMANDS.keys())
def test_expected_error_is_one_line_exit_2(
    work: Path, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("shape: error: "), err
    assert "\n" not in err and "Traceback" not in err, err


def test_missing_file_wording_is_the_same_everywhere(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    messages = []
    for args in (
        ["profile", "nope.csv", "-o", "o.shape"],
        ["check", "nope.shape", "obj.json"],
        ["show", "nope.shape"],
        ["compatibility", "nope.shape", "nope.shape"],
        ["registry", "reg", "commit", "x", "nope.shape"],
    ):
        assert main(args) == 2
        messages.append(capsys.readouterr().err.strip())
    assert all(m.startswith("shape: error: file not found: ") for m in messages), messages
    assert all("Errno" not in m for m in messages), messages


def test_compatibility_says_it_needs_model_artifacts(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["compatibility", "p.shape", "p.shape"]) == 2
    err = capsys.readouterr().err
    assert "model" in err and "profile" in err, err


def test_unknown_registry_ref_names_the_ref(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "reg", "checkout", "people", "nope"]) == 2
    assert "people@nope is not recorded in the registry" in capsys.readouterr().err


def test_debug_flag_shows_the_traceback(work: Path) -> None:
    from shape.registry.local import RegistryError

    with pytest.raises(RegistryError):
        main(["--debug", "registry", "reg", "checkout", "people", "nope"])


def test_debug_environment_shows_the_traceback(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shape.registry.local import RegistryError

    monkeypatch.setenv("SHAPE_DEBUG", "1")
    with pytest.raises(RegistryError):
        main(["registry", "reg", "checkout", "people", "nope"])


def test_programming_errors_still_raise(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only expected error types become a message; a bug is not hidden."""
    import importlib

    cli = importlib.import_module("shape.cli.main")

    def boom(_a: object) -> int:
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(cli, "_cmd_inspect", boom)
    with pytest.raises(ZeroDivisionError):
        main(["inspect", "p.shape"])


def test_registry_commit_without_a_path_is_a_usage_error(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["registry", "reg", "commit", "x"])
    assert exc.value.code == 2
    assert "ARTIFACT" in capsys.readouterr().err

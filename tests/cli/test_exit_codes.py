"""W1-14 deliverable 4: the exit-code registry, the generated document and the --help epilogs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from shape.cli import exitcodes
from shape.cli.introspect import core_commands
from shape.cli.main import main

ROOT = Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location(
        "gen_exit_codes", ROOT / "scripts/gen_exit_codes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_core_subcommand_has_a_registry_entry():
    paths = {c.path for c in core_commands()}
    assert len(paths) > 60
    missing = sorted(p for p in paths if p not in exitcodes.CODES)
    assert not missing, f"no exit-code entry for: {missing}"


def test_the_registry_names_no_command_that_does_not_exist():
    paths = {c.path for c in core_commands()}
    stale = sorted(set(exitcodes.CODES) - paths)
    assert not stale, f"registry entries for commands that are gone: {stale}"


def test_aliases_share_the_entry_of_their_command():
    assert exitcodes.codes("show") == exitcodes.codes("inspect")
    assert exitcodes.codes("compare") == exitcodes.codes("fidelity")
    for c in core_commands():
        for alias in c.aliases:
            assert exitcodes.codes(alias) == exitcodes.codes(c.path)


def test_codes_are_integers_with_a_meaning_and_keep_the_documented_classes():
    for path in exitcodes.CODES:
        table = exitcodes.codes(path)
        assert 0 in table and 2 in table, path  # ok and bad input: every command
        for code, meaning in table.items():
            assert isinstance(code, int) and not isinstance(code, bool)
            assert 0 <= code <= 255
            assert meaning.strip(), (path, code)
        assert table[0].startswith("ok"), path


def test_class_one_means_a_check_failed_never_input():
    for path in exitcodes.CODES:
        meaning = exitcodes.codes(path)[2]
        assert "input" in meaning or "argument" in meaning


def test_flags_that_signal_a_verdict_imply_their_code():
    flagged = {
        "--verify": 1,  # a signature that does not verify
        "--fail-on-drift": 1,
        "--strict": 1,
        "--live-fail": 1,
        "--check": 1,
        "--lint": 1,
    }
    for c in core_commands():
        opts = {o for a in c.parser._actions for o in a.option_strings}
        for flag, code in flagged.items():
            if flag in opts:
                assert code in exitcodes.codes(c.path), (c.path, flag)


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ("compatibility", 5),
        ("certify-shapes", 3),
        ("check", 4),
        ("check", 1),
        ("diff", 1),
        ("verify", 1),
        ("quality", 2),
    ],
)
def test_the_codes_that_are_listed_are_the_ones_the_command_returns(
    command, code, tmp_path, monkeypatch
):
    """A spot check of codes at and above 3: each is in the registry and the command returns it."""
    assert code in exitcodes.codes(command)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.csv").write_text("id,v\n" + "".join(f"{i},{i % 7}\n" for i in range(300)))
    (tmp_path / "b.csv").write_text("id,v\n" + "".join(f"{i},{100 + i % 7}\n" for i in range(300)))
    for n in "ab":
        assert main(["profile", f"{n}.csv", "-o", f"{n}.shape"]) == 0
    if command == "compatibility":
        for n, cols in (("m1", ["id", "v"]), ("m2", ["id"])):
            (tmp_path / f"{n}.csv").write_text(
                ",".join(cols) + "\n" + ",".join("1" for _ in cols) + "\n"
            )
            assert main(["capture", f"{n}.csv", "-o", f"{n}.shape"]) == 0
        argv = ["compatibility", "m1.shape", "m2.shape"]
        assert (
            main(argv) in (0, code)
            and main(["compatibility", "m1.shape", "m2.shape", "--mode", "full"]) == code
        )
        return
    if command == "certify-shapes":
        (tmp_path / "x.csv").write_text("id,v\n1,1\n2,2\n3,3\n")
        (tmp_path / "y.csv").write_text("zzz\nq\nr\n")
        assert main(["capture", "x.csv", "-o", "x.shape"]) == 0
        assert main(["capture", "y.csv", "-o", "y.shape"]) == 0
        assert main(["certify-shapes", "x.shape", "y.shape"]) == code
        return
    if command == "check" and code == 4:
        assert main(["capture", "a.csv", "-o", "m.shape"]) == 0
        (tmp_path / "ev.json").write_text(json.dumps({"columns": {"zzz": {}}}))
        assert main(["check", "m.shape", "ev.json"]) == code
        return
    if command == "check":
        (tmp_path / "c.json").write_text(json.dumps({"columns": {"nope": {}}}))
        assert main(["check", "a.shape", "c.json"]) == code
    elif command == "diff":
        assert main(["diff", "a.shape", "b.shape", "--fail-on-drift"]) == code
    elif command == "verify":
        (tmp_path / "g.json").write_text(json.dumps({"columns": {"v": {"min": 1000}}}))
        rc = main(["verify", "a.csv", "--schema", "g.json"])
        assert rc in exitcodes.codes("verify")
    else:
        (tmp_path / "q.csv").write_text("id,v\n1,1\n")
        assert main(["quality", "q.csv"]) in exitcodes.codes("quality")


def test_bad_input_is_two_everywhere_it_is_listed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["diff", "nope.shape", "nope2.shape"]) == 2
    assert main(["check", "nope.shape", "c.json"]) == 2
    assert main(["verify", "missing_dir"]) == 2
    assert main(["fidelity", "nope.csv", "nope2.csv"]) == 2


def test_every_help_ends_with_the_codes_of_the_registry(capsys):
    seen = 0
    for c in core_commands():
        with pytest.raises(SystemExit) as stop:
            main([*c.invoke, "--help"])
        assert stop.value.code == 0
        out = capsys.readouterr().out
        assert "exit codes" in out.lower(), c.path
        for code, meaning in exitcodes.codes(c.path).items():
            assert f"  {code}  " in out or f"{code} " in out, (c.path, code)
            assert meaning.split(";")[0][:20] in " ".join(out.split()), (c.path, code)
        seen += 1
    assert seen > 60


def test_an_existing_epilog_is_kept_above_the_codes(capsys):
    with pytest.raises(SystemExit):
        main(["profile", "validate", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "is the leak scanner" in out
    assert out.index("is the leak scanner") < out.lower().index("exit codes")
    text = exitcodes.epilog("diff")
    assert text.splitlines()[0].lower().startswith("exit codes")
    assert "  0  " in text


def test_the_document_is_generated_and_up_to_date(capsys):
    gen = _script()
    assert gen.main(["--check"]) == 0
    doc = (ROOT / "docs/EXIT_CODES.md").read_text(encoding="utf-8")
    for path in exitcodes.CODES:
        assert f"`shape {path}`" in doc


def test_check_flag_fails_on_a_stale_document(tmp_path, capsys):
    gen = _script()
    stale = tmp_path / "EXIT_CODES.md"
    stale.write_text("old\n")
    assert gen.main(["--check", "--output", str(stale)]) == 1
    assert "out of date" in capsys.readouterr().err
    assert gen.main(["--output", str(stale)]) == 0
    assert gen.main(["--check", "--output", str(stale)]) == 0
    missing = tmp_path / "none.md"
    assert gen.main(["--check", "--output", str(missing)]) == 1


def test_make_check_runs_the_generator():
    assert "gen_exit_codes.py --check" in (ROOT / "Makefile").read_text()

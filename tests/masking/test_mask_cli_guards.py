"""``shape mask`` guards (0.9.4, plan 4.3): a non-empty output folder, ``--seed`` with a key, and
no hint for a flag ``mask`` does not have."""

from __future__ import annotations

import json
import warnings

import pytest

from shape.cli.main import main

KEY = "0123456789abcdef0123456789abcdef"


def write_csv(d):
    d.mkdir()
    (d / "orders.csv").write_text(
        "id,email,salary\n" + "".join(f"{i},u{i}@corp.io,{10000 + i}\n" for i in range(30))
    )


def one_error_line(capsys):
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1, err
    assert err[0].startswith("shape: error:")
    return err[0]


def test_a_non_empty_output_folder_is_refused(tmp_path, capsys):
    write_csv(tmp_path / "in")
    out = tmp_path / "out"
    out.mkdir()
    (out / "orders.csv").write_text("keep me\n")
    assert main(["mask", str(tmp_path / "in"), "-o", str(out)]) == 2
    assert "not an empty folder" in one_error_line(capsys)
    assert (out / "orders.csv").read_text() == "keep me\n"


def test_an_output_path_that_is_a_file_is_refused(tmp_path, capsys):
    write_csv(tmp_path / "in")
    out = tmp_path / "out"
    out.write_text("x")
    assert main(["mask", str(tmp_path / "in"), "-o", str(out)]) == 2
    one_error_line(capsys)


def test_a_new_or_empty_output_folder_is_accepted(tmp_path):
    write_csv(tmp_path / "in")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["mask", str(tmp_path / "in"), "-o", str(empty)]) == 0
    assert main(["mask", str(tmp_path / "in"), "-o", str(tmp_path / "new")]) == 0


@pytest.mark.parametrize("option", ["--key-file", "--key-env"])
def test_seed_with_a_key_is_rejected(tmp_path, monkeypatch, capsys, option):
    write_csv(tmp_path / "in")
    if option == "--key-file":
        value = tmp_path / "k.key"
        value.write_text(KEY)
        value.chmod(0o600)
    else:
        monkeypatch.setenv("MASK_KEY_SEED_TEST", KEY)
        value = "MASK_KEY_SEED_TEST"
    argv = ["mask", str(tmp_path / "in"), "-o", str(tmp_path / "o"), option, str(value)]
    assert main([*argv, "--seed", "7"]) == 2
    assert "--seed" in one_error_line(capsys)
    assert not (tmp_path / "o").exists()
    assert main(argv) == 0


def test_the_csv_read_does_not_advise_a_flag_mask_lacks(tmp_path, capsys):
    write_csv(tmp_path / "in")
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert main(["mask", str(tmp_path / "in"), "-o", str(tmp_path / "o")]) == 0
    captured = capsys.readouterr()
    assert not [w for w in seen if "--string-columns" in str(w.message)]
    assert "--string-columns" not in captured.out + captured.err


@pytest.mark.parametrize("existing_output", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
def test_dry_run_plans_output_without_writing(tmp_path, capsys, existing_output, json_output):
    write_csv(tmp_path / "in")
    out = tmp_path / "out"
    if existing_output:
        out.mkdir()
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    argv = ["mask", str(tmp_path / "in"), "-o", str(out), "--dry-run"]
    if json_output:
        argv.append("--json")

    assert main(argv) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    action = "write" if existing_output else "create"
    if json_output:
        assert json.loads(captured.out) == {
            "format": "shape-dry-run",
            "version": 1,
            "command": "mask",
            "actions": [{"action": action, "target": str(out)}],
        }
    else:
        assert captured.out.split() == ["would", action, str(out)]
    assert out.exists() is existing_output
    if existing_output:
        assert list(out.iterdir()) == []
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before

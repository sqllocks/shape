"""W5-05 item 5 on the command line: `shape seed`."""

from __future__ import annotations

import json

import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_dry_run_prints_tables_rows_and_order_and_connects_to_nothing(capsys):
    code, out, err = run(
        capsys, "seed", "retail", "--target", "postgres://nobody@no.such.host.invalid/db",
        "--scale", "fabric_demo", "--dry-run",
    )  # fmt: skip
    assert code == 0 and err == ""
    lines = out.splitlines()
    assert "mode create" in lines[0] and "scale fabric_demo" in lines[0]
    assert lines[1].split() == ["1.", "customer:", "200", "rows"]
    assert "order: 1,000 rows" in out and "4,670 rows in 9 tables" in out
    assert out.rstrip().endswith("dry run: nothing was connected to or written")


def test_dry_run_json_and_a_password_in_the_uri_is_never_printed(capsys):
    code, out, _ = run(
        capsys, "seed", "retail", "--target", "mysql://u:hunter2@h/db", "--dry-run", "--json",
        "--scale", "tiny", "--mode", "append", "--seed", 4,
    )  # fmt: skip
    doc = json.loads(out)
    assert code == 0 and doc["dry_run"] is True and "hunter2" not in out
    assert doc["plan"]["sink"] == "mysql" and doc["plan"]["seed"] == 4
    assert doc["plan"]["mode"] == "append" and doc["plan"]["rows"] == 900
    assert [t["table"] for t in doc["plan"]["tables"]][:2] == ["customer", "address"]


def test_a_script_target_seeds_then_refuses_then_truncates(capsys, tmp_path):
    out_dir = tmp_path / "scripts"
    target = f"sql://{out_dir}"
    code, out, _ = run(capsys, "seed", "retail", "--target", target, "--scale", "tiny", "--seed", 2)
    assert code == 0 and "seeded 900 rows into 9 tables" in out
    first = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    assert sorted(first)[0] == "01_customer.sql" and len(first) == 9
    code, out, _ = run(capsys, "seed", "retail", "--target", target, "--scale", "tiny", "--seed", 3)
    assert code == 1 and "seed refused" in out and "nothing was written" in out
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == first
    code, _, _ = run(
        capsys, "seed", "retail", "--target", target, "--scale", "tiny", "--seed", 2,
        "--mode", "truncate",
    )  # fmt: skip
    assert code == 0
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} != first  # drop + create added
    again = {p.name: p.read_bytes() for p in out_dir.iterdir()}
    run(
        capsys, "seed", "retail", "--target", target, "--scale", "tiny", "--seed", 2,
        "--mode", "truncate",
    )  # fmt: skip
    assert {p.name: p.read_bytes() for p in out_dir.iterdir()} == again


def test_a_refusal_in_json_mode_is_json(capsys, tmp_path):
    target = f"sql://{tmp_path}"
    run(capsys, "seed", "retail", "--target", target, "--scale", "tiny")
    code, out, _ = run(capsys, "seed", "retail", "--target", target, "--scale", "tiny", "--json")
    assert code == 1 and "nothing was written" in json.loads(out)["refused"]


def test_bad_input_exits_two_with_a_one_line_error(capsys, tmp_path):
    cases = [
        (("seed", "retail", "--target", "oracle://h/d"), "cannot seed"),
        (("seed", "retail", "--target", "postgres://h/d", "--scale", "huge", "--dry-run"), "huge"),
        (("seed", "nodomain", "--target", f"sql://{tmp_path}"), "nodomain"),
        (("seed", "retail", "--target", "sql://"), "give the directory"),
        (("seed", "retail", "--target", f"sql://{tmp_path}?dialect=oracle"), "unknown dialect"),
    ]
    for argv, text in cases:
        code, out, err = run(capsys, *argv)
        assert code == 2 and out == "" and err.startswith("shape: error:") and text in err, argv


def test_a_database_target_without_its_sink_installed_says_how_to_install_it(capsys, monkeypatch):
    class Host:
        def get(self, group, name):
            raise KeyError(name)

    monkeypatch.setattr("shape.plugins.host.default_host", lambda: Host())
    code, _, err = run(capsys, "seed", "retail", "--target", "postgres://h/db", "--scale", "tiny")
    assert (
        code == 2 and "no postgres sink is installed" in err and "sqllocks-shape[postgres]" in err
    )


def test_arguments_are_checked_by_the_parser(capsys):
    for argv in (
        ("seed", "retail"),  # no --target
        ("seed", "retail", "--target", "sql://x", "--mode", "replace"),
    ):
        with pytest.raises(SystemExit) as exc:
            main(list(argv))
        assert exc.value.code == 2
    capsys.readouterr()

"""W5-01 (#62): keyed ``shape mask`` — referential consistency across files and runs, and the
key never appearing beside the output."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest

from shape.builtins.transforms import MaskConfig, MaskError, mask_tables
from shape.cli.main import main

KEY = "0123456789abcdef0123456789abcdef"


def write_inputs(d):
    d.mkdir()
    (d / "customers.csv").write_text(
        "customer_id,email,phone,first_name,last_name,dob\n"
        + "".join(
            f"{100 + i},user{i}@corp.io,(555) 20{i % 10}-{3000 + i},Name{i},Fam{i},"
            f"19{70 + i % 30}-01-{1 + i % 28:02d}\n"
            for i in range(60)
        )
    )
    (d / "orders.csv").write_text(
        "order_id,customer_id,contact_email\n"
        + "".join(f"{i},{100 + i % 60},user{i % 60}@corp.io\n" for i in range(120))
    )


def run(tmp_path, out, *extra):
    return main(["mask", str(tmp_path / "in"), "-o", str(out), *extra])


def test_keyed_runs_are_identical_and_other_key_differs(tmp_path):
    write_inputs(tmp_path / "in")
    k1 = tmp_path / "k1.key"
    k1.write_text(KEY)
    k1.chmod(0o600)
    k2 = tmp_path / "k2.key"
    k2.write_text(KEY[::-1])
    k2.chmod(0o600)
    assert run(tmp_path, tmp_path / "a", "--key-file", str(k1)) == 0
    assert run(tmp_path, tmp_path / "b", "--key-file", str(k1)) == 0
    assert run(tmp_path, tmp_path / "c", "--key-file", str(k2)) == 0
    for name in ("customers.csv", "orders.csv"):
        a = (tmp_path / "a" / name).read_bytes()
        assert a == (tmp_path / "b" / name).read_bytes()
        assert a != (tmp_path / "c" / name).read_bytes()


def test_keyed_masks_are_stable_when_other_rows_change(tmp_path):
    """The mask of a value does not depend on the rest of the data (the point of keyed mode)."""
    write_inputs(tmp_path / "in")
    k = tmp_path / "k.key"
    k.write_text(KEY)
    k.chmod(0o600)
    assert run(tmp_path, tmp_path / "full", "--key-file", str(k)) == 0
    lines = (tmp_path / "in" / "customers.csv").read_text().splitlines()
    (tmp_path / "in" / "customers.csv").write_text("\n".join(lines[:21]) + "\n")
    assert run(tmp_path, tmp_path / "part", "--key-file", str(k)) == 0
    full = (tmp_path / "full" / "customers.csv").read_text().splitlines()
    part = (tmp_path / "part" / "customers.csv").read_text().splitlines()
    assert part == full[:21]


def test_orders_follow_customers_and_uses_reserved_email_domains(tmp_path, monkeypatch):
    write_inputs(tmp_path / "in")
    monkeypatch.setenv("MASK_KEY_TEST", KEY)
    assert run(tmp_path, tmp_path / "o", "--key-env", "MASK_KEY_TEST", "--json") == 0
    import csv

    cust = list(csv.DictReader((tmp_path / "o" / "customers.csv").open()))
    orders = list(csv.DictReader((tmp_path / "o" / "orders.csv").open()))
    ids = {r["customer_id"] for r in cust}
    assert {r["customer_id"] for r in orders} <= ids
    emails = {r["email"] for r in cust}
    assert {r["contact_email"] for r in orders} <= emails
    assert all(e.endswith(("@example.com", "@example.org", "@example.net")) for e in emails)


def test_key_is_not_in_output_and_key_file_inside_output_is_refused(tmp_path, capsys):
    write_inputs(tmp_path / "in")
    k = tmp_path / "k.key"
    k.write_text(KEY)
    k.chmod(0o600)
    assert run(tmp_path, tmp_path / "o", "--key-file", str(k), "--json") == 0
    printed = capsys.readouterr().out
    assert KEY not in printed and json.loads(printed)["keyed"] is True
    for f in (tmp_path / "o").iterdir():
        assert KEY.encode() not in f.read_bytes()
    out = tmp_path / "o2"
    out.mkdir()
    inside = out / "k.key"
    inside.write_text(KEY)
    inside.chmod(0o600)
    assert run(tmp_path, out, "--key-file", str(inside)) == 2


def test_bad_keys_exit_2(tmp_path, monkeypatch):
    write_inputs(tmp_path / "in")
    short = tmp_path / "s.key"
    short.write_text("short")
    short.chmod(0o600)
    assert run(tmp_path, tmp_path / "o", "--key-file", str(short)) == 2
    monkeypatch.delenv("NO_SUCH_KEY_VAR", raising=False)
    assert run(tmp_path, tmp_path / "o", "--key-env", "NO_SUCH_KEY_VAR") == 2
    loose = tmp_path / "l.key"
    loose.write_text(KEY)
    loose.chmod(0o644)
    assert run(tmp_path, tmp_path / "o", "--key-file", str(loose)) == 2


def test_both_key_options_are_refused(tmp_path):
    write_inputs(tmp_path / "in")
    with pytest.raises(SystemExit):
        run(tmp_path, tmp_path / "o", "--key-file", "a", "--key-env", "B")


def test_unkeyed_mode_still_uses_reserved_email_domains():
    t = pa.table({"email": [f"u{i}@corp.io" for i in range(200)]})
    out = mask_tables({"t": t}, MaskConfig(seed=1)).tables["t"]["email"].to_pylist()
    assert all(e.endswith(("@example.com", "@example.org", "@example.net")) for e in out)


def test_short_key_in_config_is_a_mask_error():
    with pytest.raises(MaskError):
        mask_tables({"t": pa.table({"email": ["a@b.io"]})}, MaskConfig(key=b"short"))

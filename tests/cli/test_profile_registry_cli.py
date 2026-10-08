"""P6-10: ``shape profile export|import|list|validate`` and ``shape profile registry ...``.

Every subcommand runs end to end through the real CLI in a subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import shape


def cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def _csv(path: Path, rows: int = 120, extra: bool = False, nulls: int = 0) -> Path:
    head = "id,status,amount" + (",extra" if extra else "")
    lines = [head]
    for i in range(rows):
        status = "" if i < nulls else ["new", "paid", "shipped"][i % 3]
        lines.append(f"{i},{status},{i * 1.5}" + (",x" if extra else ""))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def orders(tmp_path: Path) -> Path:
    return _csv(tmp_path / "orders.csv")


@pytest.fixture()
def profile_file(tmp_path: Path, orders: Path) -> Path:
    out = tmp_path / "orders.shape"
    shape.save(shape.profile(str(orders)), str(out))
    return out


@pytest.fixture()
def root(tmp_path: Path) -> str:
    return str(tmp_path / "reg")


def _save(root: str, src: Path, *extra: str, name: str = "v1") -> subprocess.CompletedProcess[str]:
    return cli(
        "profile",
        "registry",
        "save",
        str(src),
        "--system",
        "crm",
        "--name",
        name,
        "--root",
        root,
        *extra,
    )


# -- export / import / list / validate ----------------------------------------------------


def test_export_import_roundtrip(tmp_path: Path, profile_file: Path):
    out = tmp_path / "orders.json"
    r = cli("profile", "export", str(profile_file), "-o", str(out))
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text())
    assert doc["format"] == "shape-profile"
    back = tmp_path / "back.shape"
    r = cli("profile", "import", str(out), "-o", str(back), "--name", "renamed")
    assert r.returncode == 0, r.stderr
    a, b = shape.load(str(profile_file)), shape.load(str(back))
    assert b.name == "renamed"
    assert a.to_dict() == b.to_dict()


def test_import_rejects_a_foreign_json_file(tmp_path: Path):
    foreign = tmp_path / "other.json"
    foreign.write_text(json.dumps({"name": "x", "distributions": {}, "ratios": {}}))
    r = cli("profile", "import", str(foreign), "-o", str(tmp_path / "o.shape"))
    assert r.returncode == 2
    assert "not a Shape profile export" in r.stderr
    assert not (tmp_path / "o.shape").exists()


def test_import_rejects_malformed_profile_body(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"format": "shape-profile", "format_version": 1, "profile": {"columns": 3}})
    )
    assert cli("profile", "import", str(bad), "-o", str(tmp_path / "o.shape")).returncode == 2


def test_list_shows_profiles_and_reports_what_it_skips(tmp_path: Path, profile_file: Path):
    (tmp_path / "junk.shape").write_bytes(b"not a zip")
    r = cli("profile", "list", str(tmp_path), "--json")
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert [p["file"] for p in doc["profiles"]] == ["orders.shape"]
    assert doc["profiles"][0]["tables"] == {"orders": 120}
    assert doc["skipped"] and doc["skipped"][0].startswith("junk.shape")
    assert "skipped junk.shape" in r.stderr
    assert "orders.shape" in cli("profile", "list", str(tmp_path)).stdout


def test_validate_accepts_shape_and_export_and_rejects_bad_files(
    tmp_path: Path, profile_file: Path
):
    assert cli("profile", "validate", str(profile_file)).returncode == 0
    exp = tmp_path / "p.json"
    cli("profile", "export", str(profile_file), "-o", str(exp))
    assert cli("profile", "validate", str(exp)).returncode == 0
    tampered = tmp_path / "t.shape"
    raw = bytearray(profile_file.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    tampered.write_bytes(bytes(raw))
    r = cli("profile", "validate", str(tampered), "--json")
    assert r.returncode == 1
    assert json.loads(r.stdout)["valid"] is False
    assert cli("profile", "validate", str(tmp_path / "missing.json")).returncode == 1


def test_validate_safe_still_routes_to_the_leak_scanner(profile_file: Path, tmp_path: Path):
    safe = tmp_path / "safe.json"
    assert cli("profile", "safe", str(profile_file), "-o", str(safe)).returncode == 0
    r = cli("profile", "validate", "--safe", str(safe))
    assert r.returncode == 0 and "CLEAN" in r.stdout


def test_profiling_a_file_still_works(tmp_path: Path, orders: Path):
    out = tmp_path / "o.shape"
    assert cli("profile", str(orders), "-o", str(out)).returncode == 0
    assert out.exists()


# -- registry -------------------------------------------------------------------------------


def test_registry_save_list_roundtrip(orders: Path, root: str):
    r = _save(root, orders, "--tags", "prod, daily", "--description", "daily orders")
    assert r.returncode == 0, r.stderr
    assert "crm/orders/v1" in r.stdout
    r = cli("profile", "registry", "list", "--root", root, "--json")
    (entry,) = json.loads(r.stdout)["payload"]
    assert entry["tags"] == ["daily", "prod"]
    assert entry["source_rows"] == 120
    assert entry["description"] == "daily orders"
    assert cli("profile", "registry", "list", "--root", root, "--tag", "nope").stdout.startswith(
        "No profiles"
    )
    assert (
        "crm/orders/v1"
        in cli("profile", "registry", "list", "--root", root, "--system", "crm").stdout
    )
    assert (
        shape.load(str(Path(root) / "crm" / "orders" / "v1.shape")).tables["orders"]["row_count"]
        == 120
    )


def test_registry_save_from_a_shape_profile_and_multi_table(
    tmp_path: Path, profile_file: Path, root: str
):
    assert _save(root, profile_file).returncode == 0
    d = tmp_path / "data"
    d.mkdir()
    _csv(d / "a.csv")
    _csv(d / "b.csv")
    ds = tmp_path / "ds.shape"
    shape.save(shape.profile({"a": str(d / "a.csv"), "b": str(d / "b.csv")}), str(ds))
    r = _save(root, ds, name="v2")
    assert r.returncode == 0, r.stderr
    idents = [
        e["table"]
        for e in json.loads(cli("profile", "registry", "list", "--root", root, "--json").stdout)[
            "payload"
        ]
    ]
    assert sorted(idents) == ["a", "b", "orders"]


def test_registry_save_never_replaces_silently(orders: Path, tmp_path: Path, root: str):
    assert _save(root, orders).returncode == 0
    bigger = _csv(tmp_path / "orders.csv", rows=300)
    r = _save(root, bigger)
    assert r.returncode == 2 and "--overwrite" in r.stderr
    assert (
        json.loads(cli("profile", "registry", "list", "--root", root, "--json").stdout)["payload"][
            0
        ]["source_rows"]
        == 120
    )
    assert _save(root, bigger, "--overwrite").returncode == 0
    assert (
        json.loads(cli("profile", "registry", "list", "--root", root, "--json").stdout)["payload"][
            0
        ]["source_rows"]
        == 300
    )


@pytest.mark.parametrize("system", ["..", "a/b", "../x", ".hidden", "a b"])
def test_registry_rejects_names_that_could_leave_the_root(orders: Path, root: str, system: str):
    r = cli(
        "profile",
        "registry",
        "save",
        str(orders),
        "--system",
        system,
        "--name",
        "n",
        "--root",
        root,
    )
    assert r.returncode == 2
    assert not list(Path(root).parent.glob("**/*.shape"))


def test_registry_delete(orders: Path, root: str):
    _save(root, orders)
    r = cli("profile", "registry", "delete", "crm/orders/v1", "--root", root)
    assert r.returncode == 0 and "Deleted" in r.stdout
    assert not (Path(root) / "crm").exists()  # empty folders go too
    assert (
        json.loads(cli("profile", "registry", "list", "--root", root, "--json").stdout)["payload"]
        == []
    )
    assert cli("profile", "registry", "delete", "crm/orders/v1", "--root", root).returncode == 2
    assert cli("profile", "registry", "delete", "../../etc/passwd", "--root", root).returncode == 2


def test_registry_tag_add_and_remove(orders: Path, root: str):
    _save(root, orders, "--tags", "a")
    r = cli("profile", "registry", "tag", "crm/orders/v1", "b", "c", "--root", root)
    assert r.returncode == 0, r.stderr

    def tags() -> list[str]:
        out = cli("profile", "registry", "list", "--root", root, "--json").stdout
        return json.loads(out)["payload"][0]["tags"]

    assert tags() == ["a", "b", "c"]
    assert (
        cli(
            "profile", "registry", "tag", "crm/orders/v1", "a", "--remove", "--root", root
        ).returncode
        == 0
    )
    assert tags() == ["b", "c"]
    # the tags live in the file, so a reindex keeps them and the profile is intact
    cli("profile", "registry", "reindex", "--root", root)
    assert tags() == ["b", "c"]
    assert cli("profile", "registry", "tag", "crm/orders/nope", "x", "--root", root).returncode == 2


def test_registry_diff(orders: Path, tmp_path: Path, root: str):
    _save(root, orders)
    other = _csv(tmp_path / "orders.csv", rows=200, extra=True, nulls=30)
    assert _save(root, other, name="v2").returncode == 0
    r = cli(
        "profile",
        "registry",
        "diff",
        "crm/orders/v1",
        "crm/orders/v2",
        "--json",
        "--fail-on-diff",
        "--root",
        root,
    )
    assert r.returncode == 1
    d = json.loads(r.stdout)
    assert d["added"] == ["extra"] and d["removed"] == []
    assert "null_rate" in d["changed"]["status"]
    assert d["rows"] == {"from": 120, "to": 200}
    same = cli(
        "profile",
        "registry",
        "diff",
        "crm/orders/v1",
        "crm/orders/v1",
        "--fail-on-diff",
        "--root",
        root,
    )
    assert same.returncode == 0 and "identical" in same.stdout


def test_registry_reindex_rebuilds_and_reports_skips(orders: Path, root: str):
    _save(root, orders)
    (Path(root) / "_index.json").unlink()
    bad = Path(root) / "crm" / "orders" / "broken.shape"
    bad.write_bytes(b"junk")
    r = cli("profile", "registry", "reindex", "--root", root)
    assert r.returncode == 0
    assert "Reindexed 1 profile(s)" in r.stdout
    assert "skipped crm/orders/broken.shape" in r.stderr
    assert (
        len(
            json.loads(cli("profile", "registry", "list", "--root", root, "--json").stdout)[
                "payload"
            ]
        )
        == 1
    )


def test_registry_validate_store_and_data(orders: Path, tmp_path: Path, root: str):
    _save(root, orders)
    assert cli("profile", "registry", "validate", "--root", root).returncode == 0
    assert (
        cli(
            "profile",
            "registry",
            "validate",
            "crm/orders/v1",
            "--data",
            str(orders),
            "--root",
            root,
        ).returncode
        == 0
    )
    drifted = _csv(tmp_path / "d.csv", extra=True, nulls=60)
    r = cli(
        "profile",
        "registry",
        "validate",
        "crm/orders/v1",
        "--data",
        str(drifted),
        "--json",
        "--root",
        root,
    )
    assert r.returncode == 1
    problems = json.loads(r.stdout)["problems"]
    assert any("extra" in p for p in problems) and any("null rate" in p for p in problems)
    # a damaged file and a stale index are found
    (Path(root) / "crm" / "orders" / "v1.shape").write_bytes(b"junk")
    assert cli("profile", "registry", "validate", "--root", root).returncode == 1
    assert cli("profile", "registry", "validate", "crm/orders/nope", "--root", root).returncode == 2


def test_registry_default_root_comes_from_the_environment(orders: Path, tmp_path: Path):
    env = {"SHAPE_PROFILE_REGISTRY": str(tmp_path / "envreg")}
    r = cli("profile", "registry", "save", str(orders), "--system", "s", "--name", "n", env=env)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "envreg" / "s" / "orders" / "n.shape").exists()


def test_shape_registry_keeps_its_meaning(tmp_path: Path):
    reg = tmp_path / "artifacts"
    art = tmp_path / "x.bin"
    art.write_bytes(b"payload")
    r = cli("registry", str(reg), "commit", "thing", str(art))
    assert r.returncode == 0, r.stderr
    assert (reg / "objects").is_dir() and not (reg / "_index.json").exists()

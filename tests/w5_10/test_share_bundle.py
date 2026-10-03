"""W5-10 items 3 and 4: the safe-to-share bundle (create, verify)."""

from __future__ import annotations

import base64
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape import share_bundle
from shape.cli.main import main
from shape.repro import dataset_id

SECRET = "Zyx-Quillon-Top-Secret-4821"  # the planted source value: it must never be written out
N = 120
CLASSES = {"people.name": "CONFIDENTIAL", "people.email": "SECRET", "people.city": "INTERNAL"}


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def source_table() -> pa.Table:
    """``name``: SECRET x40, then 'Common-B' x25, 'Common-C' x15, the rest unique."""
    names = [SECRET] * 40 + ["Common-B"] * 25 + ["Common-C"] * 15
    names += [f"Unique Person {i}" for i in range(N - len(names))]
    rng = np.random.default_rng(1)
    return pa.table(
        {
            "name": names,
            "email": [f"real{i}@source.example" for i in range(N)],
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], N).tolist(),
            "age": rng.integers(18, 90, N).tolist(),
        }
    )


def independent_table(names: list[str] | None = None) -> pa.Table:
    rng = np.random.default_rng(99)
    return pa.table(
        {
            "name": names or [f"Synthetic {i}" for i in range(N)],
            "email": [f"gen{i}@synthetic.example" for i in range(N)],
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], N).tolist(),
            "age": rng.integers(18, 90, N).tolist(),
        }
    )


@pytest.fixture
def world(tmp_path):
    """source/, data/ (independent), a classifications file and an output path."""
    src, data = tmp_path / "source", tmp_path / "data"
    src.mkdir()
    data.mkdir()
    pq.write_table(source_table(), src / "people.parquet")
    pq.write_table(independent_table(), data / "people.parquet")
    classes = tmp_path / "classes.json"
    classes.write_text(json.dumps(CLASSES))
    return {
        "src": src,
        "data": data,
        "classes": classes,
        "out": tmp_path / "bundle.zip",
        "tmp": tmp_path,
    }


def create(capsys, w, *extra):
    return run(
        capsys,
        "share-bundle",
        "create",
        w["data"],
        "--source",
        w["src"],
        "--classifications",
        w["classes"],
        "-o",
        w["out"],
        *extra,
    )


def names_in(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def rewrite(path: Path, out: Path, change: dict[str, bytes | None]) -> Path:
    """A copy of the bundle with members replaced (bytes), removed (None) or added."""
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    for name, content in change.items():
        if content is None:
            members.pop(name)
        else:
            members[name] = content
    with zipfile.ZipFile(out, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return out


def key_files(tmp: Path, keypair) -> tuple[Path, Path]:
    sk, pk = tmp / "k.key", tmp / "k.pub"
    sk.write_text(base64.b64encode(keypair[0]).decode())
    pk.write_text(base64.b64encode(keypair[1]).decode())
    return sk, pk


# ---- create ---------------------------------------------------------------------------------


def test_create_writes_a_valid_bundle_for_independent_data(capsys, world):
    code, out, err = create(capsys, world)
    assert code == 0, err
    assert names_in(world["out"]) == ["attestation.json", "data/people.parquet"]
    att = json.loads(zipfile.ZipFile(world["out"]).read("attestation.json"))
    assert att["format"] == "shape-share-attestation" and att["version"] == 1
    assert att["dataset_id"] == dataset_id({"people": independent_table()})
    assert [c["name"] for c in att["checks"]] == ["memorization", "top_values"]
    assert all(c["passed"] for c in att["checks"])
    from shape import __version__

    assert att["shape_version"] == __version__
    assert att["key_id"] is None and att["signature"] is None
    top = att["checks"][1]
    assert top["parameters"] == {"top_k": 20, "min_level": "CONFIDENTIAL"}
    assert {(c["column"], c["matches"]) for c in top["counts"]["columns"]} == {
        ("name", 0),
        ("email", 0),
    }  # INTERNAL city is not a restricted column
    assert run(capsys, "share-bundle", "verify", world["out"])[0] == 0


def test_the_attestation_carries_counts_never_source_values(capsys, world):
    assert create(capsys, world)[0] == 0
    with zipfile.ZipFile(world["out"]) as zf:
        everything = b"".join(zf.read(n) for n in zf.namelist())
    for value in (SECRET, "Common-B", "real0@source.example"):
        assert value.encode() not in everything


def test_a_copied_top_value_fails_create_with_no_bundle_and_no_leak(capsys, world):
    names = [SECRET] + [f"Synthetic {i}" for i in range(N - 1)]  # one row copies the top value
    pq.write_table(independent_table(names), world["data"] / "people.parquet")
    world["out"].write_bytes(b"earlier bundle")
    code, out, err = create(capsys, world)
    assert code == 1
    assert world["out"].read_bytes() == b"earlier bundle"  # not replaced, not removed
    assert "top_values: people.name: 1 of the 20 most frequent source values" in err
    for text in (out, err):
        assert SECRET not in text
    others = [p for p in world["tmp"].rglob("*") if p.is_file() and p.parent != world["src"]]
    assert sorted(p.name for p in others) == ["bundle.zip", "classes.json", "people.parquet"]
    for p in others:
        if p.name != "people.parquet":
            assert SECRET.encode() not in p.read_bytes()


def test_no_bundle_is_written_when_the_output_did_not_exist(capsys, world):
    names = [SECRET] * 3 + [f"Synthetic {i}" for i in range(N - 3)]
    pq.write_table(independent_table(names), world["data"] / "people.parquet")
    assert create(capsys, world)[0] == 1
    assert not world["out"].exists()
    assert [p.name for p in world["tmp"].glob("*.tmp")] == []


def test_a_copied_row_fails_the_memorization_check(capsys, world):
    rows = source_table().take(list(range(40, 100)))  # unique-ish rows copied whole
    gen = pa.concat_tables([rows, independent_table().slice(0, 60)])
    pq.write_table(gen, world["data"] / "people.parquet")
    code, _, err = create(capsys, world)
    assert code == 1 and "memorization: people:" in err and "reproduce a source row" in err
    assert not world["out"].exists()


def test_only_the_top_values_check_fails_for_a_single_copied_value(world):
    names = [SECRET] + [f"Synthetic {i}" for i in range(N - 1)]
    pq.write_table(independent_table(names), world["data"] / "people.parquet")
    result = share_bundle.create(world["data"], world["src"], world["classes"], world["out"])
    assert not result.ok
    memo, top = result.attestation["checks"]
    assert memo["passed"] is True and top["passed"] is False
    assert len(result.problems) == 1 and result.problems[0].startswith("top_values:")


@pytest.mark.parametrize(("top_k", "ok"), [(1, True), (2, False)])
def test_top_k_boundary(world, top_k, ok):
    """'Common-B' is the source's second most frequent name (SECRET is first)."""
    names = ["Common-B"] + [f"Synthetic {i}" for i in range(N - 1)]
    pq.write_table(independent_table(names), world["data"] / "people.parquet")
    result = share_bundle.create(
        world["data"], world["src"], world["classes"], world["out"], top_k=top_k
    )
    assert result.ok is ok
    assert world["out"].exists() is ok


def test_a_value_in_a_column_below_confidential_is_not_checked(capsys, world):
    src = source_table()
    pq.write_table(src, world["src"] / "people.parquet")
    gen = independent_table().set_column(2, "city", pa.array(["Oslo"] * N))
    pq.write_table(gen, world["data"] / "people.parquet")
    assert create(capsys, world)[0] == 0


def test_create_is_deterministic(capsys, world):
    assert create(capsys, world)[0] == 0
    first = world["out"].read_bytes()
    assert create(capsys, world)[0] == 0
    assert world["out"].read_bytes() == first


def test_the_run_manifest_is_included_when_present(capsys, world, make_manifest):
    make_manifest(world["data"] / "run_manifest.json", {"people": independent_table()})
    assert create(capsys, world)[0] == 0
    assert names_in(world["out"]) == ["attestation.json", "data/people.parquet", "manifest.json"]
    att = json.loads(zipfile.ZipFile(world["out"]).read("attestation.json"))
    assert att["manifest_sha256"]
    assert run(capsys, "share-bundle", "verify", world["out"])[0] == 0


def test_two_manifests_are_refused(capsys, world, make_manifest):
    for name in ("a_manifest.json", "b_manifest.json"):
        make_manifest(world["data"] / name, {"people": independent_table()})
    code, _, err = create(capsys, world)
    assert code == 2 and "2 run manifests" in err and not world["out"].exists()


@pytest.mark.parametrize(
    "classes",
    [
        {"people.name": "NOT_A_LEVEL"},
        {"people.city": "PUBLIC"},  # nothing restricted: the checks would test nothing
        {"orders.name": "SECRET"},  # no such generated table
        ["people.name"],
        {"name": "SECRET"},
    ],
)
def test_bad_classifications_exit_2(capsys, world, classes):
    world["classes"].write_text(json.dumps(classes))
    code, _, err = create(capsys, world)
    assert code == 2 and "shape: error:" in err and not world["out"].exists()


def test_other_bad_input_exits_2(capsys, world):
    assert create(capsys, world, "--top-k", "0")[0] == 2
    world["classes"].write_text("{")
    assert create(capsys, world)[0] == 2
    world["classes"].write_text(json.dumps(CLASSES))
    (world["src"] / "people.parquet").rename(world["src"] / "other.parquet")
    code, _, err = create(capsys, world)
    assert code == 2 and "no generated table has a source table" in err
    assert (
        run(
            capsys,
            "share-bundle",
            "create",
            world["tmp"] / "nope",
            "--source",
            world["src"],
            "--classifications",
            world["classes"],
            "-o",
            world["out"],
        )[0]
        == 2
    )


def test_classifications_may_sit_under_a_key(capsys, world):
    world["classes"].write_text(json.dumps({"classifications": CLASSES}))
    assert create(capsys, world)[0] == 0


def test_there_is_no_allow_fail_option(capsys, world):
    with pytest.raises(SystemExit):
        main(
            [
                "share-bundle",
                "create",
                str(world["data"]),
                "--source",
                str(world["src"]),
                "--classifications",
                str(world["classes"]),
                "-o",
                str(world["out"]),
                "--allow-fail",
            ]
        )


def test_signing_without_the_sign_extra_exits_2_naming_it(capsys, world, monkeypatch):
    key = world["tmp"] / "k.key"
    key.write_text(base64.b64encode(bytes(range(32))).decode())
    monkeypatch.setitem(sys.modules, "cryptography", None)
    code, _, err = create(capsys, world, "--key", key)
    assert code == 2 and "sqllocks-shape[sign]" in err and not world["out"].exists()


# ---- signing --------------------------------------------------------------------------------


@pytest.mark.sign
def test_signed_bundle_verifies_with_the_right_key_only(capsys, world, keypair):
    from shape.artifact.signing import generate_keypair

    sk, pk = key_files(world["tmp"], keypair)
    assert create(capsys, world, "--key", sk)[0] == 0
    assert run(capsys, "share-bundle", "verify", world["out"], "--public-key", pk)[0] == 0
    code, out, _ = run(capsys, "share-bundle", "verify", world["out"])
    assert code == 0 and "signature not checked" in out
    other = world["tmp"] / "other.pub"
    other.write_text(base64.b64encode(generate_keypair()[1]).decode())
    code, _, err = run(capsys, "share-bundle", "verify", world["out"], "--public-key", other)
    assert code == 1 and "signature: invalid" in err


@pytest.mark.sign
def test_a_public_key_with_an_unsigned_bundle_fails(capsys, world, keypair):
    _, pk = key_files(world["tmp"], keypair)
    assert create(capsys, world)[0] == 0
    code, _, err = run(capsys, "share-bundle", "verify", world["out"], "--public-key", pk)
    assert code == 1 and "unsigned" in err


@pytest.mark.sign
def test_an_edited_attestation_fails_the_signature(capsys, world, keypair):
    sk, pk = key_files(world["tmp"], keypair)
    assert create(capsys, world, "--key", sk)[0] == 0
    att = json.loads(zipfile.ZipFile(world["out"]).read("attestation.json"))
    att["shape_version"] = "9.9.9"
    edited = rewrite(
        world["out"], world["tmp"] / "e.zip", {"attestation.json": json.dumps(att).encode()}
    )
    code, _, err = run(capsys, "share-bundle", "verify", edited, "--public-key", pk)
    assert code == 1 and "signature: invalid" in err


# ---- verify ---------------------------------------------------------------------------------


@pytest.fixture
def bundle(capsys, world) -> Path:
    assert create(capsys, world)[0] == 0
    return world["out"]


def parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


def test_an_edited_data_file_fails_verify(capsys, world, bundle):
    names = independent_table().column("name").to_pylist()
    names[5] = "edited"
    edited = rewrite(
        bundle,
        world["tmp"] / "e.zip",
        {"data/people.parquet": parquet_bytes(independent_table(names))},
    )
    code, _, err = run(capsys, "share-bundle", "verify", edited)
    assert code == 1 and "dataset_id does NOT match" in err


def test_an_added_or_removed_table_fails_verify(capsys, world, bundle):
    added = rewrite(
        bundle, world["tmp"] / "a.zip", {"data/extra.parquet": parquet_bytes(independent_table())}
    )
    assert run(capsys, "share-bundle", "verify", added)[0] == 1
    removed = rewrite(bundle, world["tmp"] / "r.zip", {"data/people.parquet": None})
    assert run(capsys, "share-bundle", "verify", removed)[0] == 1


def test_an_edited_manifest_fails_verify(capsys, world, make_manifest):
    make_manifest(world["data"] / "run_manifest.json", {"people": independent_table()})
    assert create(capsys, world)[0] == 0
    edited = rewrite(world["out"], world["tmp"] / "e.zip", {"manifest.json": b"{}"})
    assert run(capsys, "share-bundle", "verify", edited)[0] == 1


def test_a_manifest_slipped_into_a_bundle_without_one_fails_verify(capsys, world, bundle):
    slipped = rewrite(bundle, world["tmp"] / "s.zip", {"manifest.json": b"{}"})
    assert run(capsys, "share-bundle", "verify", slipped)[0] == 1


def test_a_failed_or_missing_check_fails_verify(capsys, world, bundle):
    att = json.loads(zipfile.ZipFile(bundle).read("attestation.json"))
    failed = {**att, "checks": [{**att["checks"][0], "passed": False}, att["checks"][1]]}
    p = rewrite(bundle, world["tmp"] / "f.zip", {"attestation.json": json.dumps(failed).encode()})
    code, _, err = run(capsys, "share-bundle", "verify", p)
    assert code == 1 and "check memorization passed" in err
    missing = {**att, "checks": att["checks"][:1]}
    p = rewrite(bundle, world["tmp"] / "m.zip", {"attestation.json": json.dumps(missing).encode()})
    code, _, err = run(capsys, "share-bundle", "verify", p)
    assert code == 1 and "top_values check" in err


def zip_with(path: Path, members: dict[str, bytes], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777) << 16
            zf.writestr(info, "target")
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "data/../../evil.txt",
        "/abs/evil.txt",
        "data\\evil.parquet",
        "C:/evil.txt",
        "data//x.parquet",
        "./attestation.json2",
        "other.txt",
        "data/sub/x.parquet",
        "data/x.exe",
    ],
)
def test_paths_outside_the_root_or_the_layout_are_refused_before_extraction(
    capsys, world, bundle, name, monkeypatch
):
    att = zipfile.ZipFile(bundle).read("attestation.json")
    bad = zip_with(world["tmp"] / "bad.zip", {"attestation.json": att, name: b"x"})
    import tempfile

    def no_extraction(*a, **k):
        raise AssertionError("the bundle was extracted before its names were checked")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", no_extraction)
    code, _, err = run(capsys, "share-bundle", "verify", bad)
    assert code == 2 and "shape: error:" in err
    assert not (world["tmp"] / "evil.txt").exists()


def test_symlinks_duplicates_and_directories_are_refused(capsys, world, bundle):
    att = zipfile.ZipFile(bundle).read("attestation.json")
    link = zip_with(world["tmp"] / "l.zip", {"attestation.json": att}, symlink="data/link.parquet")
    assert run(capsys, "share-bundle", "verify", link)[0] == 2
    dup = world["tmp"] / "d.zip"
    with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(dup, "w") as zf:
        zf.writestr("attestation.json", att)
        zf.writestr("attestation.json", att)
    assert run(capsys, "share-bundle", "verify", dup)[0] == 2
    folder = zip_with(world["tmp"] / "f.zip", {"attestation.json": att, "data/": b""})
    assert run(capsys, "share-bundle", "verify", folder)[0] == 2


def test_malformed_bundles_exit_2(capsys, world, bundle):
    att = json.loads(zipfile.ZipFile(bundle).read("attestation.json"))
    (world["tmp"] / "not.zip").write_text("plain text")
    cases = {
        "not a zip": world["tmp"] / "not.zip",
        "missing file": world["tmp"] / "missing.zip",
        "no attestation": zip_with(world["tmp"] / "1.zip", {"data/x.csv": b"a\n1\n"}),
        "bad json": zip_with(world["tmp"] / "2.zip", {"attestation.json": b"{"}),
        "wrong format": zip_with(
            world["tmp"] / "3.zip",
            {"attestation.json": json.dumps({**att, "format": "x"}).encode()},
        ),
        "bad version": zip_with(
            world["tmp"] / "4.zip",
            {"attestation.json": json.dumps({**att, "version": "1"}).encode()},
        ),
        "no dataset id": zip_with(
            world["tmp"] / "5.zip",
            {
                "attestation.json": json.dumps(
                    {k: v for k, v in att.items() if k != "dataset_id"}
                ).encode()
            },
        ),
        "checks not a list": zip_with(
            world["tmp"] / "6.zip", {"attestation.json": json.dumps({**att, "checks": 1}).encode()}
        ),
    }
    for label, path in cases.items():
        code, _, err = run(capsys, "share-bundle", "verify", path)
        assert code == 2, label
        assert "shape: error:" in err, label


def test_a_newer_attestation_version_exits_2(capsys, world, bundle):
    att = json.loads(zipfile.ZipFile(bundle).read("attestation.json"))
    newer = rewrite(
        bundle,
        world["tmp"] / "n.zip",
        {"attestation.json": json.dumps({**att, "version": 2}).encode()},
    )
    code, _, err = run(capsys, "share-bundle", "verify", newer)
    assert code == 2 and "newer Shape" in err


def test_ties_in_the_top_values_are_broken_by_value(world):
    """Source names 'a' x10, 'b' x10, 'c' x10, then unique: the top 2 are 'a' and 'b'."""
    names = ["a"] * 10 + ["b"] * 10 + ["c"] * 10 + [f"u{i}" for i in range(N - 30)]
    src = source_table().set_column(0, "name", pa.array(names))
    pq.write_table(src, world["src"] / "people.parquet")

    def result(copied: str) -> bool:
        gen = [copied] + [f"Synthetic {i}" for i in range(N - 1)]
        pq.write_table(independent_table(gen), world["data"] / "people.parquet")
        return share_bundle.create(
            world["data"], world["src"], world["classes"], world["out"], top_k=2
        ).ok

    assert result("c") is True
    assert result("b") is False
    assert result("a") is False


def test_nulls_are_ignored_by_the_top_values_check(world):
    names: list[str | None] = [None] * 60 + [f"u{i}" for i in range(N - 60)]
    pq.write_table(
        source_table().set_column(0, "name", pa.array(names, pa.string())),
        world["src"] / "people.parquet",
    )
    gen = [None] * 10 + [f"Synthetic {i}" for i in range(N - 10)]
    pq.write_table(
        independent_table().set_column(0, "name", pa.array(gen, pa.string())),
        world["data"] / "people.parquet",
    )
    assert share_bundle.create(world["data"], world["src"], world["classes"], world["out"]).ok


@pytest.mark.parametrize("level", ["PII", "sensitive", "confidential", "TOP_SECRET"])
def test_level_aliases_and_case_are_accepted(world, level):
    names = [SECRET] + [f"Synthetic {i}" for i in range(N - 1)]
    pq.write_table(independent_table(names), world["data"] / "people.parquet")
    world["classes"].write_text(json.dumps({"people.name": level}))
    assert not share_bundle.create(world["data"], world["src"], world["classes"], world["out"]).ok

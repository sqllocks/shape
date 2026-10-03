"""W3-12 items 1 and 2: the reference pack format, the loader and ``shape reference``."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pyarrow as pa
import pytest

from shape.cli.main import main
from shape.generation import reference as gen_ref
from shape.reference import (
    PACK_FORMAT,
    PACK_VERSION,
    ReferencePackError,
    UnknownPackError,
    discover_packs,
    find_pack,
    read_manifest,
    write_pack,
)

HERE = Path(__file__).parent
CAPSULE = HERE / "data" / "capsule_v1"
SCHEMA_PATH = (
    Path(__import__("shape").__file__).parent / "schemas" / "reference-pack-v1.schema.json"
)

META = dict(
    name="demo-pack",
    pack_version="1.0.0",
    source="https://example.org/demo (demo.csv)",
    retrieved="2026-10-03",
    license="CC0-1.0",
    attribution="Demo data, public domain.",
    transformation_version="1",
    sensitivity="public",
)


def validate(value, schema, path="$"):
    """The subset of JSON Schema the pack schema uses (type, const, enum, required, properties,
    additionalProperties, items, pattern, minLength, minItems, uniqueItems, minimum)."""
    import re

    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "boolean": bool,
    }
    out: list[str] = []
    if "type" in schema:
        ok = isinstance(value, types[schema["type"]]) and not (
            schema["type"] == "integer" and isinstance(value, bool)
        )
        if not ok:
            return [f"{path}: expected {schema['type']}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: const")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: enum")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: pattern")
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: minLength")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value < schema.get("minimum", value)
    ):
        out.append(f"{path}: minimum")
    if isinstance(value, dict):
        out += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                out += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected {k}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: minItems")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) < len(
            value
        ):
            out.append(f"{path}: uniqueItems")
        if "items" in schema:
            for i, v in enumerate(value):
                out += validate(v, schema["items"], f"{path}[{i}]")
    return out


def demo_tables():
    return {
        "demo_cities": pa.table(
            {
                "zip": pa.array(["02872", "10001", "90210"], pa.string()),
                "city": pa.array(["Prudence Island", "New York", "Beverly Hills"], pa.string()),
            }
        )
    }


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(gen_ref.REFERENCE_PATH_ENV, raising=False)
    gen_ref.clear_search_paths()
    yield
    gen_ref.clear_search_paths()


@pytest.fixture
def pack_dir(tmp_path):
    root = tmp_path / "packs"
    write_pack(root / "demo-pack", tables=demo_tables(), **META)
    return root


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- the format -------------------------------------------------------------------------------


def test_write_pack_declares_format_version_and_the_dataset_entry(tmp_path):
    manifest = write_pack(tmp_path / "p", tables=demo_tables(), **META)
    on_disk = json.loads((tmp_path / "p" / "pack.json").read_text())
    assert on_disk == manifest
    assert manifest["format"] == PACK_FORMAT == "shape-reference-pack"
    assert manifest["version"] == PACK_VERSION == 1
    for key, value in META.items():
        assert manifest[key] == value
    (entry,) = manifest["datasets"]
    assert entry["name"] == "demo_cities"
    assert entry["fields"] == ["zip", "city"]
    assert entry["rows"] == 3
    data = (tmp_path / "p" / entry["file"]).read_bytes()
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["file"].endswith(".arrow")
    assert set(manifest) == {
        "format",
        "version",
        "name",
        "pack_version",
        "source",
        "retrieved",
        "license",
        "attribution",
        "transformation_version",
        "sensitivity",
        "datasets",
    }


def test_data_files_are_arrow_ipc(tmp_path):
    manifest = write_pack(tmp_path / "p", tables=demo_tables(), **META)
    with pa.memory_map(str(tmp_path / "p" / manifest["datasets"][0]["file"])) as f:
        table = pa.ipc.open_file(f).read_all()
    assert table.column("zip").to_pylist() == ["02872", "10001", "90210"]


def test_write_pack_is_deterministic(tmp_path):
    a = write_pack(tmp_path / "a", tables=demo_tables(), **META)
    b = write_pack(tmp_path / "b", tables=demo_tables(), **META)
    assert a == b


def test_pack_names_and_dataset_names_must_be_plain(tmp_path):
    for bad in ("../x", "a/b", "A B", "", "-x", "x" * 80):
        with pytest.raises(ReferencePackError):
            write_pack(tmp_path / "p", tables=demo_tables(), **{**META, "name": bad})
    with pytest.raises(ReferencePackError):
        write_pack(tmp_path / "p", tables={"../evil": demo_tables()["demo_cities"]}, **META)
    with pytest.raises(ReferencePackError):
        write_pack(tmp_path / "p", tables={}, **META)


def _manifest(pack_dir):
    return json.loads((pack_dir / "demo-pack" / "pack.json").read_text())


def _put(pack_dir, manifest):
    (pack_dir / "demo-pack" / "pack.json").write_text(json.dumps(manifest))


def test_read_manifest_round_trips(pack_dir):
    assert read_manifest(pack_dir / "demo-pack") == _manifest(pack_dir)


def test_a_missing_manifest_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ReferencePackError, match="pack.json"):
        read_manifest(tmp_path / "empty")


def test_the_manifest_is_validated(pack_dir):
    good = _manifest(pack_dir)

    def broken(mutate, match):
        m = copy.deepcopy(good)
        mutate(m)
        _put(pack_dir, m)
        with pytest.raises(ReferencePackError, match=match):
            read_manifest(pack_dir / "demo-pack")

    broken(lambda m: m.update(format="something-else"), "format")
    broken(lambda m: m.pop("format"), "format")
    broken(lambda m: m.update(version="1"), "version")
    broken(lambda m: m.update(version=True), "version")
    broken(lambda m: m.update(version=0), "version")
    broken(lambda m: m.pop("license"), "license")
    broken(lambda m: m.update(license=""), "license")
    broken(lambda m: m.update(name="Bad Name"), "name")
    broken(lambda m: m.update(unknown_key=1), "unknown_key")
    broken(lambda m: m.update(datasets=[]), "datasets")
    broken(lambda m: m["datasets"][0].update(rows=-1), "rows")
    broken(lambda m: m["datasets"][0].update(rows="3"), "rows")
    broken(lambda m: m["datasets"][0].update(sha256="abc"), "sha256")
    broken(lambda m: m["datasets"][0].update(fields=[]), "fields")
    broken(lambda m: m["datasets"][0].update(fields=["a", "a"]), "fields")
    broken(lambda m: m["datasets"][0].pop("name"), "name")
    broken(lambda m: m["datasets"].append(copy.deepcopy(m["datasets"][0])), "duplicate")


@pytest.mark.parametrize("file", ["../x.arrow", "/etc/passwd", "a/b.arrow", "..", "x.csv", ""])
def test_a_dataset_file_is_a_plain_arrow_file_name(pack_dir, file):
    m = _manifest(pack_dir)
    m["datasets"][0]["file"] = file
    _put(pack_dir, m)
    with pytest.raises(ReferencePackError, match="file"):
        read_manifest(pack_dir / "demo-pack")


def test_a_newer_manifest_version_says_so(pack_dir):
    m = _manifest(pack_dir)
    m["version"] = PACK_VERSION + 1
    m["added_in_v2"] = True
    _put(pack_dir, m)
    with pytest.raises(ReferencePackError, match=r"version 2.*upgrade Shape|newer"):
        read_manifest(pack_dir / "demo-pack")


# --- the JSON Schema --------------------------------------------------------------------------


def test_the_schema_accepts_a_written_manifest_and_rejects_bad_ones(pack_dir):
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["$id"].endswith("reference-pack-v1.schema.json")
    good = _manifest(pack_dir)
    assert validate(good, schema) == []
    for key in good:
        m = {k: v for k, v in good.items() if k != key}
        assert validate(m, schema), f"missing {key} must be rejected"
    assert validate({**good, "version": 2}, schema)
    assert validate({**good, "format": "x"}, schema)
    assert validate({**good, "datasets": []}, schema)
    assert validate({**good, "extra": 1}, schema)
    ds = copy.deepcopy(good)
    ds["datasets"][0]["rows"] = -1
    assert validate(ds, schema)


# --- the compatibility corpus -----------------------------------------------------------------


def test_the_committed_v1_pack_still_loads():
    """A pack written by version 1 of the format, committed as bytes: later versions of Shape
    must keep reading it."""
    manifest = read_manifest(CAPSULE)
    assert manifest["format"] == "shape-reference-pack" and manifest["version"] == 1
    gen_ref.add_search_path(CAPSULE.parent)
    ds = gen_ref.load_dataset("capsule_codes")
    assert ds.fields == ("code", "label")
    assert ds.column("code").to_pylist() == ["001", "A-2", "ÄÖ3"]
    assert len(ds) == manifest["datasets"][0]["rows"] == 3


# --- the loader -------------------------------------------------------------------------------


def test_load_dataset_finds_a_pack_in_a_search_path(pack_dir):
    gen_ref.add_search_path(pack_dir)
    ds = gen_ref.load_dataset("demo_cities")
    assert ds.fields == ("zip", "city") and ds.records
    assert ds.column("zip").to_pylist() == ["02872", "10001", "90210"]  # leading zero kept


def test_load_dataset_finds_a_pack_in_the_environment_path(pack_dir, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    assert len(gen_ref.load_dataset("demo_cities")) == 3


def test_a_search_directory_may_be_the_pack_itself(pack_dir):
    gen_ref.add_search_path(pack_dir / "demo-pack")
    assert len(gen_ref.load_dataset("demo_cities")) == 3


def test_a_registered_dataset_and_a_json_file_win_over_a_pack(pack_dir):
    gen_ref.add_search_path(pack_dir)
    (pack_dir / "demo_cities.json").write_text(json.dumps(["only"]))
    assert len(gen_ref.load_dataset("demo_cities")) == 1
    gen_ref.register_dataset("demo_cities", ["a", "b"])
    try:
        assert len(gen_ref.load_dataset("demo_cities")) == 2
    finally:
        gen_ref.unregister_dataset("demo_cities")


def test_an_earlier_search_path_wins_and_a_shipped_name_can_be_overridden(tmp_path):
    first = tmp_path / "first"
    write_pack(
        first / "mine",
        tables={"iso_3166_1": pa.table({"alpha2": ["ZZ"]})},
        **{**META, "name": "mine"},
    )
    gen_ref.add_search_path(first)
    assert gen_ref.load_dataset("iso_3166_1").column("alpha2").to_pylist() == ["ZZ"]


def test_an_unknown_dataset_names_where_it_looked(pack_dir):
    gen_ref.add_search_path(pack_dir)
    with pytest.raises(gen_ref.DatasetNotFoundError, match="nope"):
        gen_ref.load_dataset("nope")


def test_dataset_names_are_never_paths(pack_dir):
    gen_ref.add_search_path(pack_dir)
    with pytest.raises(gen_ref.DatasetNotFoundError):
        gen_ref.load_dataset("../packs/demo-pack/demo_cities")


def test_a_checksum_mismatch_is_refused_with_the_exact_message(pack_dir):
    file = pack_dir / "demo-pack" / _manifest(pack_dir)["datasets"][0]["file"]
    data = bytearray(file.read_bytes())
    data[-20] ^= 0xFF
    file.write_bytes(bytes(data))
    gen_ref.add_search_path(pack_dir)
    with pytest.raises(ReferencePackError) as err:
        gen_ref.load_dataset("demo_cities")
    assert str(err.value) == f"reference pack demo-pack: {file.name} does not match its checksum"


def test_a_checksum_mismatch_is_exit_2_on_the_command_line(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    file = pack_dir / "demo-pack" / _manifest(pack_dir)["datasets"][0]["file"]
    file.write_bytes(file.read_bytes() + b"x")
    code, _, err = run(capsys, "reference", "show", "demo-pack")
    assert code == 2
    assert f"reference pack demo-pack: {file.name} does not match its checksum" in err


def test_a_dataset_that_disagrees_with_its_manifest_is_refused(tmp_path):
    root = tmp_path / "packs"
    write_pack(root / "demo-pack", tables=demo_tables(), **META)
    m = json.loads((root / "demo-pack" / "pack.json").read_text())
    other = pa.table({"zip": ["1"], "city": ["x"], "extra": [1]})
    path = root / "demo-pack" / m["datasets"][0]["file"]
    with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, other.schema) as w:
        w.write_table(other)
    m["datasets"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "demo-pack" / "pack.json").write_text(json.dumps(m))
    gen_ref.add_search_path(root)
    with pytest.raises(ReferencePackError, match="demo-pack.*fields|fields.*demo-pack"):
        gen_ref.load_dataset("demo_cities")


def test_a_broken_pack_does_not_hide_the_others(pack_dir, tmp_path):
    (pack_dir / "broken").mkdir()
    (pack_dir / "broken" / "pack.json").write_text("{not json")
    gen_ref.add_search_path(pack_dir)
    assert len(gen_ref.load_dataset("demo_cities")) == 3
    found = discover_packs([pack_dir])
    assert "demo-pack" in {p.name for p in found.packs}
    assert [p.path.name for p in found.problems] == ["broken"]


def test_find_pack_and_unknown_pack(pack_dir):
    assert find_pack("demo-pack", [pack_dir]).name == "demo-pack"
    with pytest.raises(UnknownPackError, match="no-such-pack"):
        find_pack("no-such-pack", [pack_dir])


def test_pack_datasets_work_unchanged_with_reference_pairs(pack_dir, tmp_path):
    from shape.profile.joint.reference import measure_reference_pairs

    gen_ref.add_search_path(pack_dir)
    t = pa.table({"zip": ["02872", "10001", "00000"], "city": ["Prudence Island", "Boston", "x"]})
    import shape

    prof = shape.profile(
        t, reference_pairs=[{"columns": ["zip", "city"], "reference": "demo_cities"}]
    )
    (pair,) = prof.tables[next(iter(prof.tables))]["joint"]["reference_pairs"]
    assert pair["rows"] == 3 and pair["mismatched"] == 2
    assert measure_reference_pairs  # the function the profile used


# --- shape reference list / show --------------------------------------------------------------


def test_list_json_has_every_pack_with_version_rows_license_and_origin(
    pack_dir, capsys, monkeypatch
):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    code, out, _ = run(capsys, "reference", "list", "--json")
    assert code == 0
    doc = json.loads(out)
    by_name = {p["name"]: p for p in doc["packs"]}
    demo = by_name["demo-pack"]
    assert demo["pack_version"] == "1.0.0" and demo["license"] == "CC0-1.0"
    assert demo["origin"] == "search path" and demo["path"].endswith("demo-pack")
    assert demo["datasets"][0]["name"] == "demo_cities" and demo["datasets"][0]["rows"] == 3
    for shipped in ("iso-3166-1", "iso-639-1", "iban-lengths"):
        assert by_name[shipped]["origin"] == "shipped"
        assert by_name[shipped]["license"]
    assert doc["problems"] == []


def test_list_text_one_line_per_dataset(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    code, out, _ = run(capsys, "reference", "list")
    assert code == 0
    line = next(ln for ln in out.splitlines() if "demo_cities" in ln)
    for part in ("demo-pack", "1.0.0", "3", "CC0-1.0", "search path"):
        assert part in line


def test_list_reports_a_broken_pack_without_failing(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    (pack_dir / "broken").mkdir()
    (pack_dir / "broken" / "pack.json").write_text("{}")
    code, out, err = run(capsys, "reference", "list", "--json")
    assert code == 0
    assert json.loads(out)["problems"][0]["path"].endswith("broken")
    assert "broken" in err


def test_show_json_has_manifest_fields_and_first_rows(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    code, out, _ = run(capsys, "reference", "show", "demo-pack", "--json")
    assert code == 0
    doc = json.loads(out)
    assert doc["manifest"] == _manifest(pack_dir)
    (ds,) = doc["datasets"]
    assert ds["name"] == "demo_cities" and ds["fields"] == ["zip", "city"]
    assert ds["rows"] == 3
    assert ds["first_rows"][0] == {"zip": "02872", "city": "Prudence Island"}


def test_show_limits_the_first_rows(tmp_path, capsys, monkeypatch):
    big = pa.table({"n": [str(i) for i in range(50)]})
    write_pack(tmp_path / "p" / "big", tables={"big_rows": big}, **{**META, "name": "big"})
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(tmp_path / "p"))
    code, out, _ = run(capsys, "reference", "show", "big", "--json")
    assert code == 0
    rows = json.loads(out)["datasets"][0]["first_rows"]
    assert 1 <= len(rows) <= 10 and rows[0] == {"n": "0"}


def test_show_text_prints_the_manifest_and_rows(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    code, out, _ = run(capsys, "reference", "show", "demo-pack")
    assert code == 0
    for part in ("demo-pack", "CC0-1.0", "demo_cities", "zip", "Prudence Island"):
        assert part in out


def test_show_an_unknown_pack_is_exit_2(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    code, _, err = run(capsys, "reference", "show", "no-such-pack")
    assert code == 2 and "no-such-pack" in err


def test_show_a_pack_name_is_not_a_path(pack_dir, capsys, monkeypatch):
    monkeypatch.setenv(gen_ref.REFERENCE_PATH_ENV, str(pack_dir))
    code, _, err = run(capsys, "reference", "show", "../packs/demo-pack")
    assert code == 2 and "not a plain pack name" in err


def test_reference_needs_a_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["reference"])
    assert exc.value.code == 2


def test_the_capsule_copy_is_a_real_pack(tmp_path):
    """The committed capsule is a valid pack (manifest and checksum), not just a fixture."""
    dest = tmp_path / "c"
    shutil.copytree(CAPSULE, dest)
    assert find_pack("capsule", [tmp_path]).name == "capsule"

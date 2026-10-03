"""W6-03 item 4: the public dataset library, its profiles, commands and build script."""

from __future__ import annotations

import copy
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from shape import library
from shape.cli.main import main
from shape.library import DatasetLibraryError

ROOT = Path(__file__).resolve().parents[2]
DATASETS = [e["name"] for e in library.load_index()]


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def load_script():
    spec = importlib.util.spec_from_file_location(
        "build_dataset_library", ROOT / "scripts" / "build_dataset_library.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the shipped library ---------------------------------------------------------------------


def test_the_index_declares_its_format_and_lists_at_least_five_datasets():
    doc = json.loads((library.ROOT / "index.json").read_text())
    assert doc["format"] == "shape-dataset-library" and doc["version"] == 1
    assert isinstance(doc["version"], int) and len(doc["datasets"]) >= 5
    assert [e["name"] for e in doc["datasets"]] == sorted(e["name"] for e in doc["datasets"])


@pytest.mark.parametrize("name", DATASETS)
def test_every_entry_has_an_allowed_licence_a_source_and_a_profile_under_the_limit(name):
    entry = library.get_dataset(name)
    assert set(entry) == {"name", "title", "source_url", "license", "attribution", "retrieved",
                          "source_sha256", "rows", "profile"}  # fmt: skip
    assert entry["license"] in library.ALLOWED_LICENSES
    assert entry["source_url"].startswith("https://") and len(entry["source_sha256"]) == 64
    path = library.profile_path(name)
    assert path.suffix == ".shape" and 0 < path.stat().st_size < library.MAX_PROFILE_BYTES


def test_the_library_adds_under_two_megabytes_to_the_wheel():
    total = sum(p.stat().st_size for p in library.ROOT.parent.rglob("*") if p.is_file()
                and "__pycache__" not in p.parts)  # fmt: skip
    assert total < library.MAX_TOTAL_BYTES


def test_no_source_data_is_shipped():
    allowed = {".shape", ".json"}
    assert {p.suffix for p in library.ROOT.iterdir()} <= allowed
    assert not list(library.ROOT.rglob("*.csv")) and not list(library.ROOT.rglob("*.parquet"))


@pytest.mark.parametrize("name", DATASETS)
def test_every_profile_is_a_safe_capture_that_passes_the_leak_scan(name, capsys):
    import shape

    profile = shape.load(str(library.profile_path(name)))
    assert profile.capture["mode"] == "safe"
    assert profile.to_dict()["row_count"] == library.get_dataset(name)["rows"]
    code, _, _ = run(capsys, "profile", "validate", "--safe", library.profile_path(name))
    assert code == 0


def test_every_dataset_is_credited_in_the_third_party_notices():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for entry in library.load_index():
        assert entry["attribution"] in notices, entry["name"]
        assert entry["source_url"] in notices and f"dataset:{entry['name']}" in notices


def test_the_allow_list_is_the_one_the_documentation_states():
    doc = (ROOT / "docs" / "DATASET_LIBRARY.md").read_text(encoding="utf-8")
    for key in library.ALLOWED_LICENSES:
        assert f"`{key}`" in doc
    assert set(library.ALLOWED_LICENSES) == {"CC0-1.0", "CC-BY-4.0", "public-domain"}


@pytest.mark.parametrize("name", DATASETS)
def test_generate_from_a_dataset_runs_with_the_network_blocked(name, tmp_path, capsys, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the network was used")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    rows = library.get_dataset(name)["rows"]
    code, out, err = run(capsys, "generate", "--from", f"dataset:{name}", "--rows", 50, "--seed", 1,
                         "--format", "csv", "-o", tmp_path)  # fmt: skip
    assert code == 0, err
    assert "Wrote" in out and rows > 0
    files = list(tmp_path.rglob("*.csv"))
    assert len(files) == 1 and len(files[0].read_text().splitlines()) == 51


def test_a_dataset_reference_and_the_copied_profile_generate_the_same_rows(tmp_path, capsys):
    copy_to = tmp_path / "iris.shape"
    assert run(capsys, "library", "get", "iris", "-o", copy_to)[0] == 0
    args = ["--rows", 40, "--seed", 5, "--format", "csv"]
    assert run(capsys, "generate", "--from", "dataset:iris", *args, "-o", tmp_path / "a")[0] == 0
    assert run(capsys, "generate", "--from", copy_to, *args, "-o", tmp_path / "b")[0] == 0
    a = next((tmp_path / "a").rglob("*.csv")).read_bytes()
    b = next((tmp_path / "b").rglob("*.csv")).read_bytes()
    assert a == b


def test_generate_from_an_unknown_dataset_exits_two_and_lists_the_library(capsys):
    code, _, err = run(capsys, "generate", "--from", "dataset:nope")
    assert code == 2 and "unknown dataset 'nope'" in err and "iris" in err


def test_the_library_prefix_stays_with_scenarios(capsys):
    code, _, err = run(capsys, "generate", "--from", "library:null_flood")
    assert code == 2  # not a profile file: dataset: is the prefix of this library


# ---- the commands ---------------------------------------------------------------------------


def test_list_names_every_dataset_with_licence_and_rows(capsys):
    code, out, _ = run(capsys, "library", "list")
    assert code == 0
    for e in library.load_index():
        assert e["name"] in out and e["license"] in out and str(e["rows"]) in out


def test_list_json_is_the_index_in_a_shape_result(capsys):
    code, out, _ = run(capsys, "library", "list", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["format"] == "shape-result" and doc["command"] == "library list"
    assert doc["datasets"] == library.load_index()


def test_show_prints_the_source_licence_and_attribution(capsys):
    code, out, _ = run(capsys, "library", "show", "palmer-penguins")
    assert code == 0 and "CC0-1.0" in out and "Horst AM" in out and "dataset:palmer-penguins" in out
    assert json.loads(run(capsys, "library", "show", "iris", "--json")[1])["name"] == "iris"


def test_show_and_get_an_unknown_dataset_exit_two(capsys, tmp_path):
    code, _, err = run(capsys, "library", "show", "nope")
    assert code == 2 and "unknown dataset" in err
    code, _, err = run(capsys, "library", "get", "nope", "-o", tmp_path / "x.shape")
    assert code == 2 and not (tmp_path / "x.shape").exists()


def test_get_copies_the_profile_and_refuses_an_existing_file(tmp_path, capsys):
    out = tmp_path / "wine.shape"
    code, text, _ = run(capsys, "library", "get", "wine", "-o", out)
    assert code == 0 and out.read_bytes() == library.profile_path("wine").read_bytes()
    out.write_bytes(b"mine")
    code, _, err = run(capsys, "library", "get", "wine", "-o", out)
    assert code == 2 and "already exists" in err and out.read_bytes() == b"mine"


def test_get_dry_run_writes_nothing(tmp_path, capsys):
    code, text, _ = run(capsys, "library", "get", "wine", "-o", tmp_path / "w.shape", "--dry-run")
    assert code == 0 and "would" in text and not (tmp_path / "w.shape").exists()


def test_resolve_passes_a_file_through_and_resolves_a_reference():
    assert library.resolve_profile_ref("a/b.shape") == "a/b.shape"
    assert library.resolve_profile_ref("dataset:iris") == str(library.profile_path("iris"))
    assert library.is_reference("dataset:iris") and not library.is_reference("iris.shape")
    assert not library.is_reference(None)
    with pytest.raises(library.UnknownDatasetError, match="unknown dataset"):
        library.resolve_profile_ref("dataset:nope")


# ---- the index format ------------------------------------------------------------------------


def doc():
    return {
        "format": "shape-dataset-library",
        "version": 1,
        "datasets": copy.deepcopy(library.load_index()),
    }


def test_version_one_index_still_loads_and_a_newer_one_names_the_upgrade():
    """The compatibility test of shape-dataset-library."""
    assert len(library.parse_index(doc(), "index")) == len(DATASETS)
    newer = doc()
    newer["version"] = 2
    with pytest.raises(DatasetLibraryError, match=r"version 2.*upgrade"):
        library.parse_index(newer, "index")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format="shape-suite"), "not a shape-dataset-library"),
        (lambda d: d.pop("version"), "integer 'version'"),
        (lambda d: d.update(version="1"), "integer 'version'"),
        (lambda d: d.update(version=True), "integer 'version'"),
        (lambda d: d.update(version=0), "integer 'version'"),
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.update(datasets=[]), "non-empty 'datasets'"),
        (lambda d: d["datasets"].append(5), "must be an object"),
        (lambda d: d["datasets"][0].pop("rows"), "needs exactly"),
        (lambda d: d["datasets"][0].update(extra=1), "needs exactly"),
        (lambda d: d["datasets"][0].update(name="Not A Slug"), "slug"),
        (lambda d: d["datasets"][1].update(name=d["datasets"][0]["name"]), "listed twice"),
        (lambda d: d["datasets"][0].update(title=" "), "'title' must be text"),
        (lambda d: d["datasets"][0].update(license="CC-BY-NC-4.0"), "not allowed"),
        (lambda d: d["datasets"][0].update(license="MIT"), "not allowed"),
        (lambda d: d["datasets"][0].update(license="cc0-1.0"), "not allowed"),
        (lambda d: d["datasets"][0].update(source_url="http://example.org/x.csv"), "https URL"),
        (lambda d: d["datasets"][0].update(retrieved="yesterday"), "ISO date"),
        (lambda d: d["datasets"][0].update(source_sha256="abc"), "64 hex"),
        (lambda d: d["datasets"][0].update(source_sha256="G" * 64), "64 hex"),
        (lambda d: d["datasets"][0].update(rows=0), "positive integer"),
        (lambda d: d["datasets"][0].update(rows=True), "positive integer"),
        (lambda d: d["datasets"][0].update(rows="9"), "positive integer"),
        (lambda d: d["datasets"][0].update(profile="../x.shape"), "file name"),
        (lambda d: d["datasets"][0].update(profile=".hidden"), "file name"),
    ],
)
def test_a_malformed_index_is_refused_with_a_message(change, message):
    d = doc()
    change(d)
    with pytest.raises(DatasetLibraryError, match=message):
        library.parse_index(d, "index")


def test_an_index_that_is_not_an_object_is_refused():
    with pytest.raises(DatasetLibraryError, match="JSON object"):
        library.parse_index([], "index")


def test_a_missing_broken_or_incomplete_library_names_the_problem(tmp_path):
    with pytest.raises(DatasetLibraryError, match="not found"):
        library.load_index(tmp_path)
    (tmp_path / "index.json").write_text("{nope")
    with pytest.raises(DatasetLibraryError, match="not valid JSON"):
        library.load_index(tmp_path)
    (tmp_path / "index.json").write_text(json.dumps(doc()))
    with pytest.raises(DatasetLibraryError, match="which is missing"):
        library.profile_path("iris", tmp_path)


# ---- the build script ---------------------------------------------------------------------------

CSV = "a,b\n" + "\n".join(f"{i},{'x' if i % 2 else 'y'}" for i in range(60)) + "\n"
SOURCES = {
    "toy": {
        "title": "Toy",
        "url": "https://example.org/toy.csv",
        "license": "CC0-1.0",
        "attribution": "Nobody (2026). Toy.",
    }
}


@pytest.fixture
def build(tmp_path):
    script = load_script()
    lib = tmp_path / "lib"
    src = tmp_path / "toy.csv"
    src.write_text(CSV)

    def do(name="toy", file=src, **kw):
        return script.build(name, from_file=file, library=lib, sources=SOURCES, **kw)

    do.script, do.lib, do.src = script, lib, src
    return do


def test_a_build_profiles_the_source_and_records_it_without_shipping_rows(build):
    entry = build(retrieved="2026-10-03")
    assert entry["rows"] == 60 and entry["retrieved"] == "2026-10-03"
    index = library.load_index(build.lib)
    assert [e["name"] for e in index] == ["toy"] and index[0] == entry
    assert (build.lib / "toy.shape").is_file()
    assert sorted(p.name for p in build.lib.iterdir()) == ["index.json", "toy.shape"]
    import shape

    assert shape.load(str(build.lib / "toy.shape")).capture["mode"] == "safe"


def test_a_rebuild_of_the_same_source_changes_nothing_and_keeps_the_date(build):
    first = build(retrieved="2026-01-02")
    second = build()
    assert second == first


def test_a_source_that_changed_is_an_error_until_it_is_refreshed(build):
    first = build(retrieved="2026-01-02")
    build.src.write_text(CSV + "99,z\n")
    with pytest.raises(build.script.BuildError, match="has changed") as caught:
        build()
    assert caught.value.code == 1
    assert first["source_sha256"] in str(caught.value)
    assert library.load_index(build.lib)[0] == first  # nothing was recorded
    refreshed = build(refresh=True, retrieved="2026-02-03")
    assert refreshed["rows"] == 61 and refreshed["source_sha256"] != first["source_sha256"]
    assert refreshed["retrieved"] == "2026-02-03"


def test_the_index_keeps_its_datasets_in_name_order(build):
    SOURCES["abc"] = dict(SOURCES["toy"], title="Abc")
    try:
        build(name="toy")
        build(name="abc")
    finally:
        del SOURCES["abc"]
    assert [e["name"] for e in library.load_index(build.lib)] == ["abc", "toy"]


def test_an_unknown_dataset_a_source_that_is_not_csv_and_a_big_profile_are_refused(build, tmp_path):
    with pytest.raises(build.script.BuildError, match="unknown dataset") as caught:
        build(name="nope")
    assert caught.value.code == 2
    junk = tmp_path / "junk.csv"
    junk.write_bytes(b"\x00\x01\x02\xff")
    with pytest.raises(build.script.BuildError, match="not a readable CSV"):
        build(file=junk)
    import shape.library as lib

    original = lib.MAX_PROFILE_BYTES
    lib.MAX_PROFILE_BYTES = 10
    try:
        with pytest.raises(build.script.BuildError, match="the limit is"):
            build()
    finally:
        lib.MAX_PROFILE_BYTES = original
    assert not (build.lib / "toy.shape").exists()


def test_only_https_sources_are_downloaded(build):
    with pytest.raises(build.script.BuildError, match="only https"):
        build.script.download("http://example.org/x.csv")
    with pytest.raises(build.script.BuildError, match="only https"):
        build.script.download("file:///etc/passwd")


def test_every_catalog_source_is_https_and_under_an_allowed_licence():
    script = load_script()
    assert set(DATASETS) <= set(script.SOURCES)
    for spec in script.SOURCES.values():
        assert spec["url"].startswith("https://") and spec["license"] in library.ALLOWED_LICENSES


def test_the_script_exit_codes(tmp_path, capsys, monkeypatch):
    script = load_script()
    assert script.main(["nope"]) == 2
    assert "unknown dataset" in capsys.readouterr().err
    assert script.main(["iris", "--retrieved", "tomorrow"]) == 2
    monkeypatch.setattr(script, "LIBRARY", tmp_path / "lib")
    src = tmp_path / "x.csv"
    src.write_text(CSV)
    monkeypatch.setitem(script.SOURCES, "toy", SOURCES["toy"])
    monkeypatch.setattr(
        script, "build", lambda *a, **k: (_ for _ in ()).throw(script.BuildError("boom", 1))
    )
    assert script.main(["toy", "--from-file", str(src)]) == 1
    assert "boom" in capsys.readouterr().err


def test_the_build_script_is_the_only_code_that_touches_the_network():
    for path in (ROOT / "src" / "shape" / "library").rglob("*.py"):
        text = path.read_text()
        assert "urllib" not in text and "requests" not in text and "http.client" not in text
    assert "urllib.request" in (ROOT / "scripts" / "build_dataset_library.py").read_text()

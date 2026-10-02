"""P6-14: the pack loader, on the reference inputs and on malformed documents."""

from __future__ import annotations

import pytest

from shape.scenario import PackError, PackLoader
from tests.scenario.conftest import PACK, write


@pytest.mark.parametrize("name", ["tutorial_custom_pack.yaml", "notebook_custom_pack.yaml"])
def test_reference_packs_load_with_every_field(fixtures, name):
    pack = PackLoader().load(fixtures / name)
    assert (pack.pack_version, pack.id, pack.kind, pack.domain) == (
        1,
        "my_custom_pack",
        "file_drop",
        "retail",
    )
    assert pack.fabric_targets == {"lakehouse_files_root": "Files/landing/retail"}
    fd = pack.file_drop
    assert fd is not None
    assert (fd.cadence, fd.partitioning, fd.formats) == ("daily", "dt=YYYY-MM-DD", ["parquet"])
    assert fd.file_naming == "{domain}_{entity}_{dt}_{seq}.parquet"
    assert pack.entities == ["customer", "order"]
    assert (
        fd.manifest is not None and fd.manifest.enabled and fd.manifest.name == "manifest_{dt}.json"
    )
    assert fd.done_flag is not None and fd.done_flag.enabled
    assert fd.lateness is not None and not fd.lateness.enabled
    assert fd.duplicates is not None and not fd.duplicates.enabled
    assert pack.validation is not None and pack.validation.required_gates == ["schema_conformance"]
    assert pack.extra_keys == []


def test_defaults_when_sections_are_absent(tmp_path):
    pack = PackLoader().load(write(tmp_path / "p.yaml", "id: only\n"))
    assert (pack.pack_version, pack.kind, pack.domain, pack.fabric_targets) == (
        1,
        "file_drop",
        "",
        {},
    )
    assert pack.file_drop is None and pack.topics == [] and pack.entities == []


def test_a_missing_file_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        PackLoader().load(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is empty"),
        ("- a\n- b\n", "must be a mapping"),
        ("pack_version: one\n", "pack_version must be an integer"),
        ("id: [a]\n", "id must be text"),
        ("file_drop:\n  entities: customer\n", "file_drop.entities must be a list"),
        (
            "file_drop:\n  lateness: {enabled: 'yes please'}\n",
            "file_drop.lateness.enabled must be true or false",
        ),
        ("streaming:\n  topics: {a: 1}\n", "streaming.topics must be a list"),
        ("a: [unclosed\n", "not valid YAML"),
    ],
)
def test_malformed_documents_are_named_errors(tmp_path, text, message):
    with pytest.raises(PackError, match=message):
        PackLoader().load(write(tmp_path / "p.yaml", text))


def test_unknown_keys_are_recorded_with_their_path(tmp_path):
    text = PACK.format(fmt="csv") + "extra_top: 1\n"
    text = text.replace("  entities:", "  entites_typo: 1\n  entities:")
    pack = PackLoader().load(write(tmp_path / "p.yaml", text))
    assert sorted(pack.extra_keys) == ["extra_top", "file_drop.entites_typo"]


def test_a_pack_root_lists_and_loads(tmp_path):
    (tmp_path / "retail").mkdir()
    write(tmp_path / "retail" / "daily.yaml", PACK.format(fmt="csv"))
    loader = PackLoader(tmp_path)
    assert loader.list_packs() == [
        {"domain": "retail", "pack_id": "daily", "path": str(tmp_path / "retail" / "daily.yaml")}
    ]
    assert loader.load_from_root("retail", "daily").id == "t"
    with pytest.raises(FileNotFoundError, match="Available: daily"):
        loader.load_from_root("retail", "weekly")
    with pytest.raises(FileNotFoundError, match="No packs for domain"):
        loader.load_from_root("hr", "x")
    with pytest.raises(PackError, match="not a pack name"):
        loader.load_from_root("retail", "../x")


def test_shape_ships_no_packs():
    with pytest.raises(FileNotFoundError, match="Shape ships no packs"):
        PackLoader().load_from_root("retail", "fd_daily_batch")
    assert PackLoader().list_packs() == []

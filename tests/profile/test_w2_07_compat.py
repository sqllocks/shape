"""W2-07 compatibility: what an unsampled profile holds besides the new records is unchanged, and
profiles and decision files written before W2-07 still load."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _w2_07_data import (  # noqa: E402
    orders_table,
    shop_tables,
    write_orders_csv,
    write_orders_parquet,
)

HERE = Path(__file__).parent
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "w2_07"
NEW_TABLE_KEYS = {"sampling"}
NEW_COLUMN_KEYS = {"adequacy", "type_inference"}


def strip_new(doc: Any) -> Any:
    """A profile document without the keys W2-07 added."""
    doc = copy.deepcopy(doc)
    tables = doc["tables"].values() if "tables" in doc else [doc]
    for t in tables:
        for k in NEW_TABLE_KEYS:
            t.pop(k, None)
        for c in t["columns"].values():
            for k in NEW_COLUMN_KEYS:
                c.pop(k, None)
    return doc


def as_json(doc: Any) -> Any:
    return json.loads(json.dumps(doc, sort_keys=True))


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    """The profiles the code before W2-07 produced for these fixtures (made by running it)."""
    return json.loads((HERE / "data" / "unsampled_golden.json").read_text())


def test_unsampled_profiles_are_unchanged_apart_from_the_new_records(
    tmp_path: Path, golden: dict[str, Any]
) -> None:
    csv = write_orders_csv(tmp_path / "orders.csv")
    pqf = write_orders_parquet(tmp_path / "orders_pq.parquet")
    got = {
        "csv": shape.profile(str(csv)).to_dict(),
        "parquet": shape.profile(str(pqf)).to_dict(),
        "arrow": shape.profile(orders_table()).to_dict(),
        "dataset": shape.profile(shop_tables()).to_dict(),
    }
    for key, doc in got.items():
        assert as_json(strip_new(doc)) == golden[key], key


def test_the_new_records_are_present_in_every_unsampled_profile(tmp_path: Path) -> None:
    prof = shape.profile(shop_tables())
    for t in prof.tables.values():
        assert t["sampling"]["method"] == "none"
        for c in t["columns"].values():
            assert set(NEW_COLUMN_KEYS) <= set(c)


def test_a_profile_written_before_w2_07_loads_and_shows_the_record_as_not_recorded() -> None:
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))
    t = old.tables["orders"]
    assert "sampling" not in t and all("type_inference" not in c for c in t["columns"].values())
    assert old.sampling() == {"orders": None}
    assert old.describe_sampling() == "orders: not recorded"
    assert "sampled" not in repr(old)
    assert old.summary()["columns"]["amount"]["dtype"] == "float"


def test_an_old_profile_round_trips_without_gaining_records(tmp_path: Path) -> None:
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))
    out = tmp_path / "again.shape"
    shape.save(old, out)
    assert shape.load(str(out)).to_dict() == old.to_dict()


def test_an_old_profile_diffs_against_a_new_one_without_error_or_noise() -> None:
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))
    new = shape.profile(orders_table(200), name="orders")
    result = shape.diff(old, new)
    assert result.drifted is False and result.changes == []
    assert shape.diff(new, old).changes == []


def test_the_profile_artifact_declares_its_format_and_an_integer_version(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "p.shape"
    shape.save(shape.profile(orders_table(100), sample=40), path)
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == "shape" and manifest["kind"] == "profile"
    assert isinstance(manifest["format_version"], int) and manifest["format_version"] == 1
    # the records are part of the profile body, which the content id covers
    assert shape.load(str(path)).tables["table"]["sampling"]["method"] == "random"


def test_a_decision_file_of_version_1_without_type_proposals_reads_and_writes_unchanged() -> None:
    from shape.proposals import DecisionFile

    path = FIXTURES / "pre_w2_07_decisions.json"
    text = path.read_text(encoding="utf-8")
    df = DecisionFile.read(path)
    assert df.dumps() == text
    assert json.loads(text)["version"] == 1
    assert {e.proposal.kind for e in df.entries()} <= {"relationship", "pii", "semantic"}


def test_the_profile_registry_does_not_call_a_missing_record_a_change(tmp_path: Path) -> None:
    from shape.registry.profiles import ProfileRegistry

    reg = ProfileRegistry(tmp_path / "reg")
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))
    new = shape.profile(orders_table(200), name="orders")
    reg.save(old, system="s", name="old")
    reg.save(new, system="s", name="new")
    d = reg.diff("s/orders/old", "s/orders/new")
    assert d["changed"] == {} and d["added"] == [] and d["removed"] == []
    other = shape.profile(orders_table(200), name="orders", sample=50)
    reg.save(other, system="s", name="sampled")
    changed = reg.diff("s/orders/new", "s/orders/sampled")["changed"]
    assert changed  # a sample is a different profile: the records do differ then


def test_the_safe_profile_does_not_carry_the_new_records(tmp_path: Path) -> None:
    """The share-safe profile's allow-list is unchanged: none of the new keys reach it."""
    from shape.privacy.safe_profile import SafeConfig, to_safe_profile

    csv = write_orders_csv(tmp_path / "orders.csv", n=600)
    prof = shape.profile(str(csv), sample=300)
    text = to_safe_profile(prof, SafeConfig(k=5)).to_json()
    for key in ("sampling", "adequacy", "type_inference", "population_rows", "parse_shares"):
        assert f'"{key}"' not in text, key
    plain = to_safe_profile(shape.profile(str(csv)), SafeConfig(k=5)).to_json()
    assert set(json.loads(text)["tables"]["orders"]) == set(json.loads(plain)["tables"]["orders"])

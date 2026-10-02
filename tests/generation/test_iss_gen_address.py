"""ISS-gen #17, #18 and #19: the ``address`` strategy.

* #17: it generates from a scope alone (the reference places are the dataset ``us_zip_locations``
  of ``sqllocks-shape-domains``), accepting the scopes a user writes;
* #18: separate ``field`` columns of a table are coherent with each other (one draw per row);
* #19: ``{"dataset": name}`` references keep a schema small, dataclass rows and the loader's
  output are accepted, the reference is compiled once per engine, and a street comes from Shape's
  own pool when the reference has none."""

from __future__ import annotations

import json
import math
from typing import Any

import pyarrow as pa
import pytest

pytest.importorskip("shape_domains")

from shape.builtins.strategies.address import AddressReference  # noqa: E402
from shape.generation import reference as reference_mod  # noqa: E402
from shape.generation.engine import Engine  # noqa: E402
from shape.generation.reference import load_dataset  # noqa: E402
from shape.generation.schema import GenSchema  # noqa: E402
from shape.location import Location  # noqa: E402

FIELDS = ("address_line_1", "city", "state", "postal_code", "latitude", "longitude")


def _engine(
    cols: dict[str, tuple[str, dict[str, Any]]], n: int, seed: int = 1, chunk: int | None = None
) -> Engine:
    cdefs: dict[str, Any] = {
        "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
    }
    for name, (typ, gen) in cols.items():
        cdefs[name] = {"name": name, "type": typ, "generator": gen}
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": seed},
        "tables": {"t": {"name": "t", "primary_key": ["id"], "columns": cdefs}},
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": n}}},
    }
    kw = {"chunk_rows": chunk} if chunk else {}
    return Engine(GenSchema.from_dict(doc), seed=seed, **kw)


def _table(cols: dict[str, tuple[str, dict[str, Any]]], n: int, **kw: Any) -> pa.Table:
    return _engine(cols, n, **kw).generate().tables["t"]


def _field_columns(**extra: Any) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        f: (
            "float" if f in ("latitude", "longitude") else "string",
            {"strategy": "address", "field": f, **extra},
        )
        for f in FIELDS
    }


@pytest.fixture(scope="module")
def zips() -> dict[str, tuple[str, str, float, float]]:
    ds = load_dataset("us_zip_locations") if _registered() else _register_and_load()
    rows = zip(
        ds.column("zip").to_pylist(),
        ds.column("city").to_pylist(),
        ds.column("state").to_pylist(),
        ds.column("lat").to_pylist(),
        ds.column("lng").to_pylist(),
        strict=True,
    )
    return {z: (c, s, la, lo) for z, c, s, la, lo in rows}


def _registered() -> bool:
    try:
        load_dataset("us_zip_locations")
    except reference_mod.DatasetNotFoundError:
        return False
    return True


def _register_and_load() -> Any:
    from shape.generation.domains import load_domain

    load_domain("retail")
    return load_dataset("us_zip_locations")


# ---- #17 --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [
        [{"country": "US", "state": "WA"}],
        [{"state": "WA"}],
        ["WA"],
        "WA",
    ],
)
def test_a_scope_alone_generates_coherent_addresses(
    scope: Any, zips: dict[str, tuple[str, str, float, float]]
) -> None:
    table = _table({"addr": ("struct", {"strategy": "address", "scope": scope})}, 500)
    rows = table["addr"].to_pylist()
    assert len(rows) == 500
    for a in rows:
        city, state, lat, lon = zips[a["postal_code"]]
        assert (a["city"], a["state"]) == (city, state) == (city, "WA")
        assert abs(a["latitude"] - lat) <= 0.0021 and abs(a["longitude"] - lon) <= 0.0021
        assert a["country"] == "US" and a["address_line_1"][0].isdigit()


def test_natural_scopes_zip_city_and_weights(
    zips: dict[str, tuple[str, str, float, float]],
) -> None:
    one = _table(
        {"a": ("struct", {"strategy": "address", "scope": [{"postal_code": "98101"}]})}, 50
    )
    assert {r["postal_code"] for r in one["a"].to_pylist()} == {"98101"}
    seattle = _table(
        {"a": ("struct", {"strategy": "address", "scope": [{"city": "Seattle", "state": "WA"}]})},
        200,
    )
    assert {(r["city"], r["state"]) for r in seattle["a"].to_pylist()} == {("Seattle", "WA")}
    text = _table({"a": ("struct", {"strategy": "address", "scope": ["Seattle, WA", "98101"]})}, 50)
    assert {r["state"] for r in text["a"].to_pylist()} == {"WA"}
    mix = _table(
        {
            "a": (
                "struct",
                {"strategy": "address", "scope": ["WA", "OR"], "weights": [9, 1]},
            )
        },
        4000,
    )
    states = [r["state"] for r in mix["a"].to_pylist()]
    assert set(states) == {"WA", "OR"} and 0.85 < states.count("WA") / len(states) < 0.95
    only = _table(
        {"a": ("struct", {"strategy": "address", "scope": ["WA"], "exclude": ["98101"]})}, 3000
    )
    assert "98101" not in {r["postal_code"] for r in only["a"].to_pylist()}


def test_no_scope_covers_the_reference() -> None:
    states = {
        r["state"]
        for r in _table({"a": ("struct", {"strategy": "address"})}, 3000)["a"].to_pylist()
    }
    assert len(states) > 40


def test_an_unknown_place_or_dataset_names_the_column() -> None:
    with pytest.raises(ValueError, match=r"no place for .*t\.a"):
        _table({"a": ("struct", {"strategy": "address", "scope": [{"postal_code": "00000"}]})}, 3)
    with pytest.raises(ValueError, match=r"dataset 'nowhere'.*t\.a"):
        _table({"a": ("struct", {"strategy": "address", "reference": {"dataset": "nowhere"}})}, 3)
    with pytest.raises(ValueError, match="unsupported address mode"):
        _table({"a": ("struct", {"strategy": "address", "mode": "teleport"})}, 3)


# ---- #18 --------------------------------------------------------------------------------------


def test_separate_columns_are_coherent(zips: dict[str, tuple[str, str, float, float]]) -> None:
    table = _table(_field_columns(scope=["WA", "OR"]), 2000)
    bad = 0
    for r in table.to_pylist():
        city, state, lat, lon = zips[r["postal_code"]]
        if (r["city"], r["state"]) != (city, state):
            bad += 1
        assert abs(r["latitude"] - lat) <= 0.0021 and abs(r["longitude"] - lon) <= 0.0021
    assert bad == 0


def test_the_struct_column_and_the_field_columns_agree() -> None:
    cols = {
        **_field_columns(scope=["WA"]),
        "whole": ("struct", {"strategy": "address", "scope": ["WA"]}),
    }
    table = _table(cols, 300)
    for r in table.to_pylist():
        assert r["whole"]["postal_code"] == r["postal_code"] and r["whole"]["city"] == r["city"]
        assert r["whole"]["address_line_1"] == r["address_line_1"]


def test_columns_agree_for_any_chunking() -> None:
    whole = _table(_field_columns(scope=["WA"]), 1500)
    for chunk in (100, 777):
        assert _table(_field_columns(scope=["WA"]), 1500, chunk=chunk).equals(whole)


def test_two_groups_are_independent_and_a_seed_changes_the_draw() -> None:
    cols = {
        "home": ("string", {"strategy": "address", "field": "postal_code", "scope": ["WA"]}),
        "work": (
            "string",
            {"strategy": "address", "field": "postal_code", "scope": ["WA"], "group": "work"},
        ),
    }
    table = _table(cols, 500)
    assert table["home"].to_pylist() != table["work"].to_pylist()
    again = _table(_field_columns(scope=["WA"]), 100, seed=2)
    assert not again.equals(_table(_field_columns(scope=["WA"]), 100, seed=1))


def test_the_field_names_and_an_unknown_field() -> None:
    table = _table({"z": ("string", {"strategy": "address", "field": "zip", "scope": ["WA"]})}, 5)
    assert all(len(v) == 5 for v in table["z"].to_pylist())
    with pytest.raises(ValueError, match="unknown address field"):
        _table({"z": ("string", {"strategy": "address", "field": "nope"})}, 5)


# ---- #19 --------------------------------------------------------------------------------------


REF = [
    AddressReference("100 N High St", "Columbus", "Franklin", "OH", "43215", "US", 39.96, -83.0),
    AddressReference("5 Main St", "Austin", "Travis", "TX", "73301", "US", 30.27, -97.74),
]


def test_dataclass_rows_make_a_json_schema_and_generate() -> None:
    gen = {"strategy": "address", "reference": REF, "scope": ["OH"], "field": "city"}
    engine = _engine({"c": ("string", gen)}, 20)
    assert set(engine.generate().tables["t"]["c"].to_pylist()) == {"Columbus"}
    document = engine.schema.to_dict()
    assert (
        json.loads(json.dumps(document))["tables"]["t"]["columns"]["c"]["generator"]["reference"][
            0
        ]["city"]
        == "Columbus"
    )


def test_the_loaders_output_and_locations_are_accepted(tmp_path: Any) -> None:
    from shape.location import load_geonames_postal

    path = tmp_path / "US.txt"
    path.write_text(
        "US\t98101\tSeattle\tWashington\tWA\tKing\t033\t\t\t47.6\t-122.33\t4\n"
        "US\t97201\tPortland\tOregon\tOR\tMultnomah\t051\t\t\t45.5\t-122.68\t4\n",
        encoding="utf-8",
    )
    loaded = load_geonames_postal(path, country="US")
    for reference in (loaded, loaded[0]):
        gen = {"strategy": "address", "reference": reference, "scope": ["WA"]}
        rows = _table({"a": ("struct", gen)}, 20)["a"].to_pylist()
        assert {(r["city"], r["postal_code"], r["county"]) for r in rows} == {
            ("Seattle", "98101", "King")
        }
        # no streets in the reference: a number, a street name and a suffix from Shape's pools
        assert all(
            r["address_line_1"].split(" ")[0].isdigit() and "Synthetic" not in r["address_line_1"]
            for r in rows
        )
    gen = {"strategy": "address", "reference": [Location(country="US", state="OR", city="X")]}
    assert _table({"a": ("struct", gen)}, 3)["a"].to_pylist()[0]["state"] == "OR"


def test_a_dataset_reference_keeps_the_schema_small() -> None:
    gen = {
        "strategy": "address",
        "reference": {"dataset": "us_zip_locations"},
        "scope": [{"state": "WA"}],
    }
    engine = _engine({"a": ("struct", gen)}, 10)
    assert len(json.dumps(engine.schema.to_dict())) < 3000
    assert len(engine.generate().tables["t"]) == 10


def test_the_reference_is_compiled_once_per_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from shape.builtins.strategies import address_rows

    calls = []
    real = address_rows.compile_reference

    def counting(*args: Any, **kw: Any) -> Any:
        calls.append(1)
        return real(*args, **kw)

    monkeypatch.setattr(address_rows, "compile_reference", counting)
    engine = _engine({"a": ("struct", {"strategy": "address", "scope": ["WA"]})}, 6000, chunk=500)
    engine.generate()
    assert len(calls) == 1


def test_a_reference_with_streets_keeps_the_suffix_and_the_modes() -> None:
    gen = {"strategy": "address", "reference": REF, "field": "address_line_1"}
    lines = _table({"a": ("string", gen)}, 60)["a"].to_pylist()
    assert all(v.split(" ", 1)[1] in {"N High St", "Main St"} for v in lines)
    exact = _table({"a": ("string", {**gen, "mode": "exact_reference"})}, 60)["a"].to_pylist()
    assert set(exact) == {"100 N High St", "5 Main St"}
    geo = _table({"a": ("string", {**gen, "mode": "geographic"})}, 5)["a"].to_pylist()
    assert all(v.endswith(" Synthetic Way") for v in geo)
    lat = _table({"a": ("float", {**gen, "field": "latitude", "mode": "exact_reference"})}, 20)
    assert set(lat["a"].to_pylist()) <= {39.96, 30.27}
    with pytest.raises(ValueError, match="needs streets"):
        _table({"a": ("string", {"strategy": "address", "mode": "reference", "scope": ["WA"]})}, 3)


def test_the_cost_does_not_depend_on_the_reference_size() -> None:
    import time

    def seconds(n: int) -> float:
        started = time.perf_counter()
        _table({"a": ("struct", {"strategy": "address"})}, n)
        return time.perf_counter() - started

    seconds(10)  # the first call loads the dataset
    assert seconds(3) < 1.0 and math.isfinite(seconds(59_000)) and seconds(59_000) < 1.5

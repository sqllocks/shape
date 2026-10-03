"""Every domain, retail included, is one packaged layout; core holds none of their data or code."""

from __future__ import annotations

from importlib import metadata, resources
from pathlib import Path

from shape_domains._packaged import PackagedDomain
from shape_domains.retail import RetailDomain

from shape.generation.composite import composition
from shape.generation.domains import domain_names, load_domain

EXPECTED = (
    "capital_markets education financial healthcare hr insurance iot manufacturing marketing "
    "pulse real_estate retail supply_chain telecom"
).split()


def test_the_plugin_ships_the_fourteen_domains():
    points = {e.name for e in metadata.entry_points(group="shape.domains")}
    assert set(EXPECTED) <= points
    assert set(EXPECTED) <= set(domain_names())


def test_retail_is_a_packaged_domain_like_the_others():
    assert issubclass(RetailDomain, PackagedDomain)
    root = resources.files("shape_domains").joinpath("data/retail")
    assert {p.name for p in root.iterdir()} == {
        "schema.json",
        "schema_star.json",
        "transforms.json",
        "reference",
    }
    loaded = load_domain("retail")
    assert set(loaded.definition.reference_data) == set(RetailDomain.datasets)
    assert load_domain("retail", mode="star").schema.model.schema_mode == "star"


def test_retail_keeps_its_transform_maps():
    plugin = RetailDomain()
    assert plugin.star_map()["facts"]
    assert plugin.cdm_entities()["customer"]


def test_every_domain_offers_the_same_composition():
    from shape.plugins.host import default_host

    host = default_host()
    offers = {n: host.get("shape.domains", n).composition() for n in EXPECTED}
    first = offers["retail"]
    assert all(o == first for o in offers.values())
    assert first == composition()
    assert len(first.presets) == 6


def test_core_ships_no_retail_data_or_domain_code():
    core = Path(__import__("shape").__file__).parent
    shipped = [p for p in core.rglob("*") if p.is_file() and "retail" in p.name.lower()]
    assert shipped == []
    for path in core.rglob("*.py"):
        text = path.read_text("utf-8").replace("sqllocks-shape-domains", "")
        assert "shape_domains" not in text, path

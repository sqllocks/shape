"""AUD-security2 #281: a pack run writes nothing outside its output directory, whatever the schema
names its domain and the pack names its landing root."""

from __future__ import annotations

import copy

from shape.scenario import PackLoader, PackRunner
from tests.scenario.conftest import PACK, write


def _schema_named(retail, domain: str):
    schema = copy.deepcopy(retail.schema)
    schema.model.domain = domain
    return schema


def test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output(tmp_path, retail):
    out = tmp_path / "work" / "out"
    pack = PackLoader().load(write(tmp_path / "p.yaml", PACK.format(fmt="csv")))
    result = PackRunner().run(pack, _schema_named(retail, "../../ESCAPED"), "fabric_demo", 1, out)
    assert not result.is_success
    assert any("domain name" in e for e in result.errors), result.errors
    outside = [p for p in tmp_path.rglob("*") if "ESCAPED" in p.name]
    assert outside == []


def test_a_refused_landing_root_creates_nothing_outside(tmp_path, retail):
    out = tmp_path / "out"
    out.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (out / "link").symlink_to(elsewhere, target_is_directory=True)
    text = PACK.format(fmt="csv").replace("Files/landing", "link/created_outside")
    pack = PackLoader().load(write(tmp_path / "p.yaml", text))
    result = PackRunner().run(pack, retail, "fabric_demo", 1, out)
    assert not result.is_success
    assert not (elsewhere / "created_outside").exists()

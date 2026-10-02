"""P6-01e: composites (``shape composite``), the six presets and the ad-hoc ``a+b`` form.

The baseline's merged schema and plan for each composite are committed fixtures
(``benchmarks/vs_spindle/fixtures/composites/``, written by ``plan_fixtures.py`` whose ``--check``
proves they still equal the baseline's own output), so these tests need no baseline install. Per
composite they check, end to end through ``shape composite``: the tables, their order, column
order and row counts of the baseline's plan; the merged schema equal to the baseline's apart from
the documented differences (the harness allow-list in ``domain_1to1/allowlist.py``); whole foreign
keys, cross-domain ones included; and the two baseline defects Shape does not reproduce.
The statistical comparison with the baseline is ``domain_1to1/verify.py --domain composite_X``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from shape.api import generate as api_generate
from shape.cli.main import main
from shape.generation.composite import (
    CompositeError,
    compose,
    composition,
    get_preset,
    is_composite,
    preset_names,
    resolve,
)
from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.rules import validate_rules
from shape.plugins import kit
from shape.plugins.host import default_host

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "domain_1to1"))

import allowlist  # noqa: E402
import composites  # noqa: E402
import rowcounts  # noqa: E402
import schema_import  # noqa: E402

FIXTURES = {
    p.stem: json.loads(p.read_text("utf-8"))
    for p in sorted((BENCH / "fixtures" / "composites").glob("*.json"))
}
PRESETS = [k for k, v in FIXTURES.items() if v["preset"] is not None]


def test_the_fixtures_cover_the_six_presets_and_three_ad_hoc_composites():
    assert set(FIXTURES) == {composites.harness_id(s) for s in composites.specs()}
    assert len(PRESETS) == 6 and len(FIXTURES) == 9


@pytest.mark.parametrize("key", PRESETS)
def test_each_preset_is_the_baselines(key):
    baseline = FIXTURES[key]["preset"]
    preset = get_preset(baseline["name"])
    assert preset.description == baseline["description"]
    assert list(preset.domains) == baseline["domains"]
    assert {k: dict(v) for k, v in preset.shared_entities.items()} == baseline["shared_entities"]
    assert preset_names() == list(composites.PRESETS)


def test_the_domains_conform_with_a_composition():
    host = default_host()
    kit.check_domain(host.get("shape.domains", "retail"))
    offered = composition()
    assert {m.domain for ms in offered.mappings.values() for m in ms} <= set(
        host.names("shape.domains")
    )


def _normalised(doc: dict) -> dict:
    """A schema document without what is not a difference: nanosecond date units (every domain
    carries them, for the same Arrow type) and the baseline's output setting (a Shape schema has
    none)."""
    out = json.loads(json.dumps(doc))
    out["generation"]["output"] = {}
    for table in out["tables"].values():
        for column in table["columns"].values():
            column["generator"].pop("unit", None)
    return out


def _expected_differences(key: str) -> set[str]:
    """Exactly what differs from the baseline's merged schema (``allowlist.py``), plus the one
    schema override a domain carries on its own (capital_markets ``industry_name``, P6-01a)."""
    out: set[str] = set()
    for table, column in allowlist.extra_columns(key).items():
        out.add(f"column {table}.{column}")
    for table, column in allowlist.redirected(key).items():
        out.add(f"relationship column {table}.{column}")
    for table, column in allowlist.renamed(key):
        out.add(f"generator {table}.{column}")
    if "capital_markets" in composites.children(FIXTURES[key]["spec"]):
        out.add("generator capital_markets_industry.industry_name")
    return out


def _differences(mine: dict, base: dict) -> set[str]:
    out: set[str] = set()
    # The documents list tables and columns in the order of the domain files (Shape's are sorted);
    # the order of a run is the engine's, which the end-to-end tests check against the plan.
    assert set(mine["tables"]) == set(base["tables"])
    for table, t in mine["tables"].items():
        b = base["tables"][table]
        assert t["primary_key"] == b["primary_key"], table
        assert t["description"] == b["description"], table
        for column in set(t["columns"]) | set(b["columns"]):
            if column not in b["columns"]:
                out.add(f"column {table}.{column}")
            elif column not in t["columns"]:
                out.add(f"missing column {table}.{column}")
            elif t["columns"][column] != b["columns"][column]:
                out.add(f"generator {table}.{column}")
    assert mine["business_rules"] == base["business_rules"]
    assert mine["generation"] == base["generation"]
    assert mine["model"] == base["model"]
    assert len(mine["relationships"]) == len(base["relationships"])
    for r, br in zip(mine["relationships"], base["relationships"], strict=True):
        for field in (
            "name",
            "parent",
            "child",
            "parent_columns",
            "type",
            "optional",
            "cardinality",
        ):
            assert r[field] == br[field], r["name"]
        if r["child_columns"] != br["child_columns"]:
            out.add(f"relationship column {br['child']}.{br['child_columns'][0]}")
    return out


@pytest.mark.parametrize("key", sorted(FIXTURES))
def test_the_merged_schema_is_the_baselines_apart_from_the_named_differences(key):
    mine = _normalised(resolve(FIXTURES[key]["spec"]).schema.to_dict())
    base = _normalised(schema_import.import_dump(FIXTURES[key]["schema"]).to_dict())
    assert _differences(mine, base) == _expected_differences(key)


@pytest.mark.parametrize("key", sorted(FIXTURES))
def test_row_counts_order_and_levels_equal_the_baselines_plan_at_every_scale(key):
    plan = FIXTURES[key]["plan"]
    schema = resolve(FIXTURES[key]["spec"]).schema
    assert set(schema.generation.scales) == set(plan["row_counts"])
    for scale, counts in plan["row_counts"].items():
        counts = {t: n for t, n in counts.items() if t in schema.tables}
        engine = Engine(schema, scale=scale, seed=1)
        assert {t: engine.row_counts[t] for t in engine.order} == counts, scale
    assert Engine(schema, scale="small", seed=1).order == plan["order"]


@pytest.fixture(scope="module", params=sorted(FIXTURES))
def run(request, tmp_path_factory):
    """``shape composite SPEC --format parquet`` at small, end to end."""
    key = request.param
    out = tmp_path_factory.mktemp(key)
    assert (
        main(
            [
                "composite",
                FIXTURES[key]["spec"],
                "--scale",
                "small",
                "-f",
                "parquet",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    tables = {p.stem: pq.read_table(p) for p in out.glob("*.parquet")}
    return key, FIXTURES[key], tables


def test_tables_columns_and_row_counts_equal_the_baselines_at_small(run):
    key, fixture, tables = run
    plan = fixture["plan"]
    assert set(tables) == set(plan["order"])
    extras = allowlist.extra_columns(key)
    for table, columns in plan["columns"].items():
        expected = list(dict.fromkeys(columns))
        found = tables[table].column_names
        if table in extras:  # the bridge is a foreign key: the engine puts it with the others
            assert extras[table] in found, table
            found = [c for c in found if c != extras[table]]
        assert found == expected, table
        assert tables[table].num_rows == plan["row_counts"]["small"][table], table


def test_every_relationship_is_whole_cross_domain_links_included(run):
    key, fixture, tables = run
    schema = resolve(fixture["spec"]).schema
    assert (
        any(r.name.startswith("xdomain_") for r in schema.relationships)
        or key
        in (
            "composite_smart_factory",
            "composite_digital_commerce",
            "composite_telecom_bundle",
        )
        or "supply_chain" in key
    )
    for rel in schema.relationships:
        for child_col, parent_col in zip(rel.child_columns, rel.parent_columns, strict=True):
            values = tables[rel.child][child_col].drop_null()
            assert pc.all(pc.is_in(values, value_set=tables[rel.parent][parent_col])).as_py(), (
                rel.name
            )


def test_business_rules_hold(run):
    _, fixture, tables = run
    schema = resolve(fixture["spec"]).schema
    assert validate_rules(tables, schema) == []


# ---- the baseline defects Shape does not reproduce (allow-list CMP-1, CMP-2) ----------------


@pytest.mark.parametrize("key", ["composite_enterprise", "composite_healthcare_system"])
def test_a_link_to_a_tables_own_key_gets_a_bridge_column_and_keeps_the_key(key):
    """CMP-1. In the baseline half of retail's customers (``customer_id`` is the key) were
    'employees that do not exist'; here the key keeps its values and the bridge is a whole foreign
    key."""
    schema = resolve(FIXTURES[key]["spec"]).schema
    tables = Engine(schema, scale="small", seed=7).generate().tables
    for table, bridge in allowlist.extra_columns(key).items():
        own_key = allowlist.redirected(key)[table]
        assert bridge in tables[table].column_names
        assert schema.tables[table].columns[bridge].generator["strategy"] == "foreign_key"
        parent = schema.tables[table].columns[bridge].generator["ref"].split(".")[0]
        employee_ids = tables[parent]["employee_id"]
        assert pc.all(pc.is_in(tables[table][bridge], value_set=employee_ids)).as_py()
        assert tables[table][own_key].to_pylist() == list(range(1, tables[table].num_rows + 1))
        assert not any(
            r.child == table and r.child_columns == [own_key] for r in schema.relationships
        )


def test_the_baseline_really_has_the_cmp_1_defect():
    """The fixture shows it: the baseline declares customer_id a foreign key to employee_id."""
    rels = {r["name"]: r for r in FIXTURES["composite_enterprise"]["schema"]["relationships"]}
    link = rels["xdomain_person_hr_to_retail"]
    assert (link["child"], link["child_columns"]) == ("retail_customer", ["customer_id"])
    assert (
        "customer_id"
        in FIXTURES["composite_enterprise"]["schema"]["tables"]["retail_customer"]["primary_key"]
    )


def test_each_domain_reads_its_own_dataset_when_names_clash():
    """CMP-2. Education and HR both ship ``department_names``; in the baseline the first one
    loaded served both ('Supply Chain' as a university department)."""
    schema = resolve("campus").schema
    tables = Engine(schema, scale="small", seed=3).generate().tables
    edu = set(
        load_domain("education").definition.reference_data["department_names"]["name"].to_pylist()
    )
    hr = set(load_domain("hr").definition.reference_data["department_names"]["name"].to_pylist())
    assert edu - hr and hr - edu
    assert set(tables["education_department"]["department_name"].to_pylist()) <= edu
    assert set(tables["hr_department"]["department_name"].to_pylist()) <= hr


def test_identical_datasets_are_shared_not_renamed():
    schema = resolve("retail+financial").schema
    datasets = {
        c.generator["dataset"]
        for t in schema.tables.values()
        for c in t.columns.values()
        if "dataset" in c.generator
    }
    assert "us_zip_locations" in datasets and not any("." in d for d in datasets)


# ---- the merge ------------------------------------------------------------------------------


def test_default_shared_entities_link_the_first_domain_of_each_concept():
    schema = resolve("retail+hr+financial").schema
    names = {r.name for r in schema.relationships if r.name.startswith("xdomain_")}
    assert "xdomain_person_retail_to_hr" in names
    assert "xdomain_person_retail_to_financial" in names
    assert "xdomain_organization_hr_to_financial" in names
    bridge = schema.tables["hr_employee"].columns["shared_person_retail_customer_id"]
    assert bridge.generator == {"strategy": "foreign_key", "ref": "retail_customer.customer_id"}


def test_a_bridge_column_takes_the_type_of_the_key_it_points_at():
    """marketing's industry is the organisation primary here; capital_markets keys its company
    by ticker but only ever links *to* a primary, so the bridge is an integer key."""
    schema = resolve("capital_markets+marketing").schema
    bridge = schema.tables["capital_markets_company"].columns[
        "shared_organization_marketing_industry_id"
    ]
    assert bridge.type == schema.tables["marketing_industry"].columns["industry_id"].type
    tables = Engine(schema, scale="small", seed=1).generate().tables
    assert pc.all(
        pc.is_in(
            tables["capital_markets_company"][bridge.name],
            value_set=tables["marketing_industry"]["industry_id"],
        )
    ).as_py()


def test_an_explicit_link_to_a_text_key_makes_a_text_bridge_column():
    domains = {n: load_domain(n) for n in ("capital_markets", "marketing")}
    shared = {
        "organization": {
            "primary": "capital_markets.company",
            "links": {"marketing": "industry.ticker_ref"},
        }
    }
    schema, _ = compose(domains, shared)
    bridge = schema.tables["marketing_industry"].columns["ticker_ref"]
    assert bridge.type == schema.tables["capital_markets_company"].columns["ticker"].type
    assert bridge.generator == {"strategy": "foreign_key", "ref": "capital_markets_company.ticker"}


def test_a_scale_a_domain_lacks_falls_back_to_its_first_scale():
    """pulse has no ``warehouse`` scale: it generates at its first scale, as the baseline does."""
    schema = resolve("pulse+hr").schema
    assert (
        schema.generation.scales["warehouse"]["pulse_rider"]
        == schema.generation.scales["fabric_demo"]["pulse_rider"]
    )


def test_a_single_domain_is_a_composite_of_one():
    schema = resolve("hr").schema
    assert set(schema.tables) == {f"hr_{t}" for t in load_domain("hr").schema.tables}


def test_errors_are_plain():
    with pytest.raises(CompositeError, match="no composite preset or domain named 'nope'"):
        resolve("nope")
    with pytest.raises(CompositeError, match="no domain named 'nope'"):
        resolve("hr+nope")
    with pytest.raises(CompositeError, match="at least one domain"):
        compose({})
    with pytest.raises(CompositeError, match="duplicate domains"):
        resolve("hr+hr")


def test_a_domain_name_is_not_a_composite_but_a_preset_and_a_plus_list_are():
    assert is_composite("enterprise") and is_composite("retail+hr")
    assert not is_composite("retail") and not is_composite("not-a-thing")


def test_the_same_seed_gives_the_same_composite(tmp_path):
    schema = resolve("campus").schema
    a = Engine(schema, scale="small", seed=5).generate().tables
    b = Engine(schema, scale="small", seed=5).generate().tables
    c = Engine(schema, scale="small", seed=6).generate().tables
    assert all(a[t].equals(b[t]) for t in a)
    assert any(not a[t].equals(c[t]) for t in a)


# ---- the command line and the API -----------------------------------------------------------


def run_cli(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_presets_lists_the_six_composites(capsys):
    code, out, _ = run_cli(capsys, "presets", "--composites", "--json")
    assert code == 0
    listed = json.loads(out)
    assert sorted(listed) == sorted(composites.PRESETS)
    assert listed["campus"]["domains"] == ["education", "hr"]
    code, out, _ = run_cli(capsys, "presets", "--composites")
    assert code == 0 and "enterprise" in out and "Domains: retail, hr, financial" in out
    code, out, _ = run_cli(capsys, "presets")
    assert code == 0 and "shape presets --composites" in out


def test_presets_shows_a_composites_row_counts(capsys):
    code, out, _ = run_cli(capsys, "presets", "campus", "--json")
    assert code == 0
    counts = json.loads(out)
    assert sum(counts["small"].values()) == 32_830
    assert counts["small"] == FIXTURES["composite_campus"]["plan"]["row_counts"]["small"]


def test_describe_and_dry_run_accept_a_composite(capsys):
    code, out, _ = run_cli(capsys, "describe", "enterprise", "--json", "--scale", "small")
    assert code == 0
    d = json.loads(out)
    assert d["domain"] == "composite" and len(d["tables"]) == 28
    code, out, _ = run_cli(capsys, "composite", "retail+hr", "--dry-run", "--scale", "small")
    assert code == 0 and "retail_customer" in out and "hr_employee" in out


def test_composite_summary_matches_the_baselines_total(capsys):
    code, out, _ = run_cli(capsys, "composite", "enterprise", "--scale", "small")
    assert code == 0
    assert (
        f"{sum(FIXTURES['composite_enterprise']['plan']['row_counts']['small'].values()):,}" in out
    )


def test_composite_errors_exit_2_with_a_plain_message(capsys):
    code, _, err = run_cli(capsys, "composite", "nope")
    assert code == 2 and "no composite preset or domain named 'nope'" in err
    code, _, err = run_cli(capsys, "generate", "enterprise", "--mode", "star", "--dry-run")
    assert code == 2 and "3nf" in err
    code, _, err = run_cli(capsys, "presets", "retail", "--composites")
    assert code == 2


def test_the_api_generates_a_composite():
    result = api_generate("enterprise", scale="small", seed=1)
    assert len(result.tables) == 28
    assert result.tables["hr_employee"].num_rows == 500
    assert len(api_generate("retail+hr", scale="small").tables) == 18


def test_generate_accepts_a_composite_as_its_target(tmp_path, capsys):
    code, _, _ = run_cli(
        capsys, "generate", "campus", "--scale", "small", "-f", "csv", "-o", tmp_path
    )
    assert code == 0 and (tmp_path / "hr_employee.csv").exists()


# ---- the row-count harness ------------------------------------------------------------------


def _counts():
    return {
        "a_3nf": {
            "planned": {"small": {"t": 3, "u": 4}, "large": {"t": 30, "u": 40}},
            "generated": {"small": {"t": 3, "u": 4}},
        },
        "b_star": {"planned": {"small": {"v": 5}}, "generated": {}},
    }


def test_the_row_count_comparator_finds_every_kind_of_difference():
    base = _counts()
    assert rowcounts.compare(base, _counts()) == []
    raised = _counts()
    raised["a_3nf"]["planned"]["large"]["u"] += 1
    assert len(rowcounts.compare(base, raised)) == 1
    dropped_table = _counts()
    del dropped_table["a_3nf"]["generated"]["small"]["u"]
    assert len(rowcounts.compare(base, dropped_table)) == 1
    dropped_scale = _counts()
    del dropped_scale["a_3nf"]["planned"]["large"]
    assert len(rowcounts.compare(base, dropped_scale)) == 1
    dropped_schema = _counts()
    del dropped_schema["b_star"]
    assert len(rowcounts.compare(base, dropped_schema)) == 1
    raised_generated = _counts()
    raised_generated["a_3nf"]["generated"]["small"]["t"] += 1
    assert len(rowcounts.compare(base, raised_generated)) == 1


def test_the_negative_control_passes_on_a_sound_comparator_and_fails_on_a_blind_one(monkeypatch):
    assert rowcounts.negative_control(_counts()) == []
    monkeypatch.setattr(rowcounts, "compare", lambda a, b: [])
    assert len(rowcounts.negative_control(_counts())) >= 4
    assert rowcounts.check(_counts(), _counts(), quiet=True) == 3


def test_check_exits_1_on_a_difference_and_0_when_equal():
    base, same, off = _counts(), _counts(), _counts()
    off["b_star"]["planned"]["small"]["v"] = 6
    assert rowcounts.check(base, same, quiet=True) == 0
    assert rowcounts.check(base, off, quiet=True) == 1

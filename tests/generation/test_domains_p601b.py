"""P6-01b: the healthcare, hr and insurance domains (``shape.domains`` entries) through the engine.

The plugin ships each domain's schema (3nf and star) and reference datasets. These tests check what
it hands the engine and what the engine makes of it: the tables, column order and row counts of the
baseline's plan at every scale, whole foreign keys, business rules, output that does not depend on
the chunk size or thread count, the schema equal to the baseline's dump (apart from the nanosecond
dates) and the reference data sizes. The statistical comparison with the baseline is
``domain_1to1/verify.py --domain D --impl shape``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.compute as pc
import pytest

from shape.generation.domains import DomainModeError, domain_modes, domain_names, load_domain
from shape.generation.engine import Engine
from shape.generation.rules import validate_rules
from shape.plugins import kit
from shape.plugins.host import default_host

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "domain_1to1"))

import export_domains  # noqa: E402
import schema_import  # noqa: E402

PLAN = json.loads((BENCH / "fixtures" / "plan.json").read_text("utf-8"))
DOMAINS = ("healthcare", "hr", "insurance")
DATASETS = {
    "healthcare": {
        "cpt_codes": 49,
        "icd10_codes": 48,
        "insurance_plans": 23,
        "medication_names": 40,
        "specialties": 32,
        "us_zip_locations": 40_977,
    },
    "hr": {"department_names": 30, "position_titles": 79, "training_courses": 50},
    "insurance": {
        "claim_categories": 25,
        "peril_types": 15,
        "policy_types": 30,
        "us_zip_locations": 40_977,
    },
}


@pytest.fixture(scope="module", params=DOMAINS)
def domain(request):
    return load_domain(request.param)


@pytest.fixture(scope="module")
def small(domain):
    return Engine(domain.schema, scale="small", seed=1042).generate()


@pytest.mark.parametrize("name", DOMAINS)
def test_the_domain_is_discovered_and_conforms(name):
    assert name in domain_names()
    kit.check_domain(default_host().get("shape.domains", name))
    assert domain_modes(name) == ("3nf", "star")


def test_an_unknown_mode_is_refused():
    with pytest.raises(DomainModeError, match="3nf, star"):
        load_domain("education", mode="snowflake")


def test_the_definition_carries_reference_data_and_presets(domain):
    ref = domain.definition.reference_data
    assert {k: v.num_rows for k, v in ref.items()} == DATASETS[domain.name]
    assert set(domain.definition.scale_presets) >= {"small", "medium", "large", "xlarge"}


def test_the_shipped_files_equal_what_the_exporter_writes():
    """``export_domains.py --check`` (needs the baseline checkout for the reference files; the
    schemas alone need only the dumps)."""
    for name in DOMAINS:
        for filename, mode in (("schema.json", "3nf"), ("schema_star.json", "star")):
            shipped = (
                ROOT / "plugins/shape-domains/src/shape_domains/data" / name / filename
            ).read_text("utf-8")
            built = export_domains.export_retail.render_schema(
                export_domains.schema_document(name, mode)
            )
            assert shipped == built, (name, mode)


def test_the_schema_is_the_baselines_dump_with_nanosecond_dates(domain):
    """The one difference: ``unit: ns`` on every non-seasonal temporal column, for T-21 (a).
    Generator keys that no generator reads (``export_domains.UNREAD_KEYS``) are left out."""
    dump = json.loads((BENCH / "fixtures" / "schemas" / f"{domain.name}_3nf.json").read_text())
    assert not [k for k in export_domains.OVERRIDES if k[0] == domain.name]
    mine = domain.schema.to_dict()
    expected = schema_import.import_dump(dump).to_dict()
    for tname, table in mine["tables"].items():
        for cname, col in table["columns"].items():
            gen = col["generator"]
            if gen.get("unit") == "ns":
                assert gen["strategy"] == "temporal" and gen.get("pattern") != "seasonal"
                del gen["unit"]
            unread = export_domains.UNREAD_KEYS.get(gen.get("strategy"), frozenset())
            assert not unread & set(gen), (tname, cname)  # keys no generator reads are left out
            for key in unread:
                expected["tables"][tname]["columns"][cname]["generator"].pop(key, None)
    assert mine == expected


@pytest.mark.parametrize("name", DOMAINS)
@pytest.mark.parametrize("mode", ["3nf", "star"])
def test_row_counts_table_order_and_columns_equal_the_baselines_plan(name, mode):
    loaded = load_domain(name, mode=mode)
    plan = PLAN[f"{name}_{mode}"]
    assert set(loaded.definition.scale_presets) == set(plan["row_counts"])
    for scale, counts in plan["row_counts"].items():
        assert Engine(loaded.schema, scale=scale, seed=1).row_counts == counts, scale
    assert Engine(loaded.schema, scale="small", seed=1).order == plan["order"]


@pytest.mark.parametrize("name", DOMAINS)
def test_small_tables_have_the_baselines_columns_in_order(name):
    loaded = load_domain(name)
    result = Engine(loaded.schema, scale="small", seed=1042).generate()
    plan = PLAN[f"{name}_3nf"]
    assert set(result.tables) == set(plan["order"])
    for table, columns in plan["columns"].items():
        # the baseline lists a key column that has another strategy twice; the frame has it once
        assert result.tables[table].column_names == list(dict.fromkeys(columns)), table
        assert result.tables[table].num_rows == plan["row_counts"]["small"][table]


def test_foreign_keys_are_whole(domain, small):
    for rel in domain.schema.relationships:
        for child_col, parent_col in zip(rel.child_columns, rel.parent_columns, strict=True):
            values = small.tables[rel.child][child_col].drop_null()
            assert pc.all(
                pc.is_in(values, value_set=small.tables[rel.parent][parent_col])
            ).as_py(), (rel.child, child_col)


def test_business_rules_hold(domain, small):
    assert validate_rules(small.tables, domain.schema) == []
    assert small.remaining_violations == []


def test_output_does_not_depend_on_the_chunk_size_or_threads(domain, small, monkeypatch):
    monkeypatch.setenv("SHAPE_THREADS", "1")
    one = Engine(domain.schema, scale="small", seed=1042, chunk_rows=1_000).generate()
    monkeypatch.setenv("SHAPE_THREADS", "4")
    four = Engine(domain.schema, scale="small", seed=1042, chunk_rows=7_000).generate()
    for name, table in small.tables.items():
        assert one.tables[name].equals(table), name
        assert four.tables[name].equals(table), name


def test_the_same_seed_gives_the_same_tables_and_another_seed_does_not(domain):
    a = Engine(domain.schema, scale="small", seed=1).generate()
    b = Engine(domain.schema, scale="small", seed=1).generate()
    c = Engine(domain.schema, scale="small", seed=2).generate()
    assert all(a.tables[t].equals(b.tables[t]) for t in a.tables)
    assert any(not a.tables[t].equals(c.tables[t]) for t in a.tables)


def test_written_parquet_equals_the_generated_tables(domain, small, tmp_path):
    import pyarrow.parquet as pq

    from shape.generation.output import write_engine

    write_engine(Engine(domain.schema, scale="small", seed=1042), "parquet", tmp_path)
    for name, table in small.tables.items():
        assert pq.read_table(tmp_path / f"{name}.parquet").equals(table), name


@pytest.mark.parametrize("name", DOMAINS)
def test_the_star_schema_generates(name):
    loaded = load_domain(name, mode="star")
    result = Engine(loaded.schema, scale="small", seed=3).generate()
    assert validate_rules(result.tables, loaded.schema) == []
    assert set(result.tables) == set(PLAN[f"{name}_star"]["order"])


# ---- facts about single domains ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["healthcare", "insurance"])
def test_the_zip_locations_are_the_ones_of_retail(name):
    zips = load_domain(name).definition.reference_data["us_zip_locations"]
    assert zips.equals(load_domain("retail").definition.reference_data["us_zip_locations"])
    tables = Engine(load_domain(name).schema, scale="small", seed=5).generate().tables
    table, column = {
        "healthcare": ("patient", "zip_code"),
        "insurance": ("policyholder", "address_zip"),
    }[name]
    assert set(tables[table][column].to_pylist()) <= set(zips["zip"].to_pylist())


def test_hr_comments_come_from_the_sentence_pool():
    result = Engine(load_domain("hr").schema, scale="small", seed=1).generate()
    comments = result.tables["performance_review"]["comments"]
    assert 0.1 < comments.null_count / len(comments) < 0.3
    pool = {
        line.rstrip("\n")
        for line in (ROOT / "src/shape/builtins/strategies/pools/sentences.txt")
        .read_text("utf-8")
        .splitlines()
    }
    assert set(comments.drop_null().to_pylist()) <= pool

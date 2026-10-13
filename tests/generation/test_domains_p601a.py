"""P6-01a: the capital_markets, education and financial domains (``shape.domains`` entries) through
the engine.

The plugin ships each domain's schema (3nf and star) and reference datasets. These tests check what
it hands the engine and what the engine makes of it: the tables, column order and row counts of the
baseline's plan at every scale, whole foreign keys, business rules, output that does not depend on
the chunk size or thread count, the schema equal to the baseline's dump (with the documented
differences of ``export_domains.OVERRIDES`` and ``DESCRIPTIONS``) and the reference data equal to
the baseline's files. The statistical comparison with the baseline is
``domain_1to1/verify.py --domain D --impl shape``."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, time, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from shape.generation.domains import DomainModeError, domain_modes, domain_names, load_domain
from shape.generation.engine import Engine
from shape.generation.rules import validate_rules
from shape.plugins import kit
from shape.plugins.host import default_host

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "domain_1to1"))

import export_domains  # noqa: E402
import schema_import  # noqa: E402

PLAN = json.loads((BENCH / "fixtures" / "plan.json").read_text("utf-8"))
DOMAINS = ("capital_markets", "education", "financial")
DATASETS = {
    "capital_markets": {
        "exchanges": 3,
        "gics_sectors": 11,
        "index_memberships": 6,
        "sp500_constituents": 123,
    },
    "education": {"aid_types": 15, "course_catalog": 100, "department_names": 25},
    "financial": {
        "branch_names": 30,
        "merchant_names": 50,
        "transaction_categories": 40,
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


def test_the_schema_is_the_baselines_dump_with_the_documented_differences(domain):
    """Nanosecond dates (``unit: ns`` on every non-seasonal temporal column, for T-21 (a)) and
    ``export_domains.OVERRIDES`` (``capital_markets.industry.industry_name``, which the baseline
    fills with ``""``, and ``daily_price.trade_date`` on trading days) and ``DESCRIPTIONS``;
    generator keys that no generator reads (``export_domains.UNREAD_KEYS``) are left out."""
    dump = json.loads((BENCH / "fixtures" / "schemas" / f"{domain.name}_3nf.json").read_text())
    mine = domain.schema.to_dict()
    expected = schema_import.import_dump(dump).to_dict()
    for tname, table in mine["tables"].items():
        if (domain.name, tname) in export_domains.DESCRIPTIONS:
            assert table["description"] == export_domains.DESCRIPTIONS[(domain.name, tname)]
            table["description"] = expected["tables"][tname]["description"]
        for cname, col in table["columns"].items():
            gen = col["generator"]
            if (domain.name, tname, cname) in export_domains.OVERRIDES:
                assert gen == export_domains.OVERRIDES[(domain.name, tname, cname)]
                col["generator"] = expected["tables"][tname]["columns"][cname]["generator"]
                continue
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


def test_text_keys_of_capital_markets_are_company_tickers():
    loaded = load_domain("capital_markets")
    tables = Engine(loaded.schema, scale="small", seed=1042).generate().tables
    tickers = set(tables["company"]["ticker"].to_pylist())
    assert len(tickers) == 100  # sampled without repeats from the 123 constituents
    for child in ("daily_price", "dividend", "split", "earnings", "insider_transaction", "trade"):
        assert set(tables[child]["ticker"].to_pylist()) <= tickers, child
    assert tables["daily_price"].schema.field("trade_date").type == pa.timestamp("ns")


# The US market's weekday closures in the shipped 2022-2025 range (the exchanges' published
# holiday calendars), written out, not computed, so the test does not share the schema's rules.
US_MARKET_HOLIDAYS = {
    date.fromisoformat(d)
    for d in (
        "2022-01-17 2022-02-21 2022-04-15 2022-05-30 2022-06-20 2022-07-04 2022-09-05 2022-11-24 "
        "2022-12-26 2023-01-02 2023-01-16 2023-02-20 2023-04-07 2023-05-29 2023-06-19 2023-07-04 "
        "2023-09-04 2023-11-23 2023-12-25 2024-01-01 2024-01-15 2024-02-19 2024-03-29 2024-05-27 "
        "2024-06-19 2024-07-04 2024-09-02 2024-11-28 2024-12-25 2025-01-01 2025-01-09 2025-01-20 "
        "2025-02-17 2025-04-18 2025-05-26 2025-06-19 2025-07-04 2025-09-01 2025-11-27 2025-12-25"
    ).split()
}
TRADING_DAYS = {
    day
    for day in (date(2022, 1, 1) + timedelta(days=i) for i in range(1461))
    if day.weekday() < 5 and day not in US_MARKET_HOLIDAYS
}


def _trade_days(values: pa.Array | pa.ChunkedArray) -> list[date]:
    return [v.date() for v in values.to_pylist()]


def test_the_trading_calendar_has_the_markets_day_counts():
    by_year = Counter(day.year for day in TRADING_DAYS)
    assert by_year == {2022: 251, 2023: 250, 2024: 252, 2025: 250}


@pytest.mark.parametrize("mode", ["3nf", "star"])
def test_trade_dates_are_us_trading_days_at_every_scale(mode):
    loaded = load_domain("capital_markets", mode=mode)
    for scale in loaded.definition.scale_presets:
        engine = Engine(loaded.schema, scale=scale, seed=1042)
        total = engine.row_counts["daily_price"]
        # the head and the tail of every preset (the largest has 12.6 million rows)
        spans = [(0, total)] if total <= 400_000 else [(0, 200_000), (total - 200_000, 200_000)]
        for start, n in spans:
            days = _trade_days(engine.generate_column("daily_price", "trade_date", start, n))
            assert set(days) <= TRADING_DAYS, (scale, start, sorted(set(days) - TRADING_DAYS)[:5])
            assert all(day.weekday() < 5 for day in days), scale


def test_trade_dates_cover_every_trading_day_and_only_those():
    loaded = load_domain("capital_markets")
    prices = Engine(loaded.schema, scale="small", seed=1042).generate().tables["daily_price"]
    days = _trade_days(prices["trade_date"])
    assert set(days) == TRADING_DAYS  # 25,200 rows over 1,003 days
    assert all(v.time() == time(0) for v in prices["trade_date"].to_pylist())  # whole days


@pytest.mark.parametrize("mode", ["3nf", "star"])
def test_trade_dates_are_the_same_for_the_same_seed(mode):
    loaded = load_domain("capital_markets", mode=mode)
    first = Engine(loaded.schema, scale="demo", seed=7).generate().tables["daily_price"]
    again = Engine(loaded.schema, scale="demo", seed=7).generate().tables["daily_price"]
    assert first.equals(again)
    other = Engine(loaded.schema, scale="demo", seed=8).generate().tables["daily_price"]
    assert not first["trade_date"].equals(other["trade_date"])


def test_the_baseline_fills_industry_name_with_empty_text():
    loaded = load_domain("capital_markets")
    industry = Engine(loaded.schema, scale="small", seed=1).generate().tables["industry"]
    assert set(industry["industry_name"].to_pylist()) == {""}


def test_financial_borrows_the_zip_locations_of_retail():
    zips = load_domain("financial").definition.reference_data["us_zip_locations"]
    retail = load_domain("retail").definition.reference_data["us_zip_locations"]
    assert zips.equals(retail)
    branch = (
        Engine(load_domain("financial").schema, scale="small", seed=5).generate().tables["branch"]
    )
    assert set(branch["zip_code"].to_pylist()) <= set(zips["zip"].to_pylist())


def test_card_numbers_grow_past_their_width():
    """``{seq:4}`` is a minimum width: card 10000 is ``10000``, as in the baseline."""
    loaded = load_domain("financial")
    card = Engine(loaded.schema, scale="medium", seed=1).generate_table("card")
    last = card["last_four"].to_pylist()
    assert last[0] == "0001" and last[9998] == "9999" and last[9999] == "10000"

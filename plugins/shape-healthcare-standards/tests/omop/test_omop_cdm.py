"""OMOP CDM v5.4 output against the official OHDSI DuckDB DDL (vendored in ``ddl/``).

The CSV and Parquet outputs for the sample tables are loaded into a DuckDB database created
from the official DDL, with the primary and foreign key constraints checked.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from shape_healthcare_standards import contract
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.omop import ddl
from shape_healthcare_standards.omop.mapping import map_tables
from shape_healthcare_standards.omop.sink import OmopSink, write_omop
from shape_healthcare_standards.testing import sample_tables

from shape.plugins import kit

duckdb = pytest.importorskip("duckdb")

DDL_DIR = Path(__file__).parent / "ddl"
VOCABULARY_TABLES = {
    "concept",
    "vocabulary",
    "domain",
    "concept_class",
    "concept_relationship",
    "relationship",
    "concept_synonym",
    "concept_ancestor",
    "source_to_concept_map",
    "drug_strength",
}
ARROW_TO_DUCKDB = {
    pa.int32(): "INTEGER",
    pa.date32(): "DATE",
    pa.timestamp("us"): "TIMESTAMP",
    pa.string(): "VARCHAR",
    pa.decimal128(18, 3): "DECIMAL(18,3)",
}
PK_RE = re.compile(
    r"ALTER TABLE (\w+) ADD CONSTRAINT (\w+) PRIMARY KEY \(([\w, ]+)\);", re.IGNORECASE
)
FK_RE = re.compile(
    r"ALTER TABLE (\w+) ADD CONSTRAINT (\w+) FOREIGN KEY \((\w+)\) "
    r"REFERENCES (\w+) \((\w+)\);",
    re.IGNORECASE,
)


def official(name: str) -> str:
    text = (DDL_DIR / f"OMOPCDM_duckdb_5.4_{name}.sql").read_text(encoding="utf-8")
    return text.replace("@cdmDatabaseSchema.", "")


def new_database() -> Any:
    con = duckdb.connect()
    con.execute(official("ddl"))
    return con


def describe(con: Any, table: str) -> list[tuple[str, str, bool]]:
    rows = con.execute(f"DESCRIBE {table}").fetchall()
    return [(r[0], r[1], r[2] == "YES") for r in rows]


@dataclass
class Loaded:
    con: Any
    out: Path
    fmt: str
    counts: dict[str, int]
    constraints: dict[str, list[str]] = field(default_factory=dict)

    def one(self, sql: str) -> Any:
        return self.con.execute(sql).fetchone()[0]

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return self.con.execute(sql).fetchall()


def load_table(con: Any, out: Path, fmt: str, table: str) -> None:
    columns = describe(con, table)
    names = [c[0] for c in columns]
    path = str(out / f"{table}.{fmt}")
    if fmt == "csv":
        header = con.execute("SELECT * FROM read_csv(?, header=true) LIMIT 0", [path])
        assert [d[0] for d in header.description] == names, f"{table}: csv header differs"
        spec = ", ".join(f"'{n}': '{t}'" for n, t, _ in columns)
        source = f"read_csv(?, header=true, columns={{{spec}}})"
    else:
        shape = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path]).fetchall()
        assert [(r[0], r[1]) for r in shape] == [(n, t) for n, t, _ in columns], (
            f"{table}: parquet columns differ from the DDL"
        )
        source = "read_parquet(?)"
    cols = ", ".join(names)
    con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {source}", [path])


def check_constraints(con: Any, tables: set[str]) -> dict[str, list[str]]:
    """Apply the official PK/FK statements; where DuckDB refuses, run the same rule as SQL."""
    report: dict[str, list[str]] = {
        "pk_applied": [],
        "pk_sql": [],
        "fk_applied": [],
        "fk_sql": [],
        "fk_skipped_vocabulary": [],
        "violations": [],
    }
    for m in PK_RE.finditer(official("primary_keys")):
        table, name, cols = m.group(1).lower(), m.group(2), m.group(3)
        if table not in tables:
            continue
        try:
            con.execute(m.group(0))
            report["pk_applied"].append(name)
            continue
        except duckdb.Error:
            report["pk_sql"].append(name)
        n = con.execute(
            f"SELECT count(*) FROM (SELECT {cols} FROM {table} GROUP BY {cols} HAVING count(*) > 1)"
        ).fetchone()[0]
        nulls = " OR ".join(f"{c.strip()} IS NULL" for c in cols.split(","))
        n += con.execute(f"SELECT count(*) FROM {table} WHERE {nulls}").fetchone()[0]
        if n:
            report["violations"].append(f"{name}: {n}")
    for m in FK_RE.finditer(official("constraints")):
        table, name, col = m.group(1).lower(), m.group(2), m.group(3)
        parent, pcol = m.group(4).lower(), m.group(5)
        if table not in tables:
            continue
        if parent in VOCABULARY_TABLES:
            report["fk_skipped_vocabulary"].append(name)
            continue
        try:
            con.execute(m.group(0))
            report["fk_applied"].append(name)
            continue
        except duckdb.Error:
            report["fk_sql"].append(name)
        n = con.execute(
            f"SELECT count(*) FROM {table} c WHERE c.{col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{pcol} = c.{col})"
        ).fetchone()[0]
        if n:
            report["violations"].append(f"{name}: {n}")
    return report


@pytest.fixture(scope="module", params=["csv", "parquet"])
def loaded(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Loaded:
    fmt = request.param
    out = tmp_path_factory.mktemp(f"omop_{fmt}")
    counts = write_omop(sample_tables(), out, fmt=fmt)
    con = new_database()
    for table in counts:
        load_table(con, out, fmt, table)
    result = Loaded(con, out, fmt, counts)
    result.constraints = check_constraints(con, set(counts))
    return result


def test_official_ddl_is_vendored_and_creates():
    con = new_database()
    names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert set(ddl.TABLE_NAMES) <= names
    assert {"concept", "visit_detail", "person"} <= names


@pytest.mark.parametrize("table", ddl.TABLE_NAMES)
def test_schema_equals_the_official_ddl(table: str):
    con = new_database()
    official_cols = describe(con, table)
    schema = ddl.SCHEMAS[table]
    assert [f.name for f in schema] == [c[0] for c in official_cols]
    assert [ARROW_TO_DUCKDB[f.type] for f in schema] == [c[1] for c in official_cols]
    assert [f.nullable for f in schema] == [c[2] for c in official_cols]
    block = re.search(rf"CREATE TABLE {table} \((.*?)\);", official("ddl"), re.S | re.I)
    assert block is not None
    limits = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"(\w+) varchar\((\d+)\)", block.group(1), re.I)
    }
    assert ddl.VARCHAR_LIMITS.get(table, {}) == limits


def test_every_file_loads_with_ddl_columns_and_constraints_hold(loaded: Loaded):
    assert set(loaded.counts) == {
        "location",
        "care_site",
        "provider",
        "person",
        "observation_period",
        "payer_plan_period",
        "visit_occurrence",
        "condition_occurrence",
        "procedure_occurrence",
        "drug_exposure",
        "cost",
    }
    for table, n in loaded.counts.items():
        assert loaded.one(f"SELECT count(*) FROM {table}") == n, table
    assert loaded.constraints["violations"] == []
    checked = (
        loaded.constraints["pk_applied"]
        + loaded.constraints["pk_sql"]
        + loaded.constraints["fk_applied"]
        + loaded.constraints["fk_sql"]
    )
    assert len(checked) >= 25
    print(
        f"\n[{loaded.fmt}] constraints: " + repr({k: len(v) for k, v in loaded.constraints.items()})
    )


def test_foreign_keys_resolve_with_zero_violations_by_sql(loaded: Loaded):
    con = loaded.con
    for m in FK_RE.finditer(official("constraints")):
        table, col, parent, pcol = m.group(1).lower(), m.group(3), m.group(4).lower(), m.group(5)
        if table not in loaded.counts or parent in VOCABULARY_TABLES:
            continue
        n = con.execute(
            f"SELECT count(*) FROM {table} c WHERE c.{col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{pcol} = c.{col})"
        ).fetchone()[0]
        assert n == 0, f"{table}.{col} -> {parent}.{pcol}: {n} orphans"


def test_person_and_location(loaded: Loaded):
    members = sample_tables()["member"].to_pylist()
    assert loaded.counts["person"] == len(members) == 4
    rows = {
        r[0]: r
        for r in loaded.rows(
            "SELECT person_source_value, gender_concept_id, year_of_birth, month_of_birth, "
            "day_of_birth, race_concept_id, ethnicity_concept_id, race_source_value, "
            "location_id, birth_datetime, person_id FROM person"
        )
    }
    assert set(rows) == {m["member_id"] for m in members}
    alex = rows["SYN100001-00"]
    assert alex[1:7] == (8507, 1978, 4, 12, 8527, 38003564)
    assert alex[9] == dt.datetime(1978, 4, 12)
    assert rows["SYN100001-01"][1] == 8532 and rows["SYN100001-01"][5] == 8516
    assert rows["SYN100001-02"][6] == 38003563
    assert rows["SYN200001-00"][5] == 8515 and rows["SYN200001-00"][7] == "Asian"
    assert rows["SYN100001-00"][8] == rows["SYN100001-02"][8]
    assert rows["SYN100001-00"][8] != rows["SYN200001-00"][8]
    assert loaded.counts["location"] == 3
    assert loaded.one("SELECT zip FROM location WHERE city = 'Peoria'") == "616040001"
    assert loaded.one("SELECT count(*) FROM person WHERE provider_id IS NOT NULL") == 4


def test_ids_are_int32_positive_and_unique(loaded: Loaded):
    for table, col in [
        ("person", "person_id"),
        ("visit_occurrence", "visit_occurrence_id"),
        ("cost", "cost_id"),
    ]:
        assert loaded.one(f"SELECT min({col}) FROM {table}") >= 1
        assert loaded.one(f"SELECT count(DISTINCT {col}) FROM {table}") == loaded.counts[table]


def test_observation_and_payer_plan_periods(loaded: Loaded):
    assert loaded.counts["observation_period"] == loaded.counts["payer_plan_period"] == 4
    rows = loaded.rows(
        "SELECT p.person_source_value, o.observation_period_start_date, "
        "o.observation_period_end_date, o.period_type_concept_id "
        "FROM observation_period o JOIN person p USING (person_id) ORDER BY 1"
    )
    by_member = {r[0]: r for r in rows}
    assert by_member["SYN100001-01"][1:3] == (dt.date(2023, 1, 1), dt.date(2024, 6, 30))
    open_end = dt.date(2024, 6, 30)
    assert by_member["SYN100001-00"][1:3] == (dt.date(2023, 1, 1), open_end)
    assert by_member["SYN200001-00"][1:3] == (dt.date(2024, 1, 1), open_end)
    assert {r[3] for r in rows} == {32813}
    plan = loaded.rows(
        "SELECT plan_source_value, sponsor_source_value, family_source_value, "
        "payer_source_value, stop_reason_source_value FROM payer_plan_period "
        "ORDER BY payer_plan_period_start_date, plan_source_value, family_source_value"
    )
    assert ("PLN-COM-1", "GRP0001", "SYN100001", "Example Health Plan", "voluntary") in plan


def test_visits(loaded: Loaded):
    claims = sample_tables()["medical_claim"].to_pylist()
    assert loaded.counts["visit_occurrence"] == len(claims) == 4
    rows = {
        r[0]: r
        for r in loaded.rows(
            "SELECT visit_source_value, visit_concept_id, visit_start_date, visit_end_date, "
            "discharged_to_source_value, care_site_id, provider_id FROM visit_occurrence"
        )
    }
    assert rows["CLM-P-0001"][1] == 9202 and rows["CLM-P-0003"][1] == 9202
    inpatient = rows["CLM-I-0001"]
    assert inpatient[1:5] == (9201, dt.date(2024, 6, 10), dt.date(2024, 6, 14), "01")
    assert inpatient[5] is not None and rows["CLM-P-0001"][5] is not None
    assert rows["CLM-P-0002"][5] is None


def test_conditions_round_trip_with_dotted_source_codes(loaded: Loaded):
    diagnoses = sample_tables()["claim_diagnosis"].to_pylist()
    assert loaded.counts["condition_occurrence"] == len(diagnoses) == 9
    codes = sorted(
        r[0] for r in loaded.rows("SELECT condition_source_value FROM condition_occurrence")
    )
    assert codes == sorted(
        c if len(c) <= 3 else f"{c[:3]}.{c[3:]}" for c in (d["icd10_code"] for d in diagnoses)
    )
    assert {"E11.9", "E11.65", "N18.30", "I10", "Z79.4"} <= set(codes)
    assert (
        loaded.one("SELECT count(*) FROM condition_occurrence WHERE condition_concept_id <> 0") == 0
    )
    assert (
        loaded.one(
            "SELECT count(*) FROM condition_occurrence WHERE condition_source_concept_id <> 0"
        )
        == 0
    )
    start = loaded.one(
        "SELECT min(condition_start_date) FROM condition_occurrence "
        "WHERE condition_source_value = 'E11.65'"
    )
    assert start == dt.date(2024, 6, 10)
    assert (
        loaded.one(
            "SELECT count(*) FROM condition_occurrence c JOIN visit_occurrence v "
            "USING (visit_occurrence_id) WHERE c.person_id <> v.person_id"
        )
        == 0
    )


def test_procedures_from_lines_and_icd10pcs(loaded: Loaded):
    t = sample_tables()
    with_code = [ln for ln in t["medical_claim_line"].to_pylist() if ln["procedure_code"]]
    assert (
        loaded.counts["procedure_occurrence"] == len(with_code) + t["claim_procedure"].num_rows == 7
    )
    rows = loaded.rows(
        "SELECT procedure_source_value, modifier_source_value, quantity, procedure_date "
        "FROM procedure_occurrence"
    )
    by_code = {r[0]: r for r in rows}
    assert by_code["83036"][1] == "QW"
    assert by_code["97110"][2] == 2
    assert by_code["0BH17EZ"][3] == dt.date(2024, 6, 11)


def test_drug_exposure_only_paid_claims(loaded: Loaded):
    rx = sample_tables()["pharmacy_claim"].to_pylist()
    paid = [r for r in rx if r["claim_status"] == "paid"]
    assert loaded.counts["drug_exposure"] == len(paid) == 1
    row = loaded.rows(
        "SELECT drug_source_value, quantity, days_supply, refills, drug_exposure_start_date, "
        "drug_exposure_end_date, route_source_value, route_concept_id, drug_concept_id, "
        "drug_type_concept_id, provider_id FROM drug_exposure"
    )[0]
    assert row[0] == "00093726001" and row[1] == Decimal("60.000") and row[2] == 30
    assert row[3] == 0
    assert row[4:6] == (dt.date(2024, 2, 6), dt.date(2024, 3, 6))
    assert row[6] == "ORAL" and row[7] == 0 and row[8] == 0 and row[9] == 32869
    assert row[10] is not None
    assert loaded.one("SELECT count(*) FROM drug_exposure WHERE person_id IS NULL") == 0


def test_cost_totals_match_the_claims(loaded: Loaded):
    t = sample_tables()
    claims = t["medical_claim"].to_pylist()
    rx = [r for r in t["pharmacy_claim"].to_pylist() if r["claim_status"] == "paid"]
    assert loaded.counts["cost"] == len(claims) + len(rx) == 5

    def d(v: float) -> Decimal:
        return Decimal(str(v))

    charge = sum(d(c["total_billed"]) for c in claims) + sum(
        d(r["ingredient_cost"]) + d(r["dispensing_fee"]) for r in rx
    )
    payer = sum(d(c["total_paid"]) for c in claims) + sum(d(r["plan_paid"]) for r in rx)
    patient = sum(
        d(c["member_copay"]) + d(c["member_coinsurance"]) + d(c["member_deductible"])
        for c in claims
    ) + sum(d(r["patient_pay"]) for r in rx)
    total = loaded.rows(
        "SELECT sum(total_charge), sum(paid_by_payer), sum(paid_by_patient), sum(total_paid), "
        "sum(amount_allowed) FROM cost"
    )[0]
    assert total[0] == charge == Decimal("43956.5")
    assert total[1] == payer == Decimal("17991")
    assert total[2] == patient == Decimal("1175")
    assert total[3] == payer + patient
    assert total[4] == sum(d(c["total_allowed"]) for c in claims)
    visit = {
        r[0]: r[1:]
        for r in loaded.rows(
            "SELECT v.visit_source_value, c.total_charge, c.total_paid, c.paid_patient_copay, "
            "c.drg_source_value FROM cost c "
            "JOIN visit_occurrence v ON c.cost_event_id = v.visit_occurrence_id "
            "WHERE c.cost_domain_id = 'Visit'"
        )
    }
    assert visit["CLM-P-0001"] == (Decimal("300"), Decimal("210"), Decimal("30"), None)
    assert visit["CLM-I-0001"] == (Decimal("42000"), Decimal("18000"), Decimal("0"), "871")
    drug = loaded.rows(
        "SELECT c.paid_ingredient_cost, c.paid_dispensing_fee, c.total_paid FROM cost c "
        "JOIN drug_exposure d ON c.cost_event_id = d.drug_exposure_id "
        "WHERE c.cost_domain_id = 'Drug'"
    )
    assert drug == [(Decimal("4.5"), Decimal("1.5"), Decimal("6"))]
    assert loaded.one("SELECT count(*) FROM cost WHERE payer_plan_period_id IS NOT NULL") == 5


def test_providers_and_care_sites(loaded: Loaded):
    assert loaded.counts["provider"] == 7 and loaded.counts["care_site"] == 1
    row = loaded.rows(
        "SELECT provider_name, specialty_source_value, specialty_concept_id, care_site_id "
        "FROM provider WHERE npi = (SELECT npi FROM provider WHERE provider_name = 'Taylor Nguyen')"
    )[0]
    assert row[:3] == ("Taylor Nguyen", "207Q00000X", 0)
    site = loaded.rows("SELECT care_site_name, location_id, care_site_source_value FROM care_site")[
        0
    ]
    assert site[0] == "Example General Hospital" and site[1] is not None
    assert loaded.one("SELECT count(*) FROM provider WHERE care_site_id IS NOT NULL") == 1


def test_output_is_deterministic_and_leaves_no_staging(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_omop(sample_tables(), a)
    write_omop(sample_tables(), b)
    names = sorted(p.name for p in a.iterdir())
    assert names == sorted(p.name for p in b.iterdir())
    assert all(n.endswith(".csv") and n == n.lower() for n in names)
    for n in names:
        assert (a / n).read_bytes() == (b / n).read_bytes(), n
    write_omop(sample_tables(), a, fmt="parquet")
    assert not [p for p in a.iterdir() if p.name.startswith(".")]


def test_csv_has_header_iso_dates_and_empty_nulls(tmp_path: Path):
    write_omop(sample_tables(), tmp_path)
    lines = (tmp_path / "visit_occurrence.csv").read_text(encoding="utf-8").splitlines()
    header = lines[0].replace('"', "").split(",")
    assert header == ddl.SCHEMAS["visit_occurrence"].names
    assert re.search(r",2024-06-10,", "\n".join(lines))
    assert ",," in lines[1]


def test_concept_map_supplies_standard_concepts(tmp_path: Path):
    fake = {"condition:E11.9": 111, "I10": 222, "procedure:99213": 333, "drug:00093726001": 444}
    fake["route:ORAL"] = 555
    write_omop(sample_tables(), tmp_path, concept_map=fake)
    con = new_database()
    for table in ("person", "visit_occurrence", "condition_occurrence", "procedure_occurrence"):
        load_table(con, tmp_path, "csv", table)
    got = dict(
        con.execute(
            "SELECT condition_source_value, condition_concept_id FROM condition_occurrence"
        ).fetchall()
    )
    assert got["E11.9"] == 111 and got["I10"] == 222 and got["M54.5"] == 0
    proc = dict(
        con.execute(
            "SELECT procedure_source_value, procedure_concept_id FROM procedure_occurrence"
        ).fetchall()
    )
    assert proc["99213"] == 333 and proc["83036"] == 0
    load_table(con, tmp_path, "csv", "drug_exposure")
    assert con.execute(
        "SELECT drug_concept_id, route_concept_id FROM drug_exposure"
    ).fetchall() == [(444, 555)]


def test_unsupported_claims_are_left_out():
    t = sample_tables()
    claims = t["medical_claim"].to_pylist()
    claims[0]["claim_status"] = "reversed"
    claims[1]["claim_frequency_code"] = "8"
    t["medical_claim"] = pa.Table.from_pylist(
        claims, schema=contract.contract_schema("medical_claim")
    )
    rows = map_tables(TableSet(t))
    assert {v["visit_source_value"] for v in rows["visit_occurrence"]} == {
        "CLM-P-0003",
        "CLM-I-0001",
    }
    assert len(rows["condition_occurrence"]) == 6


def test_replaced_claims_are_not_double_counted():
    t = sample_tables()
    claims = t["medical_claim"].to_pylist()
    orig = dict(
        claims[2], claim_id="CLM-P-0003-ORIG", claim_frequency_code="1", original_claim_id=None
    )
    claims.append(orig)
    t["medical_claim"] = pa.Table.from_pylist(
        claims, schema=contract.contract_schema("medical_claim")
    )
    ids = {v["visit_source_value"] for v in map_tables(TableSet(t))["visit_occurrence"]}
    assert "CLM-P-0003-ORIG" not in ids and "CLM-P-0003" in ids


def test_member_alone_gives_person_location_and_death(tmp_path: Path):
    members = sample_tables()["member"].to_pylist()
    members[3]["deceased_date"] = dt.date(2024, 8, 1)
    schema = contract.contract_schema("member")
    batch = pa.RecordBatch.from_pylist(members, schema=schema)
    n = OmopSink().write(str(tmp_path), "member", iter([batch]))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["death.csv", "location.csv", "person.csv"]
    assert n == 4 + 2 + 1
    con = new_database()
    for table in ("person", "location", "death"):
        load_table(con, tmp_path, "csv", table)
    assert con.execute("SELECT death_date, death_type_concept_id FROM death").fetchall() == [
        (dt.date(2024, 8, 1), 32813)
    ]
    assert con.execute(
        "SELECT count(*) FROM death d JOIN person p USING (person_id) "
        "WHERE p.person_source_value = 'SYN200001-00'"
    ).fetchone() == (1,)


def test_sink_options_tables_and_parquet(tmp_path: Path):
    t = sample_tables()
    companions = {k: v for k, v in t.items() if k != "member"}
    n = OmopSink().write(
        str(tmp_path), "member", t["member"].to_batches(), tables=companions, format="parquet"
    )
    counts = write_omop(t, tmp_path / "direct", fmt="parquet")
    assert n == sum(counts.values()) and n > 40
    assert (tmp_path / "person.parquet").exists()
    assert not (tmp_path / "person.csv").exists()


def test_member_is_required_and_format_is_checked(tmp_path: Path):
    t = sample_tables()
    with pytest.raises(contract.ContractError, match="member"):
        write_omop({"eligibility": t["eligibility"]}, tmp_path)
    with pytest.raises(ValueError, match="format"):
        write_omop(t, tmp_path, fmt="xlsx")


def test_events_for_unknown_members_are_dropped(tmp_path: Path):
    t = sample_tables()
    t["member"] = t["member"].slice(1)
    counts = write_omop(t, tmp_path)
    assert counts["person"] == 3
    assert counts["drug_exposure"] == 0 and counts["visit_occurrence"] == 3


def test_long_text_is_cut_to_the_ddl_limit(tmp_path: Path):
    members = sample_tables()["member"].to_pylist()[:1]
    members[0]["member_id"] = "M" * 80
    members[0]["state"] = "ILLINOIS"
    batch = pa.RecordBatch.from_pylist(members, schema=contract.contract_schema("member"))
    OmopSink().write(str(tmp_path), "member", [batch])
    con = new_database()
    load_table(con, tmp_path, "csv", "person")
    load_table(con, tmp_path, "csv", "location")
    assert con.execute("SELECT length(person_source_value) FROM person").fetchone() == (50,)
    assert con.execute("SELECT state FROM location").fetchone() == ("IL",)


def test_plugin_kit_check_sink(tmp_path: Path):
    row = sample_tables()["member"].to_pylist()[0]
    for col in ("address_line1", "address_line2", "city", "state", "zip"):
        row[col] = None
    batch = pa.RecordBatch.from_pylist([row], schema=contract.contract_schema("member"))
    kit.check_sink(OmopSink(), str(tmp_path / "kit"), [batch], table="member")
    assert (tmp_path / "kit" / "person.csv").exists()

"""The documented, intentional differences between Shape's database profile and the baseline's.

P6-08 matched the baseline field for field. Nine behaviours of the baseline are defects, and
Shape fixes them (P6-08b: owner decisions of 2026-10-01). Everything else must still equal the
baseline, so the parity check does not ignore fields: it turns the baseline's profile into the
profile the *corrected* behaviour produces, using only the baseline's own numbers plus the
named inputs below, and compares Shape's output with that, field by field, under the unchanged
T-22 rules. Each adjustment is counted and printed, and a full run fails if a listed fix is
never exercised.

The allow-list is exactly these fields, and nothing else is adjusted:

    FIX-1  sample-based ratios (denominator = rows actually sampled, not the catalog count)
           column: null_rate, cardinality_ratio, is_unique, is_enum, enum_values
    FIX-2  a foreign key the sampled data shows is reported when none is declared
           dataset: relationships;  table: detected_fks;  column: is_foreign_key, fk_ref_table
           (only for the links named in DATA_LINKS that the baseline did not name-link)
    FIX-3  a name-inferred foreign key marks its column
           column: is_foreign_key, fk_ref_table (only columns in the baseline's detected_fks)
    FIX-4  the sample is spread over the table (deterministic hash), not the first rows.
           This changes WHICH ROWS are read, so for a table larger than the requested sample
           every column field computed from the sampled rows may differ from the baseline's:
           column: null_count, cardinality, min_value, max_value, mean, std, enum_values
           (and, through them, FIX-1's null_rate, cardinality_ratio, is_unique, is_enum).
           Nothing else may change. The values are not waved through: the harness picks the
           rows the documented method selects, hands exactly those rows to the baseline's own
           profiler (db_dump.py --rows-pickle) and Shape must equal the baseline's result on
           them. Tables no larger than the sample are read whole and must equal the baseline.
    FIX-5  no sampled row: column null_rate, cardinality_ratio, is_unique are null (unknown)
           where the baseline says 0.0 / 0.0 / false.
    FIX-6  is_unique is unique among the non-null sampled values, and null below
           UNIQUE_MIN_VALUES of them (the baseline: unique over all rows; a 1-row sample makes
           every column unique).
    FIX-7  declared keys stay authoritative, and undeclared ``*_id``/``*Id`` columns elsewhere
           are still linked (data first, then names); every link says where it came from.
           dataset: relationships (the added links, and ``evidence`` on every link);
           table: detected_fks; column: is_foreign_key, fk_ref_table, fk_evidence (new field,
           ``declared``/``data``/``name``, null on a column that is no foreign key).
           Only the links named in DATA_LINKS and NAME_LINKS.
    FIX-8  ``id``/``key`` count in a column name only as a whole word: the baseline's name
           links named in BOGUS_NAME_LINKS (``paid`` -> a table ``pa``) are dropped.
           dataset: relationships; table: detected_fks
    FIX-9  a guessed primary key is never a foreign key, and must be named like the table or
           ``id`` and unique and non-null in the sample; otherwise there is none.
           table: primary_key; column: is_primary_key (only the tables named in PK_GUESS, and
           for them the expected key is named there).

Inputs: the number of rows each table sampled is *derived* here (``min(requested, baseline
row_count)``, 0 for an unsampled or unreadable table), and Shape's ``sampled_rows`` must equal
it; DATA_LINKS, NAME_LINKS, BOGUS_NAME_LINKS and PK_GUESS name, per case, facts about the
fixture (not read from Shape's output).
"""

from __future__ import annotations

import copy
from collections import Counter

ENUM_MAX_CARDINALITY = 50
ENUM_MAX_RATIO = 0.05
UNIQUE_RATIO = 0.99
UNIQUE_MIN_VALUES = 2
DEFAULT_SAMPLE_ROWS = 1000

FIXES = ("FIX-1", "FIX-2", "FIX-3", "FIX-4", "FIX-5", "FIX-6", "FIX-7", "FIX-8", "FIX-9")

Link = tuple[str, str, str]  # (child table, child column, parent table)

# case -> links the sampled rows show although the schema declares no key for them
DATA_LINKS: dict[str, list[Link]] = {
    "id_named": [("orders", "customer_id", "customer")],
    "mixed_keys": [("orders", "product_id", "product")],
}
# case -> links only the column names suggest, beside declared keys (the baseline infers none
# when any key is declared)
NAME_LINKS: dict[str, list[Link]] = {
    "mixed_keys": [("orders", "region_key", "region")],
}
# case -> name links the baseline makes from ``paid``/``valid``-style columns that are not ids
BOGUS_NAME_LINKS: dict[str, list[Link]] = {
    "key_names": [("invoices", "valid", "val"), ("invoices", "paid", "pa")],
}
# case -> {table: the primary key the corrected guess gives}, for tables whose guess differs
PK_GUESS: dict[str, dict[str, list[str]]] = {
    "key_names": {"invoices": ["invoice_id"], "notes": []},
}

FIX1_FIELDS = ("null_rate", "cardinality_ratio", "is_unique", "is_enum", "enum_values")
FIX4_FIELDS = ("null_count", "cardinality", "min_value", "max_value", "mean", "std", "enum_values")
LINK_FIELDS = ("is_foreign_key", "fk_ref_table")


def expected_sampled_rows(sp_table: dict, requested: int, unreadable: bool) -> int:
    if unreadable:
        return 0
    return min(requested, sp_table["row_count"])


def expected_method(sp_table: dict, requested: int, unreadable: bool) -> str:
    if unreadable or requested <= 0:
        return "none"
    return "checksum spread" if sp_table["row_count"] > requested else "all rows"


def spread_tables(sp: dict, requested: int, unreadable: tuple[str, ...]) -> list[str]:
    """The tables a spread sample reads strictly fewer rows of than the table holds."""
    if requested <= 0:
        return []
    return [
        t for t, v in sp["tables"].items() if v["row_count"] > requested and t not in unreadable
    ]


def _fix4(out: dict, spread: dict | None, names: list[str], tally: Counter) -> None:
    """FIX-4: for the tables that are spread, take the sample-derived fields from the baseline
    run on the rows the documented method selects."""
    for tname in names:
        for cname, col in out["tables"][tname]["columns"].items():
            src = spread["tables"][tname]["columns"][cname]
            for f in FIX4_FIELDS:
                tally[f"FIX-4 {f}"] += col[f] != src[f]
                col[f] = src[f]


def _fix156(table: dict, n: int, tally: Counter) -> None:
    """FIX-1 (denominator = rows sampled), FIX-5 (no rows: null) and FIX-6 (null-aware)."""
    denominator = max(n, 1)
    for col in table["columns"].values():
        before = {f: col[f] for f in FIX1_FIELDS}
        card, nulls = col["cardinality"], col["null_count"]
        ratio = card / denominator
        # FIX-1 (the rules as P6-08b's first round stated them)
        col["null_rate"] = nulls / denominator
        col["cardinality_ratio"] = ratio
        col["is_unique"] = ratio > UNIQUE_RATIO if card > 0 else False
        col["is_enum"] = card > 0 and (card <= ENUM_MAX_CARDINALITY or ratio < ENUM_MAX_RATIO)
        if not col["is_enum"]:
            col["enum_values"] = None  # the baseline's frequencies stay when still an enum
        for f in FIX1_FIELDS:
            tally[f"FIX-1 {f}"] += before[f] != col[f]
        # FIX-5 and FIX-6 on top
        was = {f: col[f] for f in ("null_rate", "cardinality_ratio", "is_unique")}
        non_null = n - nulls
        if n == 0:
            col["null_rate"] = col["cardinality_ratio"] = col["is_unique"] = None
        elif non_null < UNIQUE_MIN_VALUES:
            col["is_unique"] = None
        else:
            col["is_unique"] = card / non_null > UNIQUE_RATIO
        for f, old in was.items():
            fix = "FIX-6" if n > 0 else "FIX-5"
            tally[f"{fix} {f}"] += old != col[f]


def corrected_baseline(
    sp: dict,
    case: str,
    requested: int,
    unreadable: tuple[str, ...],
    declared_fks: bool,
    declared_pks: set[str],
    tally: Counter,
    spread: dict | None = None,
) -> tuple[dict, dict[str, int], dict[tuple[str, str], str | None]]:
    """``(baseline profile with the documented corrections applied, expected sampled_rows per
    table, expected fk_evidence per (table, column))``. ``tally`` counts the fields that
    actually changed, by fix. ``spread`` is the baseline's profile of the rows the documented
    sample selects (needed when some table is larger than the sample)."""
    out = copy.deepcopy(sp)
    sampled: dict[str, int] = {}
    big = spread_tables(sp, requested, unreadable)
    if big:
        assert spread is not None, "tables larger than the sample need the baseline's spread run"
        _fix4(out, spread, big, tally)
    for tname, table in out["tables"].items():
        n = expected_sampled_rows(sp["tables"][tname], requested, tname in unreadable)
        sampled[tname] = n
        _fix156(table, n, tally)
    evidence = _links_and_keys(out, sp, case, declared_fks, declared_pks, tally)
    return out, sampled, evidence


def _fix9(out: dict, sp: dict, case: str, declared_pks: set[str], tally: Counter) -> None:
    """FIX-9: the keys of tables that declare none (named per case; all others stay as the
    baseline guessed them)."""
    guess = PK_GUESS.get(case, {})
    for tname, table in out["tables"].items():
        if tname in declared_pks:
            assert tname not in guess, f"{tname} declares its key"
            continue
        want = list(guess.get(tname, table["primary_key"]))
        if tname in guess:
            assert sp["tables"][tname]["primary_key"] != want, f"{case}:{tname} is no FIX-9 case"
        tally["FIX-9 table.primary_key"] += table["primary_key"] != want
        table["primary_key"] = want
        for cname, col in table["columns"].items():
            tally["FIX-9 is_primary_key"] += col["is_primary_key"] != (cname in want)
            col["is_primary_key"] = cname in want


def _links_and_keys(
    out: dict,
    sp: dict,
    case: str,
    declared_fks: bool,
    declared_pks: set[str],
    tally: Counter,
) -> dict[tuple[str, str], str | None]:
    """FIX-9 (keys), then FIX-2/3/7/8 (links) and their evidence. Returns the expected
    ``fk_evidence`` of every column."""
    _fix9(out, sp, case, declared_pks, tally)
    data = DATA_LINKS.get(case, [])
    bogus = BOGUS_NAME_LINKS.get(case, [])
    baseline_links = [
        (r["child"], c, r["parent"]) for r in sp["relationships"] for c in r["child_columns"]
    ]
    for lk in bogus:
        assert lk in baseline_links, f"{case}: the baseline did not make the link {lk}"
    if declared_fks:  # the baseline links nothing else
        declared = baseline_links
        name = list(NAME_LINKS.get(case, []))
    else:
        declared = []
        name = [lk for lk in baseline_links if lk not in bogus and lk not in data]
    links = [(lk, "declared") for lk in declared] + [(lk, "data") for lk in data]
    links += [(lk, "name") for lk in name]

    if not declared_fks:  # the baseline's name links are re-derived below
        for table in out["tables"].values():
            table["detected_fks"] = {}
    rels: list[dict] = (
        [dict(r, evidence="declared") for r in sp["relationships"]] if declared_fks else []
    )
    for (child, ccol, parent), kind in links:
        if kind == "declared":
            continue
        table = out["tables"][child]
        table["detected_fks"][ccol] = parent
        pk = out["tables"][parent]["primary_key"]
        rels.append(
            {
                "name": f"fk_{child}_{ccol}",
                "parent": parent,
                "child": child,
                "parent_columns": list(pk) if kind == "data" else list(pk[:1]) or [ccol],
                "child_columns": [ccol],
                "type": "one_to_many",
                "evidence": kind,
            }
        )
    out["relationships"] = rels
    tally["FIX-7 relationships.evidence"] += len(rels)

    evidence: dict[tuple[str, str], str | None] = {
        (t, c): None for t, v in out["tables"].items() for c in v["columns"]
    }
    for (child, ccol, parent), kind in links:
        evidence[(child, ccol)] = kind
        col = out["tables"][child]["columns"][ccol]
        col["is_foreign_key"], col["fk_ref_table"] = True, parent
    # a column the baseline had marked and no corrected link keeps is a bug in this oracle
    for tname, t in out["tables"].items():
        for cname, col in t["columns"].items():
            assert col["is_foreign_key"] == (evidence[(tname, cname)] is not None), (tname, cname)

    # what changed against the baseline, by fix
    for lk, kind in links:
        child, ccol, parent = lk
        old = sp["tables"][child]["columns"][ccol]
        changed = {
            "is_foreign_key": old["is_foreign_key"] is not True,
            "fk_ref_table": old["fk_ref_table"] != parent,
        }
        if kind == "declared":
            fix = None
        elif declared_fks:
            fix = "FIX-7"
        elif lk in baseline_links:
            fix = "FIX-3"  # the baseline name-linked it; only the column marking is new
        else:
            fix = "FIX-2"
        if fix:
            for f, did in changed.items():
                tally[f"{fix} {f}"] += did
            if lk not in baseline_links:
                tally[f"{fix} table.detected_fks"] += 1
                tally[f"{fix} dataset.relationships"] += 1
        tally["FIX-7 fk_evidence"] += 1
    tally["FIX-8 table.detected_fks"] += len(bogus)
    tally["FIX-8 dataset.relationships"] += len(bogus)
    return evidence

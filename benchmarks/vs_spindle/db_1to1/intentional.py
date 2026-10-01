"""The documented, intentional differences between Shape's database profile and the baseline's.

P6-08 matched the baseline field for field. Three behaviours of the baseline are defects, and
Shape fixes them (P6-08b, owner decision 2026-10-01). Everything else must still equal the
baseline, so the parity check does not ignore fields: it turns the baseline's profile into the
profile the *corrected* behaviour produces, using only the baseline's own numbers plus the
inputs named below, and compares Shape's output with that, field by field, under the unchanged
T-22 rules. Each adjustment is counted and printed.

The allow-list is exactly these fields, and nothing else is adjusted:

    FIX-1  sample-based ratios (denominator = rows actually sampled, not the catalog count)
           column: null_rate, cardinality_ratio, is_unique, is_enum, enum_values
           (is_enum and enum_values follow from cardinality_ratio)
    FIX-2  a foreign key the sampled data shows is reported when none is declared
           dataset: relationships;  table: detected_fks;  column: is_foreign_key, fk_ref_table
           (only for the links named in DATA_LINKS)
    FIX-3  a name-inferred foreign key marks its column
           column: is_foreign_key, fk_ref_table (only for columns in the baseline's
           table.detected_fks)

Inputs: the number of rows each table sampled is *derived* here (``min(requested, baseline
row_count)``, 0 for an unsampled or unreadable table), and Shape's ``sampled_rows`` must equal
it; DATA_LINKS names, per case, the link the sampled data shows (a fixture fact, not read from
Shape's output).
"""

from __future__ import annotations

import copy
from collections import Counter

ENUM_MAX_CARDINALITY = 50
ENUM_MAX_RATIO = 0.05
UNIQUE_RATIO = 0.99
DEFAULT_SAMPLE_ROWS = 1000

# case -> [(child table, child column, parent table)]: relationships the sampled rows show
# although the schema declares none (the baseline discards them).
DATA_LINKS: dict[str, list[tuple[str, str, str]]] = {
    "id_named": [("orders", "customer_id", "customer")],
}

FIX1_FIELDS = ("null_rate", "cardinality_ratio", "is_unique", "is_enum", "enum_values")
FIX23_FIELDS = ("is_foreign_key", "fk_ref_table")


def expected_sampled_rows(sp_table: dict, requested: int, unreadable: bool) -> int:
    if unreadable:
        return 0
    return min(requested, sp_table["row_count"])


def corrected_baseline(
    sp: dict,
    case: str,
    requested: int,
    unreadable: tuple[str, ...],
    declared_fks: bool,
    tally: Counter,
) -> tuple[dict, dict[str, int]]:
    """``(baseline profile with the documented corrections applied, expected sampled_rows)``.
    ``tally`` counts the fields that actually changed, by fix."""
    out = copy.deepcopy(sp)
    sampled: dict[str, int] = {}
    for tname, table in out["tables"].items():
        n = expected_sampled_rows(sp["tables"][tname], requested, tname in unreadable)
        sampled[tname] = n
        denominator = max(n, 1)
        for col in table["columns"].values():
            before = {f: col[f] for f in FIX1_FIELDS}
            card = col["cardinality"]
            ratio = card / denominator
            col["null_rate"] = col["null_count"] / denominator
            col["cardinality_ratio"] = ratio
            col["is_unique"] = ratio > UNIQUE_RATIO if card > 0 else False
            col["is_enum"] = card > 0 and (card <= ENUM_MAX_CARDINALITY or ratio < ENUM_MAX_RATIO)
            if not col["is_enum"]:
                col["enum_values"] = None  # the baseline's frequencies stay when still an enum
            for f in FIX1_FIELDS:
                tally[f"FIX-1 {f}"] += before[f] != col[f]
    if declared_fks:
        return out, sampled
    # FIX-3: columns the baseline linked by name are now marked.
    for table in out["tables"].values():
        for cname, parent in table["detected_fks"].items():
            col = table["columns"][cname]
            before = {f: col[f] for f in FIX23_FIELDS}
            col["is_foreign_key"], col["fk_ref_table"] = True, parent
            for f in FIX23_FIELDS:
                tally[f"FIX-3 {f}"] += before[f] != col[f]
    # FIX-2: links the data shows (named per case) are reported and marked.
    links = []
    for child, ccol, parent in DATA_LINKS.get(case, []):
        assert sp["tables"][child]["detected_fks"].get(ccol) is None, "baseline already linked it"
        t = out["tables"][child]
        t["detected_fks"][ccol] = parent
        col = t["columns"][ccol]
        col["is_foreign_key"], col["fk_ref_table"] = True, parent
        tally["FIX-2 table.detected_fks"] += 1
        tally["FIX-2 is_foreign_key"] += 1
        tally["FIX-2 fk_ref_table"] += 1
        links.append(
            {
                "name": f"fk_{child}_{ccol}",
                "parent": parent,
                "child": child,
                "parent_columns": list(out["tables"][parent]["primary_key"]),
                "child_columns": [ccol],
                "type": "one_to_many",
            }
        )
    if links:
        out["relationships"] = links + out["relationships"]
        tally["FIX-2 dataset.relationships"] += len(links)
    return out, sampled

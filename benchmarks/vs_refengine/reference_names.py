"""Explicit reference NAME aliases from the Owner's approved row lineage.

The source names are represented by UTF-8 digests, not redistributed text. No
generated value, frequency, numeric similarity or observed order fits this map.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
from recorded_store import RecordingError, manifest

OWNER_COMMIT = "904d5d2ed46bdd2e79c0ffb243ff82821134c4d2"
BASELINE_COMMIT = "d09731e59b630fac11b7127c812c47ccca8ab64f"
HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "reference_name_aliases.json"
DATA = HERE.parents[1] / "plugins/shape-domains/src/shape_domains/data"
GROUPS = {
    "healthcare": {
        "code": ("cpt_codes", "code", None),
        "description": ("cpt_codes", "description", None),
    },
    "capital_markets": {
        "sector_name": ("gics_sectors", "sector_name", "sector_id"),
        "sector_code": ("gics_sectors", "sector_code", "sector_id"),
        "industry_name": ("gics_sectors", "industry_name", "industry_id"),
    },
}
SCOPES = {
    "healthcare": {
        ("procedure", "cpt_code"): "code",
        ("procedure", "cpt_description"): "description",
        ("claim_line", "cpt_code"): "code",
    },
    "capital_markets": {
        ("sector", "sector_name"): "sector_name",
        ("sector", "sector_code"): "sector_code",
        ("company", "sector_name"): "sector_name",
        ("company", "industry_name"): "industry_name",
    },
}
DATASETS = {
    "healthcare": {"cpt_codes": {"code": "code", "description": "description"}},
    "capital_markets": {
        "gics_sectors": {"sector_name": "sector_name", "sector_code": "sector_code"},
        "sp500_constituents": {"sector": "sector_name", "industry": "industry_name"},
    },
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def name_digest(name: str) -> str:
    if not isinstance(name, str):
        raise RecordingError("reference NAME alias received a non-string value")
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def lineage(rows: list[dict[str, Any]], field: str, key: str | None) -> dict[int, str]:
    if key == "industry_id":
        rows = [item for row in rows for item in row["industries"]]
    pairs = [(row[key] if key else i, row[field]) for i, row in enumerate(rows, 1)]
    if len({k for k, _ in pairs}) != len(pairs):
        raise RecordingError("duplicate reference lineage")
    if any(type(k) is not int or not isinstance(v, str) for k, v in pairs):
        raise RecordingError("invalid reference NAME lineage")
    return dict(pairs)


class ReferenceNames:
    def __init__(self, domain: str, source: dict, target: dict, contract: dict) -> None:
        self.domain = domain
        if (
            domain not in GROUPS
            or contract.get("owner_commit") != OWNER_COMMIT
            or contract.get("baseline_commit") != BASELINE_COMMIT
            or domain not in contract.get("domains", {})
        ):
            raise RecordingError("reference NAME alias provenance/domain mismatch")
        entry = contract["domains"][domain]
        expected = set(DATASETS[domain])
        if set(entry["source"]) != expected or set(entry["target"]) != expected:
            raise RecordingError("reference NAME alias dataset mismatch")
        for dataset in expected:
            if (
                digest(source[dataset]) != entry["source"][dataset]
                or digest(target[dataset]) != entry["target"][dataset]
            ):
                raise RecordingError("reference NAME alias row digest mismatch")
        if set(entry["aliases"]) != set(GROUPS[domain]):
            raise RecordingError("reference NAME alias field mismatch")
        self.maps: dict[str, dict[str, str]] = {}
        self.targets: dict[str, set[str]] = {}
        for group, (dataset, field, key) in GROUPS[domain].items():
            old, new = lineage(source[dataset], field, key), lineage(target[dataset], field, key)
            aliases = entry["aliases"][group]
            if set(old) != set(new) or len(aliases) != len(old):
                raise RecordingError("reference NAME alias lineage coverage mismatch")
            mapping = {}
            seen = set()
            for alias in aliases:
                row = alias["lineage"]
                if (
                    row not in old
                    or row in seen
                    or alias["source"] != name_digest(old[row])
                    or alias["target"] != new[row]
                ):
                    raise RecordingError("reference NAME alias association differs from lineage")
                seen.add(row)
                mapping[alias["source"]] = alias["target"]
            if len(mapping) != len(aliases) or len(set(mapping.values())) != len(aliases):
                raise RecordingError("reference NAME aliases must be bijective")
            self.maps[group] = mapping
            self.targets[group] = set(mapping.values())
        # Check every reference row, including weights, charges and unrelated fields.
        # Canonicalization can explain ONLY names, never numeric/data differences.
        for dataset in expected:
            if self.normalize_dataset(dataset, source[dataset]) != target[dataset]:
                raise RecordingError("reference NAME aliases do not preserve reference rows")

    def name(self, group: str, value: Any) -> Any:
        if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
            return value
        hashed = name_digest(value)
        if value in self.targets[group]:
            return value
        if hashed not in self.maps[group]:
            raise RecordingError(f"unknown reference NAME in {self.domain}.{group}")
        return self.maps[group][hashed]

    def normalize_dataset(self, dataset: str, rows: list[dict]) -> list[dict]:
        result = copy.deepcopy(rows)
        for row in result:
            for field, group in DATASETS[self.domain].get(dataset, {}).items():
                row[field] = self.name(group, row[field])
            if self.domain == "capital_markets" and dataset == "gics_sectors":
                for industry in row["industries"]:
                    industry["industry_name"] = self.name(
                        "industry_name", industry["industry_name"]
                    )
        return result

    def normalize_frames(self, frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        result = {table: frame.copy(deep=True) for table, frame in frames.items()}
        for (table, column), group in SCOPES[self.domain].items():
            if table in result and column in result[table]:
                result[table][column] = result[table][column].map(
                    lambda v, group=group: self.name(group, v)
                )
        return result

    def wrap_pools(self, pools: Any) -> Any:
        aliases = self

        class CanonicalPools:
            def __getattr__(self, name: str) -> Any:
                return getattr(pools, name)

            def dataset(self, name: str, table: str | None = None) -> Any:
                rows = pools.dataset(name, table)
                return aliases.normalize_dataset(name, rows)

            def pool_for(self, gen: dict, table: str | None = None) -> set[str] | None:
                values = pools.pool_for(gen, table)
                group = DATASETS[aliases.domain].get(gen.get("dataset"), {}).get(gen.get("field"))
                return (
                    {aliases.name(group, v) for v in values}
                    if group and values is not None
                    else values
                )

        return CanonicalPools()


def for_domain(domain: str, pools: Any) -> ReferenceNames | None:
    if domain not in GROUPS:
        return None
    if manifest()["shape_commit"] != BASELINE_COMMIT:
        raise RecordingError("reference NAME alias baseline provenance mismatch")
    source = {name: pools.dataset(name) for name in DATASETS[domain]}
    target = {
        name: pa.ipc.open_file(DATA / domain / "reference" / (name + ".arrow"))
        .read_all()
        .to_pylist()
        for name in DATASETS[domain]
    }
    return ReferenceNames(domain, source, target, json.loads(REGISTRY.read_text()))

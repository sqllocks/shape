"""Offline GeoNames and U.S. Census Gazetteer reference ingestion."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from shape.location.core import Location


@dataclass(frozen=True, slots=True)
class ReferenceProvenance:
    source: str
    version: str
    license_id: str
    sha256: str


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_geonames_postal(path, country=None):
    p = Path(path)
    out = []
    with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 12:
                continue
            (
                cc,
                postal,
                place,
                admin1,
                admin1_code,
                admin2,
                admin2_code,
                admin3,
                admin3_code,
                lat,
                lon,
                accuracy,
            ) = row[:12]
            if country and cc.casefold() != country.casefold():
                continue
            try:
                la = float(lat)
                lo = float(lon)
            except ValueError:
                la = lo = None
            out.append(
                Location(
                    country=cc,
                    state=admin1_code or admin1,
                    county=admin2 or None,
                    city=place or None,
                    postal_code=postal,
                    canonical_id=f"geonames:postal:{cc}:{postal}:{place}:{admin1_code}",
                    latitude=la,
                    longitude=lo,
                )
            )
    return out, ReferenceProvenance(
        "GeoNames Postal Code Dataset", "downloaded", "CC-BY-4.0", sha256_file(p)
    )


def load_census_gazetteer(path, kind, version):
    p = Path(path)
    out = []
    sample = p.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    delim = "|" if sample.count("|") > sample.count("\t") else "\t"
    with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for raw in csv.DictReader(f, delimiter=delim):
            row = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
            geoid = row.get("GEOID") or row.get("GEOID2") or ""
            name = row.get("NAME") or row.get("NAME2") or ""
            try:
                la = float(row.get("INTPTLAT")) if row.get("INTPTLAT") else None
                lo = float(row.get("INTPTLONG")) if row.get("INTPTLONG") else None
            except ValueError:
                la = lo = None
            kw = {
                "country": "US",
                "canonical_id": f"census:{version}:{kind}:{geoid}",
                "latitude": la,
                "longitude": lo,
            }
            if kind == "zcta":
                kw["postal_code"] = geoid
            elif kind == "county":
                kw["county"] = name
            elif kind == "place":
                kw["city"] = name
            elif kind == "state":
                kw["state"] = name
            out.append(Location(**kw))
    return out, ReferenceProvenance(
        "US Census Gazetteer", version, "PUBLIC-DOMAIN-USG", sha256_file(p)
    )

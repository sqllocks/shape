"""Build the locale data of W4-03 (``src/shape/builtins/strategies/locales/``) from upstream files.

    python scripts/build_locale_data.py SRC_DIR [--read-on YYYY-MM-DD]

``SRC_DIR`` holds the unpacked GeoNames postal files ``GB.txt CA.txt DE.txt FR.txt IN.txt AU.txt``
with their ``<CC>.zip`` downloads (https://download.geonames.org/export/zip/), and INSEE's
``nat2021.csv`` with ``ins.zip`` (the "Fichier des prénoms"). The output is deterministic: the same
inputs give the same bytes. Places: one row per postal code (the alphabetically first place),
without rows whose place name is an organisation (GeoNames' German file lists company postcodes).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src/shape/builtins/strategies/locales"
GEONAMES = "https://download.geonames.org/export/zip/{cc}.zip"
GEONAMES_LICENCE = "https://download.geonames.org/export/zip/readme.txt"
INSEE_PAGE = "https://www.insee.fr/fr/statistiques/2540004"
INSEE_ZIP = "https://www.insee.fr/fr/statistiques/fichier/2540004/nat2021_csv.zip"
ORGANISATION = re.compile(
    r"\b(GmbH|mbH|AG|KG|OHG|SE|e\.\s?V\.|eG|Bank|Versicherung\w*|Postfach|Deutsche Post|"
    r"Sparkasse|Finanzamt|Kunden\w*|Service\w*|Zentrale|Verwaltung\w*|Gesellschaft|Stiftung|"
    r"Universität|Klinik\w*|Hochschule|Landkreis|Amt|Kommission|Direktion)\b",
    re.IGNORECASE,
)
POSTAL_CODE = {  # the stem each country's file must give (no CEDEX or business suffixes)
    "GB": re.compile(r"[A-Z]{1,2}\d[A-Z\d]?"),
    "CA": re.compile(r"[A-Z]\d[A-Z]"),
    "DE": re.compile(r"\d{5}"),
    "FR": re.compile(r"\d{5}"),
    "IN": re.compile(r"\d{6}"),
    "AU": re.compile(r"\d{4}"),
}
FIRST_NAME_YEARS = (1950, 2100)
FIRST_NAME_ROWS = 1000


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def places(src: Path, cc: str) -> list[tuple[str, str, str, str, str]]:
    """One row per postal code: the place name found under most postcodes (then the first
    alphabetically). For ``DE`` only rows with a GeoNames accuracy value (places; the postcodes of
    single companies, listed under the company's name, have none) and no organisation word
    qualify."""
    with (src / f"{cc}.txt").open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.reader(handle, delimiter="\t")]
    frequency = Counter(r[2].strip() for r in rows)
    best: dict[str, tuple[int, str, str, str, str]] = {}
    for row in rows:
        code, city, region, lat, lng = (
            row[1].strip(),
            row[2].strip(),
            row[3].strip(),
            row[9],
            row[10],
        )
        if not (code and city and region and lat and lng):
            continue
        if not POSTAL_CODE[cc].fullmatch(code):
            continue
        if cc == "DE" and (not row[11].strip() or ORGANISATION.search(city)):
            continue
        current = best.get(code)
        if current is None or (-frequency[city], city) < (-current[0], current[1]):
            best[code] = (frequency[city], city, region, lat, lng)
    return [(b[1], b[2], code, b[3], b[4]) for code, b in sorted(best.items())]


def title(name: str) -> str:
    return "-".join(part[:1].upper() + part[1:].lower() for part in name.split("-"))


def first_names(src: Path) -> list[str]:
    counts: Counter[str] = Counter()
    with (src / "nat2021.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            name = row["preusuel"]
            if name.startswith("_") or len(name) < 2 or not row["annais"].isdigit():
                continue
            if FIRST_NAME_YEARS[0] <= int(row["annais"]) < FIRST_NAME_YEARS[1]:
                counts[title(name)] += int(row["nombre"])
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:FIRST_NAME_ROWS]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("--read-on", default="2026-10-03")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, object]] = {}
    for cc in ("GB", "CA", "DE", "FR", "IN", "AU"):
        rows = places(args.src, cc)
        name = f"{cc.lower()}_places.tsv"
        text = "".join("\t".join(r) + "\n" for r in rows)
        (OUT / name).write_text(text, encoding="utf-8")
        files[name] = {
            "source": GEONAMES.format(cc=cc),
            "upstream_sha256": sha256(args.src / f"{cc}.zip"),
            "licence": "Creative Commons Attribution 4.0 (CC-BY-4.0)",
            "licence_text_url": GEONAMES_LICENCE,
            "read_on": args.read_on,
            "rows": len(rows),
        }
    names = first_names(args.src)
    (OUT / "fr_first_names.txt").write_text("".join(f"{n}\n" for n in names), encoding="utf-8")
    files["fr_first_names.txt"] = {
        "source": INSEE_ZIP,
        "upstream_sha256": sha256(args.src / "ins.zip"),
        "licence": "Licence Ouverte / Open Licence version 2.0 (Etalab)",
        "licence_text_url": INSEE_PAGE,
        "read_on": args.read_on,
        "rows": len(names),
    }
    for name, entry in files.items():
        entry["sha256"] = sha256(OUT / name)
    manifest = {"format": "shape-locale-data", "version": 1, "files": dict(sorted(files.items()))}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["rows"] for k, v in files.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

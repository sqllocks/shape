"""Rebuild PUB-29 name lists from local official downloads (never downloads at runtime).

Usage: uv run --locked python scripts/build_locale_names.py SRC_DIR
Download URLs and local filenames are recorded in the locale manifest's ``inputs``.
Requires the existing development dependency openpyxl for NRS workbooks.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/shape/builtins/strategies/locales"
MAX_ROWS = 5000  # Same cap as each existing US name pool.
READ_ON = "2026-11-12"
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ONTARIO = "https://data.ontario.ca/dataset/"
AU_PAGE = (
    "https://www.data.qld.gov.au/dataset/top-100-baby-names/resource/53c648be"
    "-8623-4f1b-83c5-9a591a8d6acd"
)
AU_API = (
    "https://www.data.qld.gov.au/api/3/action/datastore_search?resource_id=53"
    "c648be-8623-4f1b-83c5-9a591a8d6acd&limit=1000"
)
RECIPES: dict[str, dict[str, Any]] = {
    "gb_first_names.txt": {
        "publisher": "National Records of Scotland",
        "source_page": "https://www.nrscotland.gov.uk/publications/babies-first-names-2023/",
        "licence": "Open Government Licence v3.0",
        "licence_text_url": OGL,
        "inputs": {
            "gb-first.xlsx": "https://www.nrscotland.gov.uk/media/oaaf0cbz/full-list-2023.xlsx"
        },
        "selection": (
            "Boys and Girls worksheets, rows after the three header rows: Name and "
            "Number; omit NB death annotations in the rank column, not the name; sum "
            "counts across sexes. Scotland births, 2023; upstream has already "
            "removed accents."
        ),
    },
    "gb_last_names.txt": {
        "publisher": "National Records of Scotland",
        "source_page": "https://www.nrscotland.gov.uk/publications/most-common-surnames-2023/",
        "licence": "Open Government Licence v3.0",
        "licence_text_url": OGL,
        "inputs": {
            "gb-last.xlsx": "https://www.nrscotland.gov.uk/media/44zfo3hz/most-common-surnames-bmd-register-2023.xlsx"
        },
        "selection": (
            "All Surnames for 2023 worksheet, after header: Surname and Number. "
            "Scotland birth, marriage and death registrations, not a population "
            "census. Preserve upstream uppercase spelling."
        ),
    },
    "ca_first_names.txt": {
        "publisher": "Government of Ontario",
        "source_page": "https://data.ontario.ca/dataset/ontario-top-baby-names-female",
        "licence": "Open Government Licence – Ontario",
        "licence_text_url": "https://www.ontario.ca/page/open-government-licence-ontario",
        "inputs": {
            "ca-female.csv": ONTARIO
            + (
                "4d339626-98f9-49fe-aede-d64f03fa914f/resource/acc72e92-3100-4a04-8f5f-4f"
                "ad9cd77cc5/download/baby_names_-_female_.csv"
            ),
            "ca-male.csv": ONTARIO
            + (
                "eb4c585c-6ada-4de7-8ff1-e876fb1a6b0b/resource/5d8b8ece-fa01-43c5-955b-4b"
                "642b28c559/download/baby_names_-_male.csv"
            ),
        },
        "selection": (
            "Both Ontario CSVs, UTF-8 BOM, Name/Nom and Frequency/Fréquence; sum "
            "numeric frequencies across all supplied years and sexes; ignore "
            "suppressed nonnumeric frequencies. Preserve upstream spelling and case. "
            "Provincial birth registrations, not all Canada."
        ),
    },
    "au_first_names.txt": {
        "publisher": "Department of Justice, Queensland Government",
        "source_page": AU_PAGE,
        "licence": "Creative Commons Attribution 3.0 Australia (CC BY 3.0 AU)",
        "licence_text_url": "https://creativecommons.org/licenses/by/3.0/au/",
        "inputs": {"au-api.json": AU_API},
        "selection": (
            "Official DataStore API records for Top 100 Baby Names 2024: Girl "
            "Names/Count of Girl Names and Boy Names/Count of Boy Names; sum counts "
            "across sexes, omit null name/count cells. Require all records (total) "
            "in response. Queensland births only, not all Australia."
        ),
    },
    "de_first_names.txt": {
        "publisher": "Stadt Bonn",
        "source_page": "https://opendata.bonn.de/dataset/vornamen-von-neugeborenen-im-jahr-2024",
        "licence": "Creative Commons CC0 1.0 Universal (CC0-1.0)",
        "licence_text_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "inputs": {
            "de-first.csv": "https://opendata.bonn.de/sites/default/files/Vornamenstatistik_2024-01-01_2024-12-31.csv"
        },
        "selection": (
            "UTF-8 semicolon CSV: vorname and anzahl; sum counts across geschlecht "
            "and position. Bonn newborns in 2024 only, not all Germany. Preserve "
            "case and accents."
        ),
    },
    "fr_last_names.txt": {
        "publisher": "Insee",
        "source_page": "https://www.insee.fr/fr/statistiques/3536630",
        "licence": "Licence Ouverte / Open Licence version 2.0 (Etalab)",
        "licence_text_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        "licence_statement_url": "https://www.insee.fr/fr/information/2008466?page=copyright.htm",
        "inputs": {
            "fr-last.zip": "https://www.insee.fr/fr/statistiques/fichier/3536630/noms2008nat_txt.zip"
        },
        "selection": (
            "ZIP member noms2008nat_txt.txt, UTF-8 tab-separated: NOM; sum all "
            "eleven decade columns (1891–2000); exclude AUTRES NOMS aggregate. "
            "Preserve upstream uppercase spelling and accents. Source: Insee, "
            "Fichier des noms, updated 2018-05-22."
        ),
    },
}
COMMON = (
    " Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing "
    "anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. "
    "Merge identical normalized names by summing counts. Select at most 5000 names by descending "
    "total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical "
    "order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform "
    "sampling only: counts rank the capped selection but are not sampling weights."
)


def clean(name: str) -> str:
    name = unicodedata.normalize("NFC", name.strip())
    if not name or not any(c.isalpha() for c in name):
        return ""
    return name if all(unicodedata.category(c)[0] in "LM" or c in " '-’" for c in name) else ""


def counts(src: Path, filename: str) -> Counter[str]:
    pairs: list[tuple[Any, Any]] = []
    if filename.startswith("gb_"):
        first = filename == "gb_first_names.txt"
        workbook = load_workbook(
            src / ("gb-first.xlsx" if first else "gb-last.xlsx"), read_only=True, data_only=True
        )
        for sheet in ["Boys", "Girls"] if first else ["All Surnames for 2023"]:
            for row in workbook[sheet].iter_rows(min_row=4 if first else 2, values_only=True):
                pairs.append((row[1], row[2]) if first else (row[2], row[3]))
        workbook.close()
    elif filename.startswith("ca_"):
        for source in ("ca-female.csv", "ca-male.csv"):
            with (src / source).open(encoding="utf-8-sig", newline="") as handle:
                pairs.extend(
                    (r["Name/Nom"], r["Frequency/Fréquence"]) for r in csv.DictReader(handle)
                )
    elif filename.startswith("au_"):
        result = json.loads((src / "au-api.json").read_text("utf-8"))["result"]
        if len(result["records"]) != result["total"]:
            raise ValueError("Incomplete Australian API response")
        for row in result["records"]:
            for sex in ("Girl", "Boy"):
                pairs.append((row[f"{sex} Names"], row[f"Count of {sex} Names"]))
    elif filename.startswith("de_"):
        with (src / "de-first.csv").open(encoding="utf-8-sig", newline="") as handle:
            pairs.extend((r["vorname"], r["anzahl"]) for r in csv.DictReader(handle, delimiter=";"))
    else:
        with ZipFile(src / "fr-last.zip") as archive:
            text = archive.read("noms2008nat_txt.txt").decode("utf-8")
        for row in csv.reader(io.StringIO(text), delimiter="\t"):
            if row[0] not in ("NOM", "AUTRES NOMS"):
                pairs.append((row[0], sum(int(v) for v in row[1:])))
    result_counts: Counter[str] = Counter()
    for name, count in pairs:
        if not isinstance(name, str) or count is None or not str(count).isdigit():
            continue
        normalized = clean(name)
        if normalized and int(count) > 0:
            result_counts[normalized] += int(count)
    return result_counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    args = parser.parse_args()
    manifest_path = OUT / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    notices = [
        "## Locale name lists (PUB-29)",
        "",
        (
            "Retrieved and licence statements checked 2026-11-12. Files contain "
            "names only; all lists use the existing uniform sampling format. "
            "Regional sources are examples of names used in each country, not "
            "nationally representative distributions."
        ),
        "",
    ]
    for filename, recipe in RECIPES.items():
        ranked = sorted(counts(args.src, filename).items(), key=lambda item: (-item[1], item[0]))
        names = sorted(name for name, _ in ranked[:MAX_ROWS])
        if not names:
            raise ValueError(f"Empty list: {filename}")
        data = ("\n".join(names) + "\n").encode("utf-8")
        (OUT / filename).write_bytes(data)
        inputs = {
            name: {
                "source": url,
                "sha256": hashlib.sha256((args.src / name).read_bytes()).hexdigest(),
            }
            for name, url in recipe["inputs"].items()
        }
        entry = {k: v for k, v in recipe.items() if k not in ("inputs", "selection")}
        entry.update(
            source=next(iter(recipe["inputs"].values())),
            inputs=inputs,
            read_on=READ_ON,
            transformation=recipe["selection"] + COMMON,
            rows=len(names),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        manifest["files"][filename] = entry
        notices.extend(
            [
                f"### `{filename}`",
                "",
                f"Publisher: {entry['publisher']}. Source page: {entry['source_page']}",
                f"Licence: {entry['licence']}: {entry['licence_text_url']}",
                f"Licence statement: {entry.get('licence_statement_url', entry['source_page'])}",
                f"Retrieval date: {READ_ON}. Rows: {len(names)}. "
                f"Shipped SHA-256: `{entry['sha256']}`.",
                "",
                *[
                    f"Source: {v['source']} (upstream SHA-256 `{v['sha256']}`; local input `{k}`)."
                    for k, v in inputs.items()
                ],
                "",
                f"Transformation: {entry['transformation']}",
                "",
            ]
        )
    notices.extend(
        [
            (
                "Attribution: Contains information from National Records of Scotland, © "
                "Crown copyright, licensed under the Open Government Licence v3.0. "
                "Contains information licensed under the Open Government Licence – "
                "Ontario. © The State of Queensland (Department of Justice), Top 100 "
                "Baby Names 2024, CC BY 3.0 AU; adapted as described above. Stadt Bonn, "
                "Vornamen von Neugeborenen im Jahr 2024, CC0. Source : Insee, Fichier "
                "des noms, updated 2018-05-22; adapted as described above. No publisher "
                "endorses this package."
            ),
            "",
        ]
    )
    manifest["files"] = dict(sorted(manifest["files"].items()))
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    notice_path = ROOT / "THIRD_PARTY_NOTICES.md"
    notice = notice_path.read_text("utf-8").split("\n## Locale name lists (PUB-29)\n")[0]
    notice_text = notice.rstrip() + "\n\n" + "\n".join(notices)
    notice_path.write_text(notice_text, encoding="utf-8")
    # The domains distribution carries the repository's identical notices text.
    (ROOT / "plugins/shape-domains/THIRD_PARTY_NOTICES.md").write_text(
        notice_text, encoding="utf-8"
    )


if __name__ == "__main__":
    main()

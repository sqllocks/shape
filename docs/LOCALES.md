# Basic locale packs

Status: experimental.


`{"strategy": "locale", "locale": "FR", "provider": "postcode"}` generates values that look right
for a country: places and postal codes, phone numbers, first names. A locale is a country:
`US`, `CA`, `GB`, `DE`, `FR`, `IN` or `AU`, written `FR`, `fr`, `fr_FR` or `fr-FR`. An unknown
locale is a `StrategyError`.

Providers: `city`, `region`, `postcode`, `phone_number`, `first_name`, `last_name` and `name`.
Row addressed like every strategy: row `r` depends on the seed, the table, the column (or the
group) and `r`, never on the chunk.

## What each locale offers

| Locale | Places and postcodes | Phone numbers (reserved for fiction) | Names |
|---|---|---|---|
| `US` | 40,977 ZIP codes (`sqllocks-shape-domains`) | `+1 (AAA) 555-0100` to `555-0199` | first and last: Shape's own pools |
| `CA` | 1,651 first-three-character areas, completed with a random local part | same NANP lines as `US` | first: Ontario; last: not shipped |
| `GB` | 2,980 outward codes, completed with a random inward code | not shipped | first and last: Scotland |
| `DE` | 8,172 places | not shipped | first: Bonn; last: not shipped |
| `FR` | 6,064 places | the six audiovisual roots of the national plan | first and last: INSEE |
| `IN` | 19,238 PIN codes | not shipped | not shipped |
| `AU` | 3,171 postcodes | not shipped | first: Queensland; last: not shipped |

* **Places.** `city`, `region` (a state, province, nation or region) and `postcode` columns of a
  table with the same `group` (default `address`) draw one place per row, so they agree. A second
  set of columns with another `group` is an independent address. GeoNames lists one place under
  each postal code of the file; the shipped data keeps one place per code.
* **Phone numbers.** Only in ranges a country reserves for fiction. `US` and `CA` use the
  numbering plan's `555-0100` to `555-0199` lines; `FR` uses the roots `01 99 00`, `02 61 91`,
  `03 53 01`, `04 65 71`, `05 36 49` and `06 39 98` followed by four digits
  (`"format": "international"` gives `+33 1 99 00 12 34`). For a country without a shipped
  range the provider raises a `StrategyError` that says no range reserved for fiction is shipped:
  it never falls back to numbers that may be real.
* **Names.** `first_name` for `US`, `GB`, `CA`, `AU`, `DE` and `FR`; `last_name` and `name`
  for `US`, `GB` and `FR`. Elsewhere the provider raises a `StrategyError` that says no openly licensed
  list is shipped. Names use uniform sampling: the existing one-name-per-line format has no weight
  field. Source counts rank the capped selections; these are not population frequency models.
  Each added list has at most 5,000 rows (the existing US pool size), in NFC with accents retained
  where supplied upstream. Scotland's first-name source has already removed accents.
  Regional sources demonstrate names used in a country; they do not cover its whole population.
* **No national identifier.** Social security, insurance, tax, passport and similar numbers
  (`ssn`, `nino`, `nir`, `aadhaar`, `tfn`, ...) are refused for every country with a
  `StrategyError`; the strategy has no provider for one.

## Sources and licences

Every file, its source URL, licence, read date and shipped SHA-256 is in
`src/shape/builtins/strategies/locales/MANIFEST.json`, and quoted in `THIRD_PARTY_NOTICES.md`:
GeoNames (CC BY 4.0) for places, INSEE (Licence Ouverte 2.0) for French first names, and the
French regulator's numbering plan for the reserved roots. `python scripts/build_locale_data.py`
rebuilds the original files from upstream downloads, byte for byte. For the added name lists,
`uv run --locked python scripts/build_locale_names.py SRC_DIR` rebuilds from the local input
filenames and download URLs recorded in the manifest. Its entries and `THIRD_PARTY_NOTICES.md`
record publisher, licence statement and text URLs, retrieval date, exact transformation, row count,
upstream hashes and shipped hashes. A test checks the files against the manifest.

### Added name sources (checked 2026-11-12)

* `GB`: National Records of Scotland, babies' first names and surnames, 2023, OGL v3.0.
* `CA` first: Government of Ontario, both baby-name CSVs, Open Government Licence – Ontario.
* `AU` first: Queensland Department of Justice, Top 100 Baby Names 2024, CC BY 3.0 AU.
* `DE` first: Stadt Bonn, newborn first names 2024, CC0 1.0.
* `FR` last: Source : Insee, Fichier des noms, updated 2018-05-22, Licence Ouverte 2.0.

Full source and licence links, coverage limitations and transformations are in the manifest
and third-party notices. No names were invented or supplemented from an unlicensed source.

## What is not shipped

The first issue asked for names, addresses and phone formats for seven countries. These are not
shipped, because no source with a licence we could read and quote was reachable when the pack was
built (2026-10-03), with the name-source check updated on 2026-11-12:

* `CA`, `AU`, `DE` surnames and `IN` first and last names: **no licensed source found** in the
  2026-11-12 check described below;
* streets (the sources list places and postcodes only);
* phone ranges for `GB`, `DE`, `IN` and `AU`: the regulators' pages for the United Kingdom and
  Australia could not be fetched, the German page did not lead to the range list, and India has
  no range reserved for fiction that we know of.

Each one is a gap in the data, not in the mechanism: add a `<cc>_first_names.txt` or
`<cc>_last_names.txt` with its manifest entry and notice, and the provider serves it.


### Name sources checked but not used (2026-11-12)

| Missing list | What was checked | Why no licensed source found |
|---|---|---|
| `CA` last | [Ontario name catalogue](https://data.ontario.ca/dataset/?keywords_en=name), [Statistics Canada](https://www.statcan.gc.ca/) | Verified baby-name sources supply given names, not surnames; no surname download with verified redistribution terms identified. |
| `AU` last | [Queensland baby names](https://www.data.qld.gov.au/dataset/top-100-baby-names), [NSW baby names](https://data.nsw.gov.au/data/dataset/popular-baby-names-from-1952) | Given-name datasets only; no surname download with verified redistribution terms identified. |
| `DE` last | [Digital Dictionary of Surnames in Germany](https://www.namenforschung.net/dfd/woerterbuch/liste/) | Source page returned HTTP 403; could not verify redistribution rights. Bonn provides given names only. |
| `IN` first and last | [India open-data catalogue](https://data.gov.in/catalogs), Faker's `en_IN` provider source | No suitable licensed name-list download identified in the official catalogue. Faker's given names cite commercial baby-name pages; its software licence does not establish those upstream data rights. |

This records the scope of the search, not a claim that licensed sources cannot exist. No data
was extracted from the unverified sources. The shipped files use only the official sources above.

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
| `US` | 40,977 ZIP codes (`sqllocks-shape-domains`) | `+1 (AAA) 555-0100` to `555-0199` | Shape's own pools |
| `CA` | 1,651 first-three-character areas, completed with a random local part | same NANP lines as `US` | not shipped |
| `GB` | 2,980 outward codes, completed with a random inward code | not shipped | not shipped |
| `DE` | 8,172 places | not shipped | not shipped |
| `FR` | 6,064 places | the six audiovisual roots of the national plan | 1,000 first names |
| `IN` | 19,238 PIN codes | not shipped | not shipped |
| `AU` | 3,171 postcodes | not shipped | not shipped |

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
* **Names.** `first_name` for `FR` (INSEE) and `US`; `last_name` and `name` for `US`. Anywhere
  else the provider raises a `StrategyError` that says no openly licensed list is shipped.
* **No national identifier.** Social security, insurance, tax, passport and similar numbers
  (`ssn`, `nino`, `nir`, `aadhaar`, `tfn`, ...) are refused for every country with a
  `StrategyError`; the strategy has no provider for one.

## Sources and licences

Every file, its source URL, licence, read date and upstream SHA-256 is in
`src/shape/builtins/strategies/locales/MANIFEST.json`, and quoted in `THIRD_PARTY_NOTICES.md`:
GeoNames (CC BY 4.0) for places, INSEE (Licence Ouverte 2.0) for French first names, and the
French regulator's numbering plan for the reserved roots. `python scripts/build_locale_data.py`
rebuilds the files from the upstream downloads, byte for byte. A test checks the files against
the manifest.

## What is not shipped

The first issue asked for names, addresses and phone formats for seven countries. These are not
shipped, because no source with a licence we could read and quote was reachable when the pack was
built (2026-10-03):

* surnames for every country but `US`, and first names for `CA`, `GB`, `DE`, `IN` and `AU`;
* streets (the sources list places and postcodes only);
* phone ranges for `GB`, `DE`, `IN` and `AU`: the regulators' pages for the United Kingdom and
  Australia could not be fetched, the German page did not lead to the range list, and India has
  no range reserved for fiction that we know of.

Each one is a gap in the data, not in the mechanism: add a `<cc>_first_names.txt` or
`<cc>_last_names.txt` with its manifest entry and notice, and the provider serves it.

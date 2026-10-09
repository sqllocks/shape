# Reference packs and column validators

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


Shape ships reference data that works offline: a ZIP-to-city table, ISO code lists and the
length of an IBAN in each country. They are **reference packs**, a small versioned format that you
can also use for your own code lists. Everything is checked against a checksum before it is read,
and nothing is downloaded at run time.

What reads a pack: `shape profile --reference-pair`, the `reference_pair` contract rule and the
`reference_match_change` drift kind (`docs/JOINT.md`), and every generation strategy that takes a
dataset name. They all look a dataset up with `shape.generation.reference.load_dataset`, which finds
the datasets of packs as well as `<name>.json` files and registered datasets, so nothing changes
for the caller.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

## The pack format

A pack is a directory with `pack.json` and its data files, which are Arrow IPC files:

```json
{
  "format": "shape-reference-pack",
  "version": 1,
  "name": "us-zip-city",
  "pack_version": "1.0.0",
  "source": "GeoNames postal codes, US.zip (https://download.geonames.org/export/zip/US.zip), US.txt sha256 ...",
  "retrieved": "2026-10-03",
  "license": "CC-BY-4.0",
  "attribution": "This work includes data from GeoNames (https://www.geonames.org/), licensed under CC-BY-4.0.",
  "transformation_version": "1",
  "sensitivity": "public",
  "datasets": [
    {"name": "us_zip_city", "file": "us_zip_city.arrow",
     "fields": ["zip", "city", "state", "county"], "rows": 40979, "sha256": "..."}
  ]
}
```

| Key | Meaning |
|---|---|
| `format`, `version` | always `shape-reference-pack` and an integer. A manifest with a newer `version` than Shape reads is refused and says to upgrade |
| `name` | the pack: lowercase letters, digits and hyphens |
| `pack_version` | the pack's own release; raised when its data changes |
| `source`, `retrieved` | where the data came from (with the checksum of the source file where there is one) and when |
| `license`, `attribution` | the licence of the data and the notice it requires; both are repeated in `THIRD_PARTY_NOTICES.md` for every shipped pack |
| `transformation_version` | raised when the way the data is derived from the source changes |
| `sensitivity` | `public` for every pack Shape ships |
| `datasets` | each with its `name` (letters, digits, underscores), `file` (a plain `*.arrow` name in the pack directory), `fields`, `rows` and the SHA-256 of the file |

The JSON Schema is `src/shape/schemas/reference-pack-v1.schema.json`. Every key is required and no
other key is allowed in version 1. A reader checks, in this order: the manifest, then for each
dataset it reads the checksum (`reference pack NAME: FILE does not match its checksum`, exit 2),
then that the file's fields and row count are the manifest's. A dataset file name is never a path.

`shape.refpacks.write_pack(directory, name=..., tables={dataset: pyarrow.Table}, ...)` writes a
pack, with the checksums, and gives the same bytes for the same table.

### Where packs are found

`load_dataset(name)` looks in this order and uses the first match:

1. a dataset registered in this process (`register_dataset`);
2. `<name>.json` in a search directory (`add_search_path`, then `SHAPE_REFERENCE_PATH`);
3. a dataset of a pack in a search directory (the directory itself, if it holds `pack.json`, or
   each of its subdirectories that does);
4. a dataset of a shipped pack: the packs in `shape/refpacks/data/` and, with
   `sqllocks-shape-domains` installed, its `reference_packs/`.

So your own pack with the dataset name `us_zip_city` in a search directory replaces the shipped one.
A directory that looks like a pack but cannot be read is skipped when a dataset is looked up, and
reported by `shape reference list` (a warning on stderr and `problems` in `--json`).

### Commands

* `shape reference list [--json]` lists every pack and dataset found, with its version, rows,
  license and where it came from (`shipped` or `search path`).
* `shape reference show NAME [--json]` prints one pack's manifest and, for each dataset, its fields
  and first rows. Reading the rows checks the checksum. An unknown pack, or a name that is not a
  plain pack name, is exit 2.

## The packs

### `us-zip-city` (in `sqllocks-shape-domains`)

Dataset `us_zip_city`: `zip` (five-character text, leading zeros kept), `city`, `state` (the
two-letter code) and `county` (null where GeoNames has none). One row per ZIP, 40,979 rows. A row
needs a five-digit ZIP, a place name and a state code: the military post offices (APO, FPO, DPO),
which GeoNames lists without a state, are left out.

* Source: the GeoNames US postal file `US.zip` (`US.txt`), https://download.geonames.org/export/zip/US.zip,
  retrieved 2026-10-03.
* License: CC-BY-4.0 (the readme at https://download.geonames.org/export/zip/readme.txt states it).
  Attribution: "This work includes data from GeoNames (https://www.geonames.org/), licensed under
  CC-BY-4.0." It is in the manifest and in `THIRD_PARTY_NOTICES.md`.
* With the plugin installed, this works offline:

  ```python
  shape.profile("orders.csv", reference_pairs=[
      {"columns": {"zip": "zip", "city": "city", "state": "state"}, "reference": "us_zip_city"}])
  ```

  and on the example data of `docs/JOINT.md` (ZIPs moved to another place) it reports the moved
  rows. Values compare as text, trimmed and case folded, and digit strings without leading zeros,
  so a ZIP read as the number `2872` still matches `02872`.

### `iso-3166-1` (shipped in core)

Dataset `iso_3166_1`: `alpha2`, `alpha3`, `numeric` (three-character text, `004` for Afghanistan)
and `name` (English). 249 rows.

* Source: Unicode CLDR 48.2, `core.zip` (https://unicode.org/Public/cldr/48.2/core.zip): the regions
  that have idStatus `regular` in `common/validity/region.xml` and a numeric code in
  `common/supplemental/supplementalData.xml`, without `XK` (Kosovo), which CLDR lists with a
  user-assigned numeric code that ISO 3166-1 does not assign. The names are CLDR's English display
  names (`common/main/en.xml`, the form without a variant: "Türkiye", "Czechia", "Myanmar (Burma)"),
  not ISO's own short names.
* License: Unicode-3.0 (the Unicode License v3). The notice is in `THIRD_PARTY_NOTICES.md`.

### `iso-639-1` (shipped in core)

Dataset `iso_639_1`: `alpha2` (the two-letter code) and `name` (English). 183 rows.

* Source: Unicode CLDR 48.2: the two-letter codes with idStatus `regular` in
  `common/validity/language.xml`, plus `tl` and `tw`, which CLDR deprecates in favour of `fil` and
  `ak` while ISO 639-1 still assigns them; names from `common/main/en.xml`. Codes that CLDR deprecates
  (`iw`, `in`, `ji`, `jw`, `mo`, `sh`, `bh`) are not listed.
* License: Unicode-3.0.

### `iban-lengths` (shipped in core)

Dataset `iban_lengths`: `country` and `length`, the length of an IBAN in each of the 103 countries of
the SWIFT IBAN Registry.

* Source: schwifty 2026.7.3 (https://pypi.org/project/schwifty/2026.7.3/),
  `schwifty/iban_registry/generated.json`, which derives it from the SWIFT IBAN Registry.
* License: MIT (schwifty). Attribution: "Copyright (c) 2021 Martin Domke (schwifty), MIT License;
  the lengths are those of the SWIFT IBAN Registry." Only the length of each country is taken, a fact
  of the registry.

### `iso-4217` and `iso-639`: not shipped

The issue asks for packs of the ISO 4217 currency codes and of ISO 639-1 and 639-2/T. Shape does
**not** ship them, because no source whose licence allows redistribution in an MIT project could be
confirmed:

* ISO 4217: the maintenance agency (SIX Group) offers the list "online and free of charge" and states
  no licence that allows redistributing it. Unicode CLDR carries currency data, but its list is not
  ISO 4217's (it lacks funds and precious-metal codes and some numeric codes), so a pack from it
  would not be the ISO list.
* ISO 639-2: the Library of Congress (the registration authority) publishes the list and states no
  licence on the page or the file. The ISO 639-3 tables of SIL International (which map to 639-2) are
  offered under terms that allow use in software but name the SIL site as the only authorised
  distribution site and do not allow the product to provide a means to redistribute the code set,
  which a public repository does.

`iso-639-1` above covers what the `iso639_1` validator needs. To have the other two on your own
machine, download the files yourself and build the packs locally (the script reads files, never
the network):

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`list-one.xml` is the SIX Group file at
https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
and `ISO-639-2_utf-8.txt` the Library of Congress file at
https://www.loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt. A locally built pack records `local
build ... do not redistribute` as its licence. Dataset `iso_4217` has `alphabetic_code`,
`numeric_code` (three-character text) and `minor_units` (null for `N.A.`); dataset `iso_639` has
`alpha2` (null where there is none), `alpha3_t` (the 639-2/T code) and `name`. The `iso4217`
validator uses `iso_4217`.

### Refreshing a pack

`scripts/build_reference_packs.py PACK --source FILE` rebuilds a shipped pack from its source file;
`--check` rebuilds into a temporary directory and exits 1, naming the files that differ, if the
committed pack is not what the source produces. The sources of the shipped packs are pinned by
SHA-256 in the script (a different file is refused unless `--allow-unpinned-source` is given):

| Pack | `--source` | Pinned file |
|---|---|---|
| `us-zip-city` | GeoNames `US.zip` or `US.txt` | `US.txt`, sha256 `a2e9aa82edb6...` (full value in the script) |
| `iso-3166-1`, `iso-639-1` | CLDR 48.2 `core.zip` | sha256 `d2844f9dbf61...` |
| `iban-lengths` | schwifty's `generated.json` | sha256 `4b25b3f6c334...` |

To refresh to newer data: download the new source, build with `--allow-unpinned-source`, check
the result, then update the pin, `RETRIEVED` and the versions in the script, raise `pack_version`
(and `transformation_version` if the derivation changed), and update `THIRD_PARTY_NOTICES.md` and
this page.

## Column validators

A **validator** says whether a value is a valid code of some kind. It enters Shape in three places.

```python
shape.profile("orders.csv", validators={"iban": "iban", "country": ["iso3166_alpha2"]})
```

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

```json
{"columns": {"iban": {"valid_as": {"kind": "iban", "min_valid_rate": 0.999}}}}
```

The profile stores on the column `validators: {KIND: {"checked": N, "valid": V, "valid_rate": R}}`:
counts and a share, never a value, so a profile that holds it is no riskier to share (the share-safe
profile carries it). The invalid examples are not stored. Missing values (nulls and NaN) are not
counted in `checked`; `valid_rate` is null when nothing was checked.

`valid_as` is a column rule of contract format v1: `kind` is one of the kinds below and
`min_valid_rate` (from 0 to 1) defaults to 1.0. A rule the profile holds no measurement for (it was
profiled without that validator) is a violation that says `not measured`, like the other joint rules.
A column with nothing to check (all values missing) passes. A reader that predates the rule rejects
it as an unknown rule, as before. For a dataset, `validators` maps a table name to such a dict; the
command line checks a single table.

| Kind | A value is valid when | Needs |
|---|---|---|
| `iban` | after plain spaces are removed and letters upper-cased: ASCII letters and digits only, a known country code, that country's length, and the ISO 7064 MOD 97-10 check equals 1 | `iban-lengths` |
| `iso3166_alpha2` | an ISO 3166-1 alpha-2 code in upper case (`US`) | `iso-3166-1` |
| `iso3166_alpha3` | an ISO 3166-1 alpha-3 code in upper case (`USA`) | `iso-3166-1` |
| `iso4217` | an ISO 4217 alphabetic code in upper case (`EUR`) | the `iso-4217` pack, built locally (above) |
| `iso639_1` | an ISO 639-1 code in lower case (`de`) | `iso-639-1` |
| `us_zip` | five digits, optionally `-` and four digits, and not `00000`; a number from 1 to 99999 counts as a ZIP whose leading zeros were lost | none |

Except for the IBAN, a code is matched exactly: no surrounding spaces, no other case. Only text is a
code (a number is not an ISO code); the ZIP also accepts the numbers described above, because a
column of ZIPs read from a CSV is often numbers and `2872` is `02872` that lost its zero. Profile the
column as text to check the zeros too. The code lists are those of the packs: a pack of the same
dataset name earlier in the search path replaces them. `iso4217` without its pack is exit 2 with the
steps to build it.

### The IBAN check and what it does not catch

`shape.validation.iban.is_valid_iban(value)` (and `iban_problem(value)`, which says why not). It
checks the length and the checksum, not the structure of the national account number. Against the
published example IBANs of 30 countries, every substitution of one digit by another digit or one
letter by another letter after the country code is rejected, and so is every swap of two adjacent
different characters, with **one exception** that no MOD 97-10 check can catch: swapping an adjacent
`1` and `B`, because the letter `B` is 11 and `1B` and `B1` both become `111`. A letter replaced by a
digit, or the reverse, can still pass the checksum, so that case is not claimed. The length table is
the `iban-lengths` pack (`shape.validation.iban.IBAN_LENGTHS`).

## Compatibility

`pack.json` declares `format` and an integer `version`. Version 1 is what this page describes. A
change that older readers could not read raises `version`; Shape refuses a pack with a version it
does not know instead of reading part of it. `tests/reference/data/capsule_v1` is a committed
version 1 pack that every later Shape must keep reading.

# Third-Party Notices

Shape is released under the MIT license (see `LICENSE`).

## GeoNames

Postal-code and place reference data is derived from GeoNames (https://www.geonames.org/).

GeoNames data is licensed under the Creative Commons Attribution 4.0 International
license (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/. Attribution:
"This work includes data from GeoNames (https://www.geonames.org/), licensed under
CC-BY-4.0." Any distribution that includes this data must retain this attribution.

Source of the licence statement for the postal-code data:
https://download.geonames.org/export/zip/readme.txt ("This work is licensed under a Creative
Commons Attribution 4.0 License"), checked 2026-10-02. That readme's own link still names the
older 3.0 URL; this repository follows the readme's text, 4.0, everywhere.

### Reference pack `us-zip-city`

`plugins/shape-domains/src/shape_domains/reference_packs/us-zip-city/` (dataset `us_zip_city`:
ZIP, place, state code, county) is built from the GeoNames US postal file (US.zip,
https://download.geonames.org/export/zip/US.zip, retrieved 2026-10-03; the licence statement is the
readme above, checked 2026-10-02). Attribution: "This work includes data from GeoNames
(https://www.geonames.org/), licensed under CC-BY-4.0."

## Unicode CLDR

The reference packs `iso-3166-1` and `iso-639-1` (`src/shape/refpacks/data/`) are built from the
Unicode Common Locale Data Repository, release 48.2 (`core.zip`,
https://unicode.org/Public/cldr/48.2/core.zip, retrieved 2026-10-03): the region and language
validity lists, the territory code mappings and the English display names. Attribution:
"Copyright (c) 2019-2025 Unicode, Inc. Data files of the Unicode Common Locale Data Repository
(CLDR) are used under the Unicode License v3 (https://www.unicode.org/license.txt)." The licence
text, as shipped in `core.zip`:

> UNICODE LICENSE V3
>
> COPYRIGHT AND PERMISSION NOTICE
>
> Copyright © 2019-2025 Unicode, Inc.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of data files and
> any associated documentation (the "Data Files") or software and any associated documentation
> (the "Software") to deal in the Data Files or Software without restriction, including without
> limitation the rights to use, copy, modify, merge, publish, distribute, and/or sell copies of
> the Data Files or Software, and to permit persons to whom the Data Files or Software are
> furnished to do so, provided that either (a) this copyright and permission notice appear with
> all copies of the Data Files or Software, or (b) this copyright and permission notice appear in
> associated Documentation.
>
> THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
> PARTICULAR PURPOSE AND NONINFRINGEMENT OF THIRD PARTY RIGHTS. IN NO EVENT SHALL THE COPYRIGHT
> HOLDER OR HOLDERS INCLUDED IN THIS NOTICE BE LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR
> CONSEQUENTIAL DAMAGES, OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
> WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
> CONNECTION WITH THE USE OR PERFORMANCE OF THE DATA FILES OR SOFTWARE.
>
> Except as contained in this notice, the name of a copyright holder shall not be used in
> advertising or otherwise to promote the sale, use or other dealings in these Data Files or
> Software without prior written authorization of the copyright holder.
>
> SPDX-License-Identifier: Unicode-3.0

## schwifty (IBAN lengths)

The reference pack `iban-lengths` (`src/shape/refpacks/data/iban-lengths/`) holds the length of an
IBAN in each country, read from `schwifty/iban_registry/generated.json` of schwifty 2026.7.3
(https://pypi.org/project/schwifty/2026.7.3/, retrieved 2026-10-03), which derives it from the
SWIFT IBAN Registry. schwifty is under the MIT licence. Attribution: "Copyright (c) 2021 Martin
Domke (schwifty), MIT License; the lengths are those of the SWIFT IBAN Registry." The licence
text:

> The MIT License (MIT)
>
> Copyright (c) 2021 Martin Domke
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software
> and associated documentation files (the "Software"), to deal in the Software without
> restriction, including without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
> Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
> BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## python-dateutil

`src/shape/profile/reference/_dateutil_parser.py` is a port of the date/time string parser of
python-dateutil 2.9.0 (`dateutil/parser/_parser.py`), kept so that Shape classifies and parses
date text the way pandas does (pandas falls back on dateutil), without depending on the
package.

python-dateutil is Copyright (c) 2003-2011 Gustavo Niemeyer, (c) 2012-2014 Tomi Pieviläinen,
(c) 2014-2016 Yaron de Leeuw, (c) 2015- Paul Ganssle and the dateutil contributors, under the
BSD 3-Clause license (contributions after 2017-12-01 are also licensed under Apache 2.0):

Redistribution and use in source and binary forms, with or without modification, are permitted
provided that the following conditions are met: redistributions of source code must retain the
above copyright notice, this list of conditions and the following disclaimer; redistributions in
binary form must reproduce the above copyright notice, this list of conditions and the following
disclaimer in the documentation and/or other materials provided with the distribution; neither
the name of the copyright holder nor the names of its contributors may be used to endorse or
promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY
WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Locale packs (W4-03): places, postal codes, first names, reserved phone ranges

Files in `src/shape/builtins/strategies/locales/`, built by `scripts/build_locale_data.py`; each
file's upstream archive SHA-256, licence and read date are in `MANIFEST.json` next to it. All read
on 2026-10-03.

### GeoNames postal codes: GB, CA, DE, FR, IN, AU

`gb_places.tsv`, `ca_places.tsv`, `de_places.tsv`, `fr_places.tsv`, `in_places.tsv` and
`au_places.tsv` (city, region, postal code, latitude, longitude; one place per postal code) are
derived from these downloads: https://download.geonames.org/export/zip/GB.zip,
https://download.geonames.org/export/zip/CA.zip, https://download.geonames.org/export/zip/DE.zip,
https://download.geonames.org/export/zip/FR.zip, https://download.geonames.org/export/zip/IN.zip
and https://download.geonames.org/export/zip/AU.zip. Licence: Creative Commons Attribution 4.0, from the readme at
https://download.geonames.org/export/zip/readme.txt ("This work is licensed under a Creative
Commons Attribution 4.0 License"), read 2026-10-03. Attribution: "This work includes data from
GeoNames (https://www.geonames.org/), licensed under CC-BY-4.0." The files hold the first part of
a British postcode and the first three characters of a Canadian one only; the German file is
reduced to places (the rows of single companies are left out).

### INSEE, Fichier des prénoms: FR first names

`fr_first_names.txt` is the 1,000 most given first names of persons born in France from 1950 on,
from INSEE's "Fichier des prénoms" (national file, 2021 edition),
https://www.insee.fr/fr/statistiques/fichier/2540004/nat2021_csv.zip, described at
https://www.insee.fr/fr/statistiques/2540004. Licence: the page states that the data are
available "sous la Licence Ouverte / Open Licence version 2.0 (Etalab)",
https://www.etalab.gouv.fr/licence-ouverte-open-licence, read 2026-10-03. Source: INSEE, Fichier
des prénoms, 2021 edition. Names were re-cased and counts dropped.

### ARCEP, French numbering plan: reserved fiction numbers

The six roots `01 99 00`, `02 61 91`, `03 53 01`, `04 65 71`, `05 36 49` and `06 39 98` used by the
French `phone_number` provider are the "numéros pour œuvres audiovisuelles" of Arcep's Decision
n° 2018-0881 of 24 July 2018 as amended, national numbering plan, version of 1 January 2026,
section "Numéros pour œuvres audiovisuelles": "peuvent être utilisés comme numéros de téléphone
dans des fictions qui en auraient besoin". https://www.arcep.fr/uploads/tx_gsavis/18-0881.pdf,
read 2026-10-03. The document states no data licence; only these six facts are used.

### Not shipped

No other source is shipped. The `docs/LOCALES.md` section "What is not shipped" lists the data
that could not be read under a licence this session.

## Domain reference data (SQLLocks)

The reference data of the `capital_markets`, `education`, `financial`, `healthcare`, `hr`, `insurance`, `iot`, `manufacturing`,
`marketing`, `real_estate`, `retail`, `supply_chain` and `telecom` domains in `plugins/shape-domains` (names, catalogs,
exchange and sector lists, index memberships, constituents, code lists, and device, property, shipping and network lists)
is distributed under the MIT licence by SQLLocks. The reference-data attribution is:

MIT License. Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). Permission is hereby granted, free
of charge, to any person obtaining a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following conditions: the
above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

The S&P 500 constituent list names public companies and their public attributes (ticker, sector,
headquarters state, website); it is sample data and is not a current or complete index membership.

## Synthetic healthcare and capital-markets reference replacements (0.9.3)

The healthcare procedure catalog uses `SP-01` through `SP-49`, with descriptions
`Synthetic procedure 01` through `Synthetic procedure 49`. The letters and hyphen
make these five-character identifiers distinct from CPT code formats. They have
no clinical meaning and are not an AMA code set. Charges and selection weights
retain the original numeric distributions.

The capital-markets taxonomy uses `SA` through `SK`, `Synthetic sector A` through
`Synthetic sector K`, and synthetic industry labels. Company classification labels
use that same synthetic naming. These classifications are not GICS and make no
claim of MSCI or S&P endorsement. Schemas, identifiers and category counts remain
compatible. The legacy dataset aliases `cpt_codes` and `gics_sectors` identify these
synthetic tables for existing specifications; their names do not denote licensed content.

These replacements were authored for Shape and are distributed under the SQLLocks
MIT notice above. The CPT-shaped detector and user-supplied code-system adapters
ship no CPT code list; their recognition of input supplied by a user is unchanged.

## Public dataset library

`src/shape/library/datasets/` holds safe profiles (statistics and formats, never rows) of the public
datasets below. No source data is shipped. Each profile was built from the file at the source URL
(its SHA-256 is recorded in `index.json`), and each dataset is under the licence named here.

### Abalone (`dataset:abalone`)

- Source: https://archive.ics.uci.edu/static/public/1/data.csv
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/
- Retrieved: 2026-10-03
- Attribution: Nash, W., Sellers, T., Talbot, S., Cawthorn, A. and Ford, W. (1994). Abalone. UCI Machine Learning Repository. https://doi.org/10.24432/C55C7W. Licensed under CC BY 4.0.

### Adult (census income) (`dataset:adult-income`)

- Source: https://archive.ics.uci.edu/static/public/2/data.csv
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/
- Retrieved: 2026-10-03
- Attribution: Becker, B. and Kohavi, R. (1996). Adult. UCI Machine Learning Repository. https://doi.org/10.24432/C5XW20. Licensed under CC BY 4.0.

### Breast Cancer Wisconsin (Diagnostic) (`dataset:breast-cancer-wisconsin`)

- Source: https://archive.ics.uci.edu/static/public/17/data.csv
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/
- Retrieved: 2026-10-03
- Attribution: Wolberg, W., Mangasarian, O., Street, N. and Street, W. (1993). Breast Cancer Wisconsin (Diagnostic). UCI Machine Learning Repository. https://doi.org/10.24432/C5DW2B. Licensed under CC BY 4.0.

### Iris (`dataset:iris`)

- Source: https://archive.ics.uci.edu/static/public/53/data.csv
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/
- Retrieved: 2026-10-03
- Attribution: Fisher, R. A. (1936). Iris. UCI Machine Learning Repository. https://doi.org/10.24432/C56C76. Licensed under CC BY 4.0.

### Palmer penguins (`dataset:palmer-penguins`)

- Source: https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv
- Licence: Creative Commons CC0 1.0 Universal (public domain dedication), https://creativecommons.org/publicdomain/zero/1.0/
- Retrieved: 2026-10-03
- Attribution: Horst AM, Hill AP, Gorman KB (2020). palmerpenguins: Palmer Archipelago (Antarctica) penguin data. R package version 0.1.1; data collected by Kristen Gorman and the Palmer Station LTER, released under CC0 (https://allisonhorst.github.io/palmerpenguins/).

### Wine (`dataset:wine`)

- Source: https://archive.ics.uci.edu/static/public/109/data.csv
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/
- Retrieved: 2026-10-03
- Attribution: Aeberhard, S. and Forina, M. (1992). Wine. UCI Machine Learning Repository. https://doi.org/10.24432/C5PC7J. Licensed under CC BY 4.0.


## Per-domain reference-data attribution

This table lists the evidence in these notices, not a claim of calibration or upstream research.
Retain the full licence notices above when redistributing reference data. Domain code can reuse
reference providers from other domains; those providers keep their own attribution requirements.

| Domain | Source recorded here | Licence | Attribution / missing evidence |
|---|---|---|---|
| `capital_markets` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-12](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-capital_markets). No GICS codes or taxonomy names are bundled. |
| `education` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-education). |
| `financial` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-financial). |
| `healthcare` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-12](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-healthcare). No AMA CPT code list or descriptors are bundled. |
| `hr` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-hr). |
| `insurance` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-insurance). |
| `iot` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-iot). |
| `manufacturing` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-manufacturing). |
| `marketing` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-marketing). |
| `real_estate` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-real_estate). |
| `retail` | SQLLocks lists; GeoNames US postal locations | MIT; CC-BY-4.0 | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). Includes GeoNames data licensed under CC-BY-4.0. |
| `supply_chain` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-supply_chain). |
| `telecom` | SQLLocks reference lists | MIT | Copyright (c) 2025-2026 SQLLocks (Jonathan Stewart). These reference lists were authored by the Owner, by hand or with AI assistance, and were not copied from a specific external website or dataset. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-telecom). |
| `pulse` | No bundled reference files in the domain data directory | MIT (schema) | SQLLocks; The name pools were authored by the Owner, by hand or with AI assistance, and were not copied from an external source; values are generated by the built-in seeded providers. [Owner decision, 2026-10-11](https://github.com/sqllocks/shape/blob/main/docs/DECISIONS.md#domain-pulse). |

## Documentation diagrams

The documentation site bundles Mermaid 11.17.2 from the npm distribution under MIT.
Its bundle hash and source archive are in `docs/assets/javascripts/mermaid-provenance.json`.
The Mermaid and installed dependency licence notices are preserved in
`docs/assets/javascripts/MERMAID_NOTICES.txt`. `scripts/vendor_docs_diagrams.py` regenerates
these assets from an install of the pinned package. No CDN request is needed to render diagrams.

## Documentation fonts

The documentation site bundles these fonts locally. It does not request fonts from an external service.

| Font | Copyright | Licence | Bundled notice |
|---|---|---|---|
| Host Grotesk | Copyright 2023 The Host Grotesk Project Authors | SIL Open Font License 1.1 | `docs/assets/fonts/host-grotesk-OFL.txt` |
| Fragment Mono | Copyright 2022 The Fragment-Mono Project Authors | SIL Open Font License 1.1 | `docs/assets/fonts/fragment-mono-OFL.txt` |

## Locale name lists (PUB-29)

Retrieved and licence statements checked 2026-11-12. Files contain names only; all lists use the existing uniform sampling format. Regional sources are examples of names used in each country, not nationally representative distributions.

### `gb_first_names.txt`

Publisher: National Records of Scotland. Source page: https://www.nrscotland.gov.uk/publications/babies-first-names-2023/
Licence: Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/
Licence statement: https://www.nrscotland.gov.uk/publications/babies-first-names-2023/
Retrieval date: 2026-11-12. Rows: 5000. Shipped SHA-256: `84fcf512ab54b5e94ba24a0277308394aeeb362e1003a68f993b0a28f38e7850`.

Source: https://www.nrscotland.gov.uk/media/oaaf0cbz/full-list-2023.xlsx (upstream SHA-256 `b3c8aef8aca94206625138f4bcb195cfc035d204c27990ef705521a7e7af637e`; local input `gb-first.xlsx`).

Transformation: Boys and Girls worksheets, rows after the three header rows: Name and Number; omit NB death annotations in the rank column, not the name; sum counts across sexes. Scotland births, 2023; upstream has already removed accents. Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. Merge identical normalized names by summing counts. Select at most 5000 names by descending total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform sampling only: counts rank the capped selection but are not sampling weights.

### `gb_last_names.txt`

Publisher: National Records of Scotland. Source page: https://www.nrscotland.gov.uk/publications/most-common-surnames-2023/
Licence: Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/
Licence statement: https://www.nrscotland.gov.uk/publications/most-common-surnames-2023/
Retrieval date: 2026-11-12. Rows: 2113. Shipped SHA-256: `c8f1ab176c05439c40d723a1467791b49412e066c962f999230f6af230ec24a4`.

Source: https://www.nrscotland.gov.uk/media/44zfo3hz/most-common-surnames-bmd-register-2023.xlsx (upstream SHA-256 `a0f92998090ae25ef5b062ba2320e12eb1af2efc111f4cb4c2e91edc72f37dd9`; local input `gb-last.xlsx`).

Transformation: All Surnames for 2023 worksheet, after header: Surname and Number. Scotland birth, marriage and death registrations, not a population census. Preserve upstream uppercase spelling. Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. Merge identical normalized names by summing counts. Select at most 5000 names by descending total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform sampling only: counts rank the capped selection but are not sampling weights.

### `ca_first_names.txt`

Publisher: Government of Ontario. Source page: https://data.ontario.ca/dataset/ontario-top-baby-names-female
Licence: Open Government Licence – Ontario: https://www.ontario.ca/page/open-government-licence-ontario
Licence statement: https://data.ontario.ca/dataset/ontario-top-baby-names-female
Retrieval date: 2026-11-12. Rows: 5000. Shipped SHA-256: `1f7dccc216d9f31643fa58b2ebc19805a53927199dab300fc4cb0e6061b05a97`.

Source: https://data.ontario.ca/dataset/4d339626-98f9-49fe-aede-d64f03fa914f/resource/acc72e92-3100-4a04-8f5f-4fad9cd77cc5/download/baby_names_-_female_.csv (upstream SHA-256 `93831ff6a0ee7e79b91859d5301c78d4d33afd6aea915779fdeadb9c51237226`; local input `ca-female.csv`).
Source: https://data.ontario.ca/dataset/eb4c585c-6ada-4de7-8ff1-e876fb1a6b0b/resource/5d8b8ece-fa01-43c5-955b-4b642b28c559/download/baby_names_-_male.csv (upstream SHA-256 `61b3bede993a38f6e104b662295017bfc77266b56f46e88372bb5ed8b9282861`; local input `ca-male.csv`).

Transformation: Both Ontario CSVs, UTF-8 BOM, Name/Nom and Frequency/Fréquence; sum numeric frequencies across all supplied years and sexes; exclude the registration placeholders BABY, BOY, FEMALE, INFANT, MALE, NOT and UNNAMED; ignore suppressed nonnumeric frequencies. Preserve upstream spelling and case. Provincial birth registrations, not all Canada. Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. Merge identical normalized names by summing counts. Select at most 5000 names by descending total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform sampling only: counts rank the capped selection but are not sampling weights.

### `au_first_names.txt`

Publisher: Department of Justice, Queensland Government. Source page: https://www.data.qld.gov.au/dataset/top-100-baby-names/resource/53c648be-8623-4f1b-83c5-9a591a8d6acd
Licence: Creative Commons Attribution 3.0 Australia (CC BY 3.0 AU): https://creativecommons.org/licenses/by/3.0/au/
Licence statement: https://www.data.qld.gov.au/dataset/top-100-baby-names/resource/53c648be-8623-4f1b-83c5-9a591a8d6acd
Retrieval date: 2026-11-12. Rows: 203. Shipped SHA-256: `24b87e11e6c45655a62161adac738bdb0e5640e9643f67019fe956457a612227`.

Source: https://www.data.qld.gov.au/api/3/action/datastore_search?resource_id=53c648be-8623-4f1b-83c5-9a591a8d6acd&limit=1000 (upstream SHA-256 `35ac3b8db255ef6770e6ec6a59b71937b4413b23609de2cebd878c5b62b134e9`; local input `au-api.json`).

Transformation: Official DataStore API records for Top 100 Baby Names 2024: Girl Names/Count of Girl Names and Boy Names/Count of Boy Names; sum counts across sexes, omit null name/count cells. Require all records (total) in response. Queensland births only, not all Australia. Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. Merge identical normalized names by summing counts. Select at most 5000 names by descending total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform sampling only: counts rank the capped selection but are not sampling weights.

### `de_first_names.txt`

Publisher: Stadt Bonn. Source page: https://opendata.bonn.de/dataset/vornamen-von-neugeborenen-im-jahr-2024
Licence: Creative Commons CC0 1.0 Universal (CC0-1.0): https://creativecommons.org/publicdomain/zero/1.0/
Licence statement: https://opendata.bonn.de/dataset/vornamen-von-neugeborenen-im-jahr-2024
Retrieval date: 2026-11-12. Rows: 3242. Shipped SHA-256: `bb903fe545d6078ae3fdf441e164664daeaefbdff216a3fcc37931fa84b92e22`.

Source: https://opendata.bonn.de/sites/default/files/Vornamenstatistik_2024-01-01_2024-12-31.csv (upstream SHA-256 `1552e9815d75dbd947adb9aa5fee890922d54060115d41407fbf8e9877121c77`; local input `de-first.csv`).

Transformation: UTF-8 semicolon CSV: vorname and anzahl; sum counts across geschlecht and position. Bonn newborns in 2024 only, not all Germany. Preserve case and accents. Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. Merge identical normalized names by summing counts. Select at most 5000 names by descending total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform sampling only: counts rank the capped selection but are not sampling weights.

### `fr_last_names.txt`

Publisher: Insee. Source page: https://www.insee.fr/fr/statistiques/3536630
Licence: Licence Ouverte / Open Licence version 2.0 (Etalab): https://www.etalab.gouv.fr/licence-ouverte-open-licence/
Licence statement: https://www.insee.fr/fr/information/2008466?page=copyright.htm
Retrieval date: 2026-11-12. Rows: 5000. Shipped SHA-256: `bf434300dc712bd1564ae3bc18869bf37c46c02b5f90bc958c955ec63db2d844`.

Source: https://www.insee.fr/fr/statistiques/fichier/3536630/noms2008nat_txt.zip (upstream SHA-256 `c8693ff69bed32621250f1fc06e71b686e72bba1dbd771055e431d305527cfba`; local input `fr-last.zip`).

Transformation: ZIP member noms2008nat_txt.txt, UTF-8 tab-separated: NOM; sum all eleven decade columns (1891–2000); exclude AUTRES NOMS aggregate. Preserve upstream uppercase spelling and accents. Source: Insee, Fichier des noms, updated 2018-05-22. Strip outer whitespace, normalize Unicode NFC; reject empty values and values containing anything other than Unicode letters/combining marks, spaces, apostrophes or hyphens. Merge identical normalized names by summing counts. Select at most 5000 names by descending total count, breaking ties by Unicode name; emit selected names in Unicode alphabetical order, UTF-8, LF, final newline. The existing one-name-per-line format supports uniform sampling only: counts rank the capped selection but are not sampling weights.

Attribution: Contains information from National Records of Scotland, © Crown copyright, licensed under the Open Government Licence v3.0. Contains information licensed under the Open Government Licence – Ontario. © The State of Queensland (Department of Justice), Top 100 Baby Names 2024, CC BY 3.0 AU; adapted as described above. Stadt Bonn, Vornamen von Neugeborenen im Jahr 2024, CC0. Source : Insee, Fichier des noms, updated 2018-05-22; adapted as described above. No publisher endorses this package.

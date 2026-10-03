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
is copied from the reference data of release 3.0.1 (commit 422e78df2267e73bb2fa976267e48cb437861e2f)
of an earlier SQLLocks data-generation library, which is released under the MIT license with the
same copyright holder as Shape:

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

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

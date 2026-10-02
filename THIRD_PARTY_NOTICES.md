# Third-Party Notices

Shape is released under the MIT license (see `LICENSE`).

## GeoNames

Postal-code and place reference data is derived from GeoNames (https://www.geonames.org/).

GeoNames data is licensed under the Creative Commons Attribution 4.0 International
license (CC-BY-4.0), https://creativecommons.org/licenses/by/4.0/. Attribution:
"This work includes data from GeoNames (https://www.geonames.org/), licensed under
CC-BY-4.0." Any distribution that includes this data must retain this attribution.

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

## Domain reference data (Spindle)

The reference data of the `capital_markets`, `education`, `financial` and `retail` domains in
`plugins/shape-domains` (names, catalogs, exchange and sector lists, index memberships, constituents)
is copied from the reference data of Spindle 3.0.1 (https://github.com/sqllocks/spindle,
commit 422e78df2267e73bb2fa976267e48cb437861e2f), which is released under the MIT license with the
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

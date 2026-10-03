"""Build the reference packs (W3-12) from their source files, and check the committed packs.

    python scripts/build_reference_packs.py PACK --source FILE [--out DIR] [--check]

PACK is one of ``us-zip-city``, ``iso-3166-1``, ``iso-639-1`` and ``iban-lengths`` (shipped:
their sources are pinned by SHA-256 below, so a rebuild from the same file gives the same bytes),
or ``iso-4217`` and ``iso-639`` (not shipped, because their publishers state no licence that
allows redistribution; build them locally into a directory on ``SHAPE_REFERENCE_PATH``).

The script reads local files only; the download addresses are in ``docs/REFERENCE_PACKS.md``.
``--check`` rebuilds into a temporary directory and compares it with ``--out`` (default: where
the pack is committed): exit 1, with the files that differ, when the committed pack is not what
the source produces. A source whose SHA-256 is not the pinned one is refused unless
``--allow-unpinned-source`` is given.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pyarrow as pa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shape.reference import read_manifest, write_pack  # noqa: E402

CORE_DATA = ROOT / "src" / "shape" / "reference" / "data"
DOMAINS_PACKS = ROOT / "plugins" / "shape-domains" / "src" / "shape_domains" / "reference_packs"

#: SHA-256 of the file each shipped pack is built from.
PINNED = {
    "us-zip-city": "a2e9aa82edb6037deb1c5524c74cd7c948d337fb43c06a216d470989e901647f",  # US.txt
    "iso-3166-1": "d2844f9dbf6124d11a7b047f5381a467902d82a673be3d658f4c0791ffa0b83b",  # core.zip
    "iso-639-1": "d2844f9dbf6124d11a7b047f5381a467902d82a673be3d658f4c0791ffa0b83b",  # core.zip
    "iban-lengths": "4b25b3f6c334c60da9f426dcad835d1b2a082bba8d81ab119d1671e74c060ce8",  # json
}
#: Region codes CLDR carries with a numeric code that ISO 3166-1 does not assign (user-assigned).
NOT_ISO_3166_1 = frozenset({"XK"})
#: ISO 639-1 codes that CLDR marks deprecated only in favour of another code (fil, ak); ISO 639-1
#: still assigns them (Tagalog, Twi), so a validator must accept them.
ISO_639_1_RETAINED = frozenset({"tl", "tw"})
DEFAULT_OUT = {
    "us-zip-city": DOMAINS_PACKS / "us-zip-city",
    "iso-3166-1": CORE_DATA / "iso-3166-1",
    "iso-639-1": CORE_DATA / "iso-639-1",
    "iban-lengths": CORE_DATA / "iban-lengths",
}

GEONAMES_URL = "https://download.geonames.org/export/zip/US.zip"
CLDR_URL = "https://unicode.org/Public/cldr/48.2/core.zip"
SCHWIFTY_URL = "https://pypi.org/project/schwifty/2026.7.3/"
RETRIEVED = "2026-10-03"
UNICODE_ATTRIBUTION = (
    "Copyright (c) 2019-2025 Unicode, Inc. Data files of the Unicode Common Locale Data "
    "Repository (CLDR) are used under the Unicode License v3 "
    "(https://www.unicode.org/license.txt)."
)


class BuildError(SystemExit):
    """A build problem: the message is the exit text (exit code 2)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"build_reference_packs: {message}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise BuildError(f"source file not found: {path}") from None
    except IsADirectoryError:
        raise BuildError(f"{path} is a directory, a file is needed") from None


# --- sources ----------------------------------------------------------------------------------


def _geonames_text(source: Path) -> bytes:
    data = _read(source)
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            try:
                return z.read("US.txt")
            except KeyError:
                raise BuildError(f"{source} has no US.txt member") from None
    return data


def _cldr_member(source: Path, member: str) -> bytes:
    if source.is_dir():
        path = source / member
        if not path.is_file():
            raise BuildError(f"{source} has no {member}")
        return path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(_read(source))) as z:
        try:
            return z.read(member)
        except KeyError:
            raise BuildError(f"{source} has no {member}") from None


def _expand(compact: str) -> list[str]:
    """CLDR validity lists: ``AC~G`` is AC to AG (the range runs over the last letter)."""
    out: list[str] = []
    for token in re.sub(r"<!--.*?-->", "", compact, flags=re.S).split():
        if "~" in token:
            start, end = token.split("~")
            prefix = start[:-1]
            out.extend(prefix + chr(c) for c in range(ord(start[-1]), ord(end[-1]) + 1))
        else:
            out.append(token)
    return out


def _validity(source: Path, member: str, kind: str, status: str) -> list[str]:
    text = _cldr_member(source, member).decode("utf-8")
    found = re.search(rf"<id type='{kind}' idStatus='{status}'>(.*?)</id>", text, flags=re.S)
    if found is None:
        raise BuildError(f"{member} has no {status} {kind} codes")
    return _expand(found.group(1))


def _english_names(source: Path, tag: str) -> dict[str, str]:
    """The English display names of ``<tag type=..>`` entries of ``common/main/en.xml`` (an
    entry with an ``alt`` attribute is a variant and is not used)."""
    root = ElementTree.fromstring(_cldr_member(source, "common/main/en.xml"))
    return {
        el.attrib["type"]: (el.text or "").strip()
        for el in root.iter(tag)
        if "alt" not in el.attrib and "type" in el.attrib
    }


# --- the packs --------------------------------------------------------------------------------

Built = tuple[dict[str, pa.Table], dict[str, str]]


def _us_zip_city(source: Path, pin: bool) -> Built:
    text = _geonames_text(source)
    sha = _sha256(text)
    _pinned(source, "us-zip-city", sha, pin)
    seen: set[str] = set()
    rows: dict[str, list[str | None]] = {"zip": [], "city": [], "state": [], "county": []}
    reader = csv.reader(io.StringIO(text.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
    for rec in reader:
        if len(rec) < 6:
            continue
        zip_code, city, state, county = rec[1].strip(), rec[2].strip(), rec[4].strip(), rec[5]
        if not (re.fullmatch(r"\d{5}", zip_code) and city and state) or zip_code in seen:
            continue
        seen.add(zip_code)
        rows["zip"].append(zip_code)
        rows["city"].append(city)
        rows["state"].append(state)
        rows["county"].append(county.strip() or None)
    if not rows["zip"]:
        raise BuildError(f"{source} has no usable rows (ZIP, place and state code)")
    table = pa.table({k: pa.array(v, pa.string()) for k, v in rows.items()})
    return {"us_zip_city": table}, {
        "source": f"GeoNames postal codes, US.zip ({GEONAMES_URL}), US.txt sha256 {sha}",
        "license": "CC-BY-4.0",
        "attribution": (
            "This work includes data from GeoNames (https://www.geonames.org/), "
            "licensed under CC-BY-4.0."
        ),
    }


def _cldr_source(source: Path, pack: str, pin: bool) -> str:
    if source.is_dir():
        _pinned(source, pack, None, pin)
        return "Unicode CLDR common/ directory (unpinned source)"
    sha = _sha256(_read(source))
    _pinned(source, pack, sha, pin)
    return f"Unicode CLDR 48.2 core.zip ({CLDR_URL}), sha256 {sha}"


def _iso_3166_1(source: Path, pin: bool) -> Built:
    where = _cldr_source(source, "iso-3166-1", pin)
    regular = _validity(source, "common/validity/region.xml", "region", "regular")
    supplemental = _cldr_member(source, "common/supplemental/supplementalData.xml").decode("utf-8")
    codes = {
        m.group(1): (m.group(2), m.group(3))
        for m in re.finditer(
            r'<territoryCodes type="(\w+)"(?: numeric="(\d+)")?(?: alpha3="(\w+)")?', supplemental
        )
    }
    names = _english_names(source, "territory")
    rows: dict[str, list[str]] = {"alpha2": [], "alpha3": [], "numeric": [], "name": []}
    for alpha2 in sorted(set(regular)):
        numeric, alpha3 = codes.get(alpha2, (None, None))
        if not numeric or not alpha3 or alpha2 in NOT_ISO_3166_1:
            continue  # reserved, exceptionally reserved or user-assigned: not an ISO 3166-1 code
        if not names.get(alpha2):
            raise BuildError(f"no English name for region {alpha2} in common/main/en.xml")
        rows["alpha2"].append(alpha2)
        rows["alpha3"].append(alpha3)
        rows["numeric"].append(numeric.zfill(3))
        rows["name"].append(names[alpha2])
    table = pa.table({k: pa.array(v, pa.string()) for k, v in rows.items()})
    return {"iso_3166_1": table}, {
        "source": (
            f"{where}: regions with idStatus regular in common/validity/region.xml that have a "
            "numeric code in common/supplemental/supplementalData.xml, without XK (a "
            "user-assigned code); English names from common/main/en.xml"
        ),
        "license": "Unicode-3.0",
        "attribution": UNICODE_ATTRIBUTION,
    }


def _iso_639_1(source: Path, pin: bool) -> Built:
    where = _cldr_source(source, "iso-639-1", pin)
    regular = _validity(source, "common/validity/language.xml", "language", "regular")
    names = _english_names(source, "language")
    deprecated = _validity(source, "common/validity/language.xml", "language", "deprecated")
    two = sorted(
        {c for c in regular if len(c) == 2} | {c for c in deprecated if c in ISO_639_1_RETAINED}
    )
    for code in two:
        if not names.get(code):
            raise BuildError(f"no English name for language {code} in common/main/en.xml")
    table = pa.table(
        {
            "alpha2": pa.array(two, pa.string()),
            "name": pa.array([names[c] for c in two], pa.string()),
        }
    )
    return {"iso_639_1": table}, {
        "source": (
            f"{where}: two-letter language codes with idStatus regular in "
            "common/validity/language.xml, plus tl and tw (ISO 639-1 codes that CLDR deprecates "
            "in favour of fil and ak); English names from common/main/en.xml"
        ),
        "license": "Unicode-3.0",
        "attribution": UNICODE_ATTRIBUTION,
    }


def _iban_lengths(source: Path, pin: bool) -> Built:
    data = _read(source)
    sha = _sha256(data)
    _pinned(source, "iban-lengths", sha, pin)
    registry = json.loads(data)
    countries: list[str] = []
    lengths: list[int] = []
    for country in sorted(registry):
        length = registry[country].get("iban_length")
        if not isinstance(length, int) or isinstance(length, bool) or not 15 <= length <= 34:
            raise BuildError(f"{country}: no usable iban_length in {source.name}")
        countries.append(country)
        lengths.append(length)
    table = pa.table({"country": pa.array(countries, pa.string()), "length": pa.array(lengths)})
    return {"iban_lengths": table}, {
        "source": (
            f"schwifty 2026.7.3 ({SCHWIFTY_URL}), schwifty/iban_registry/generated.json "
            f"(sha256 {sha}), the IBAN length of each country, as in the SWIFT IBAN Registry"
        ),
        "license": "MIT",
        "attribution": (
            "Copyright (c) 2021 Martin Domke (schwifty), MIT License; the lengths are those "
            "of the SWIFT IBAN Registry."
        ),
    }


def _iso_4217(source: Path, pin: bool) -> Built:
    root = ElementTree.fromstring(_read(source))
    seen: dict[str, tuple[str, int | None]] = {}
    for entry in root.iter("CcyNtry"):
        code = (entry.findtext("Ccy") or "").strip()
        if not code:  # a country with no universal currency
            continue
        numeric = (entry.findtext("CcyNbr") or "").strip().zfill(3)
        minor_text = (entry.findtext("CcyMnrUnts") or "").strip()
        minor = int(minor_text) if minor_text.isdigit() else None
        if code in seen and seen[code] != (numeric, minor):
            raise BuildError(f"{code} is listed twice with different numeric code or minor units")
        seen[code] = (numeric, minor)
    if not seen:
        raise BuildError(f"{source} lists no currencies")
    codes = sorted(seen)
    table = pa.table(
        {
            "alphabetic_code": pa.array(codes, pa.string()),
            "numeric_code": pa.array([seen[c][0] for c in codes], pa.string()),
            "minor_units": pa.array([seen[c][1] for c in codes], pa.int32()),
        }
    )
    return {"iso_4217": table}, {
        "source": f"ISO 4217 list one (SIX Group, the ISO 4217 maintenance agency), {source.name} "
        f"sha256 {_sha256(_read(source))}",
        "license": "local build: the publisher states no licence that allows redistribution; "
        "do not redistribute this pack",
        "attribution": "ISO 4217 currency codes, maintained by SIX Group on behalf of ISO.",
    }


def _iso_639(source: Path, pin: bool) -> Built:
    text = _read(source).decode("utf-8-sig")
    alpha2: list[str | None] = []
    alpha3: list[str] = []
    names: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip():
            raise BuildError(
                f"{source.name} line {number}: expected alpha3-b|alpha3-t|alpha2|name|fr"
            )
        bibliographic, terminologic, two, english = (p.strip() for p in parts[:4])
        alpha3.append(terminologic or bibliographic)
        alpha2.append(two or None)
        names.append(english)
    if not names:
        raise BuildError(f"{source} lists no languages")
    order = sorted(range(len(alpha3)), key=lambda i: alpha3[i])
    table = pa.table(
        {
            "alpha2": pa.array([alpha2[i] for i in order], pa.string()),
            "alpha3_t": pa.array([alpha3[i] for i in order], pa.string()),
            "name": pa.array([names[i] for i in order], pa.string()),
        }
    )
    return {"iso_639": table}, {
        "source": f"ISO 639-2 code list, Library of Congress (ISO 639-2 Registration Authority), "
        f"{source.name} sha256 {_sha256(_read(source))}",
        "license": "local build: the publisher states no licence that allows redistribution; "
        "do not redistribute this pack",
        "attribution": "ISO 639-2 language codes, Library of Congress.",
    }


def _pinned(source: Path, pack: str, sha: str | None, pin: bool) -> None:
    if not pin:
        return
    if sha is None:
        raise BuildError(
            f"{pack}: a pinned build needs the original archive, not an unpacked directory "
            "(--allow-unpinned-source builds from anything)"
        )
    if sha != PINNED[pack]:
        raise BuildError(
            f"{pack}: {source} (sha256 {sha}) is not the pinned source (sha256 {PINNED[pack]}); "
            "pass --allow-unpinned-source to build from it anyway"
        )


BUILDERS: dict[str, Callable[[Path, bool], Built]] = {
    "us-zip-city": _us_zip_city,
    "iso-3166-1": _iso_3166_1,
    "iso-639-1": _iso_639_1,
    "iban-lengths": _iban_lengths,
    "iso-4217": _iso_4217,
    "iso-639": _iso_639,
}
#: pack_version and transformation_version of each pack. Raise pack_version when the data
#: changes, transformation_version when the way it is derived changes.
VERSIONS = {name: ("1.0.0", "1") for name in BUILDERS}


def build(
    pack: str,
    source: Path,
    out: Path,
    *,
    pin: bool = True,
    retrieved: str = RETRIEVED,
) -> dict[str, Any]:
    """Build ``pack`` from ``source`` into the directory ``out``; returns the manifest."""
    if pack not in BUILDERS:
        raise BuildError(f"unknown pack {pack!r} (one of {', '.join(BUILDERS)})")
    tables, meta = BUILDERS[pack](Path(source), pin)
    pack_version, transformation = VERSIONS[pack]
    return write_pack(
        out,
        name=pack,
        pack_version=pack_version,
        source=meta["source"],
        retrieved=retrieved,
        license=meta["license"],
        attribution=meta["attribution"],
        transformation_version=transformation,
        sensitivity="public",
        tables=tables,
    )


def check(
    pack: str, source: Path, out: Path, *, pin: bool = True, retrieved: str = RETRIEVED
) -> list[str]:
    """The differences between the pack in ``out`` and what ``source`` produces (empty when it
    is the same, byte for byte, manifest included)."""
    problems: list[str] = []
    try:
        committed = read_manifest(out)
    except Exception as exc:  # a missing or malformed manifest is a difference, not a crash
        return [f"{out}: {exc}"]
    with tempfile.TemporaryDirectory() as tmp:
        fresh = build(pack, source, Path(tmp), pin=pin, retrieved=retrieved)
        for entry in fresh["datasets"]:
            path = out / entry["file"]
            actual = _sha256(path.read_bytes()) if path.is_file() else None
            if actual != entry["sha256"]:
                problems.append(
                    f"{pack}: {entry['file']} does not match its checksum "
                    f"(source gives {entry['sha256']}, committed file {actual})"
                )
        if not problems and fresh != committed:
            problems.append(f"{pack}: pack.json differs from what the source produces")
        for entry in committed["datasets"]:
            path = out / entry["file"]
            if path.is_file() and _sha256(path.read_bytes()) != entry["sha256"]:
                problems.append(f"{pack}: {entry['file']} does not match the checksum in pack.json")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack", choices=sorted(BUILDERS))
    ap.add_argument("--source", required=True, type=Path, help="the source file (see the docs)")
    ap.add_argument("--out", type=Path, help="the pack directory (default: where it is committed)")
    ap.add_argument("--check", action="store_true", help="compare, do not write")
    ap.add_argument("--allow-unpinned-source", action="store_true")
    ap.add_argument(
        "--retrieved",
        default=None,
        help=f"retrieval date for packs built locally (default {RETRIEVED} for shipped packs, "
        "today for the others)",
    )
    a = ap.parse_args(argv)
    out = a.out or DEFAULT_OUT.get(a.pack)
    if out is None:
        ap.error(f"{a.pack} is not shipped: give --out DIR")
    shipped = a.pack in PINNED
    retrieved = a.retrieved or (RETRIEVED if shipped else date.today().isoformat())
    pin = shipped and not a.allow_unpinned_source
    if a.check:
        problems = check(a.pack, a.source, out, pin=pin, retrieved=retrieved)
        for p in problems:
            print(p, file=sys.stderr)
        if not problems:
            print(f"{a.pack}: {out} matches its source")
        return 1 if problems else 0
    manifest = build(a.pack, a.source, out, pin=pin, retrieved=retrieved)
    for d in manifest["datasets"]:
        print(f"{a.pack}: wrote {out / d['file']} ({d['rows']} rows, sha256 {d['sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

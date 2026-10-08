"""The CMS risk-adjustment model software ("<year> Model Software/ICD-10 Mappings").

The software zip holds one zip per model package, named ``<kind> software <id>.zip``
(``CMS-HCC software V2826.115.T2.zip``, ``ESRD software E2426.86.T2.zip``,
``RxHCC software R0826.84.Y1.zip``). The id starts with a letter and two digits of the model
version (``V28``, ``E24``, ``R08``), so the model name matches the mapping table's
(``CMS-HCC V28``, ``ESRD V24``, ``RxHCC V08``). A package holds the SAS macros as ``.TXT`` files
(the hierarchy macro, the main macro that names the score variables, the main program) and the
coefficients as a ``.csv`` file. Several packages can carry one model (RxHCC V08 has three, one
per calibration population); the package id (``software``) tells them apart.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

PAGE = (
    "https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/"
    "2027-model-software-icd-10-mappings"
)
URL = "https://www.cms.gov/files/zip/2027-initial-model-software.zip"
RELEASE = "2027 Initial Model Software (packages of 2026-05-04)"

_PKG = re.compile(r"(CMS-HCC|ESRD|RxHCC) software (([A-Z])(\d{2})\d{2}\.\d+\.[A-Z0-9]+)\.zip$")


@dataclass(frozen=True, slots=True)
class Package:
    """One model package: its model name, its id and its text files (name -> text)."""

    model: str
    software: str
    files: dict[str, str]


def read_packages(data: bytes) -> list[Package]:
    """Every model package in the software zip, in the zip's order.

    Raises ``ValueError`` when the zip holds no package named as CMS names them."""
    out: list[Package] = []
    with zipfile.ZipFile(io.BytesIO(data)) as outer:
        for name in outer.namelist():
            m = _PKG.search(name.rsplit("/", 1)[-1])
            if not m:
                continue
            files: dict[str, str] = {}
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                for member in inner.namelist():
                    base = member.rsplit("/", 1)[-1]
                    if base.lower().endswith((".txt", ".csv")):
                        raw = inner.read(member)
                        files[base] = raw.decode("utf-8", errors="replace").replace("\r", "")
            out.append(Package(f"{m.group(1)} V{m.group(4)}", m.group(2), files))
    if not out:
        raise ValueError(
            "no model packages ('<kind> software <id>.zip') in the model software zip: "
            "the file layout changed"
        )
    return out


def read_software_zip(path: Path) -> list[Package]:
    return read_packages(path.read_bytes())


_ASOF = re.compile(r'DATE_ASOF\s*=\s*"\s*\d{1,2}\s*[A-Z]{3}\s*(\d{4})\s*"\s*D', re.I)


def payment_year(files: dict[str, str]) -> int:
    """The payment year of a package: the year of ``DATE_ASOF`` in its main program (CMS sets it
    to February 1 of the payment year). Raises ``ValueError`` when no program sets it."""
    years = {int(y) for text in files.values() for y in _ASOF.findall(text)}
    if len(years) != 1:
        raise ValueError(
            f"expected one DATE_ASOF payment year in the main program, found {sorted(years)}: "
            "the file layout changed"
        )
    return years.pop()

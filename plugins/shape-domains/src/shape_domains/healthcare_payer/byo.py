"""Bring-your-own licensed reference tables.

CPT (AMA), revenue codes and type-of-bill (NUBC) are licensed.  Shape ships none of them: a user who
holds a licence supplies a CSV with the columns ``table,key,value`` where ``table`` is one of

* ``cpt``     - ``key`` is a Shape service key (``LAB_HBA1C``), ``value`` the CPT code;
* ``revenue`` - ``key`` is a service key (``INPT_ROOM_BOARD``), ``value`` the revenue code;
* ``tob``     - ``key`` is ``<facility_type>:<frequency>`` (``inpatient:1``), ``value`` the type
  of bill;
* ``taxonomy`` - ``key`` is a specialty key (``family_medicine``), ``value`` the NUCC taxonomy
  code (NUCC is AMA-copyrighted; the codes lane treats it as bring-your-own).

Without it the CPT slot carries the service key (code system ``SHAPE-SVC``) and the revenue code and
type of bill stay empty.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .reference import SPECIALTY
from .services import SERVICES


@dataclass(frozen=True, slots=True)
class LicensedTables:
    cpt: dict[str, str] = field(default_factory=dict)
    revenue: dict[str, str] = field(default_factory=dict)
    tob: dict[str, str] = field(default_factory=dict)
    taxonomy: dict[str, str] = field(default_factory=dict)
    source: str = "none"


def load_licensed(path: str | Path) -> LicensedTables:
    cpt: dict[str, str] = {}
    revenue: dict[str, str] = {}
    tob: dict[str, str] = {}
    taxonomy: dict[str, str] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for n, row in enumerate(csv.DictReader(handle), start=2):
            table, key, value = (row.get(c, "").strip() for c in ("table", "key", "value"))
            if table == "cpt":
                if key not in SERVICES:
                    raise ValueError(f"{path}:{n}: unknown service key {key!r}")
                if len(value) != 5 or not value[:4].isalnum():
                    raise ValueError(f"{path}:{n}: a CPT code is five characters, got {value!r}")
                cpt[key] = value
            elif table == "revenue":
                if not (value.isdigit() and len(value) in (3, 4)):
                    raise ValueError(f"{path}:{n}: a revenue code is 3-4 digits, got {value!r}")
                revenue[key] = value
            elif table == "tob":
                if not (value.isdigit() and len(value) in (3, 4)):
                    raise ValueError(f"{path}:{n}: a type of bill is 3-4 digits, got {value!r}")
                tob[key] = value
            elif table == "taxonomy":
                if len(value) != 10 or not value.isalnum():
                    raise ValueError(f"{path}:{n}: a taxonomy code is 10 characters, got {value!r}")
                if key not in SPECIALTY:
                    raise ValueError(f"{path}:{n}: unknown specialty key {key!r}")
                taxonomy[key] = value
            else:
                raise ValueError(
                    f"{path}:{n}: unknown table {table!r} (cpt, revenue, tob, taxonomy)"
                )
    return LicensedTables(cpt, revenue, tob, taxonomy, str(path))

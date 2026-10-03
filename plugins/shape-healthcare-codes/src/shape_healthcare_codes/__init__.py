"""Shape plugin: healthcare reference code sets, validators and detectors.

The public API (all importable from here, lazily):

* :func:`load`, :func:`available`, :class:`CodeSet`, :class:`CodeSystem`: load a built code
  system (ICD-10-CM, ICD-10-PCS, HCPCS Level II, NDC, ...) as an Arrow-backed ``CodeSet`` with
  date-aware lookups (``is_valid``, ``codes_on``);
* :func:`icd10cm_valid_billable`, :func:`ndc_marketed`: validate on a date of service or fill
  date;
* the NPI helpers (:func:`luhn_valid`, :func:`is_synthetic_npi`,
  :func:`generate_synthetic_npis`): generated NPIs are in a never-assigned range;
* :func:`normalize_ndc`: the 11-digit 5-4-2 form;
* ``detectors``: ``shape.detectors`` entry points for ICD-10, NDC, NPI, HCPCS/CPT, member id.

See ``docs/plugins/healthcare-codes.md`` and the plugin README.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

SHAPE_API = "1.0"
"""The plugin targets the plugin API major version this Shape provides."""

_EXPORTS: dict[str, str] = {
    "AmbiguousNdc": "shape_healthcare_codes.ndc",
    "AssetMissing": "shape_healthcare_codes.store",
    "CodeRecord": "shape_healthcare_codes.model",
    "CodeSet": "shape_healthcare_codes.model",
    "CodeSystem": "shape_healthcare_codes.model",
    "NdcIndex": "shape_healthcare_codes.validators",
    "available": "shape_healthcare_codes.store",
    "check_digit": "shape_healthcare_codes.npi",
    "fiscal_year": "shape_healthcare_codes.model",
    "format_icd10": "shape_healthcare_codes.model",
    "generate_synthetic_npis": "shape_healthcare_codes.npi",
    "icd10cm_valid_billable": "shape_healthcare_codes.validators",
    "is_real_format_npi": "shape_healthcare_codes.npi",
    "is_synthetic_npi": "shape_healthcare_codes.npi",
    "load": "shape_healthcare_codes.store",
    "luhn_valid": "shape_healthcare_codes.npi",
    "ndc_marketed": "shape_healthcare_codes.validators",
    "normalize_code": "shape_healthcare_codes.model",
    "normalize_ndc": "shape_healthcare_codes.ndc",
}

if TYPE_CHECKING:
    from shape_healthcare_codes.model import (
        CodeRecord,
        CodeSet,
        CodeSystem,
        fiscal_year,
        format_icd10,
        normalize_code,
    )
    from shape_healthcare_codes.ndc import AmbiguousNdc, normalize_ndc
    from shape_healthcare_codes.npi import (
        check_digit,
        generate_synthetic_npis,
        is_real_format_npi,
        is_synthetic_npi,
        luhn_valid,
    )
    from shape_healthcare_codes.store import AssetMissing, available, load
    from shape_healthcare_codes.validators import NdcIndex, icd10cm_valid_billable, ndc_marketed


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'shape_healthcare_codes' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])


__all__ = [
    "AmbiguousNdc",
    "AssetMissing",
    "CodeRecord",
    "CodeSet",
    "CodeSystem",
    "NdcIndex",
    "available",
    "check_digit",
    "fiscal_year",
    "format_icd10",
    "generate_synthetic_npis",
    "icd10cm_valid_billable",
    "is_real_format_npi",
    "is_synthetic_npi",
    "load",
    "luhn_valid",
    "ndc_marketed",
    "normalize_code",
    "normalize_ndc",
]

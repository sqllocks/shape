"""Built-in semantic detectors: email, US SSN, phone and IPv4 (``shape.detectors``).

They wrap the conservative matchers in :mod:`shape.privacy.detect`, so the labels and
confidences are the ones the privacy layer already reports.
"""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api.v1 import Detection
from shape.privacy.detect import detect_column

SHAPE_API = "1.0"
_SAMPLE = 1000


class _Detector:
    name = ""
    kind = ""

    def detect(self, values: pa.Array, column: str) -> Detection | None:
        sample = values.slice(0, _SAMPLE).to_pylist()
        for hit in detect_column(sample, sample_limit=_SAMPLE):
            if hit.kind == self.kind:
                return Detection(self.name, float(hit.confidence))
        return None


class EmailDetector(_Detector):
    """Detect email patterns using the privacy layer sample matcher."""

    name = "email"
    kind = "email"


class UsSsnDetector(_Detector):
    """Detect US SSN patterns using the privacy layer sample matcher."""

    name = "us_ssn"
    kind = "us_ssn"


class PhoneDetector(_Detector):
    """Detect phone patterns using the privacy layer sample matcher."""

    name = "phone"
    kind = "phone"


class Ipv4Detector(_Detector):
    """Detect IPv4 patterns using the privacy layer sample matcher."""

    name = "ipv4"
    kind = "ipv4"


__all__ = ["SHAPE_API", "EmailDetector", "Ipv4Detector", "PhoneDetector", "UsSsnDetector"]

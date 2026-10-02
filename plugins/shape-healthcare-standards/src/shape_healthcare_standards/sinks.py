"""Entry-point targets of the ``shape.sinks`` group, one per output format."""

from .fhir.sink import FhirBundleSink, FhirNdjsonSink
from .ncpdp.sink import NcpdpSink
from .omop.sink import OmopSink
from .x12.sink import (
    X12Claim837ISink,
    X12Claim837PSink,
    X12Enrollment834Sink,
    X12Remittance835Sink,
)

__all__ = [
    "FhirBundleSink",
    "FhirNdjsonSink",
    "NcpdpSink",
    "OmopSink",
    "X12Claim837ISink",
    "X12Claim837PSink",
    "X12Enrollment834Sink",
    "X12Remittance835Sink",
]

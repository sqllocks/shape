"""Entry-point target of the ``shape.emitters`` group."""

from .fhir.emitter import FhirEmitter

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "FhirEmitter"]

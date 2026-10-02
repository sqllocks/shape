"""Entry-point target of the ``shape.emitters`` group."""

from .fhir.emitter import FhirEmitter

__all__ = ["FhirEmitter"]

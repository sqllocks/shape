"""Generic domain factories loaded through the documented ``shape.domains`` entry point."""

from __future__ import annotations

from shape.plugins.api.v1 import DomainDefinition

SHAPE_API = "1.0"


class WithoutDescription:
    """A valid extension deliberately omitting the optional description attribute."""

    name = "fixture_beta"

    def definition(self) -> DomainDefinition:
        return DomainDefinition(
            schema={
                "schema_version": 1,
                "model": {"name": self.name, "description": "Fixture schema description"},
                "tables": {
                    "item": {
                        "name": "item",
                        "primary_key": ["id"],
                        "columns": {
                            "id": {
                                "name": "id",
                                "type": "integer",
                                "generator": {"strategy": "sequence"},
                            }
                        },
                    }
                },
                "generation": {"scales": {"small": {"item": 2}}},
            }
        )


class WithDescription(WithoutDescription):
    """An extension providing its own domain description."""

    name = "fixture_alpha"
    description = "Generic extension domain"

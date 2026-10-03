"""``shape pin``: write the current generator version of every strategy and distribution a spec
uses into its ``generators`` map, or list the ones it does not pin
(``docs/GENERATION_STABILITY.md``).

Pinning adds the names that are not pinned yet; a pin that is already there is never moved, because
moving it would change the data. The spec is edited through :class:`SpecDocument`, so unknown
fields, ``x-*`` keys and ``$comment`` survive and an unchanged spec is written back byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shape.generation import versions
from shape.generation.schema import GenSchema, schema_problems
from shape.generation.spec_edit import SpecDocument


@dataclass(slots=True)
class PinReport:
    """What a spec uses and what it pins. ``current`` is the latest version of every name the spec
    uses; ``unpinned`` the names it uses but does not pin; ``unused`` the pins of names it does
    not use; ``added`` what :func:`pin` wrote."""

    current: dict[str, int]
    pinned: dict[str, int]
    unpinned: list[str]
    unused: list[str]
    added: dict[str, int] = field(default_factory=dict)


def inspect(doc: SpecDocument, source: str = "the spec") -> PinReport:
    """The pins of ``doc`` against the generators it uses. Raises ``SpecError`` for a spec that is
    not valid and :class:`~shape.generation.versions.GeneratorPinError` for a pin to a version this
    Shape does not have (the message starts with ``source``)."""
    if schema_problems(doc.to_dict()):
        doc.raise_for_errors()
    schema = GenSchema.from_dict(doc.to_dict())
    usage = versions.usage_of(schema.tables)
    unused = versions.check_pins(schema.generators, usage, source)
    doc.raise_for_errors()
    current = versions.current_versions(usage)
    return PinReport(
        current=current,
        pinned={n: v for n, v in schema.generators.items() if n in usage},
        unpinned=[n for n in current if n not in schema.generators],
        unused=unused,
    )


def pin(doc: SpecDocument, source: str = "the spec") -> PinReport:
    """Pin every used name that is not pinned at its current version; the report's ``added`` has
    them. The document is left unchanged when everything is pinned already."""
    report = inspect(doc, source)
    for name in report.unpinned:
        doc.set_generator_version(name, report.current[name])
        report.added[name] = report.current[name]
    report.pinned = {**report.pinned, **report.added}
    report.unpinned = []
    return report

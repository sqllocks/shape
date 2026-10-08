"""The functions behind ``import shape`` (reference: ``docs/API.md``).

``profile``, ``save``, ``load``, ``check`` and ``diff`` are the early-access surface; the
generation and query functions read the documents their docstrings name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shape.contracts.v1 import check as check
from shape.contracts.v1 import diff as diff
from shape.profile.reference import load as load
from shape.profile.reference import profile as profile
from shape.profile.reference import save as save
from shape.profile.types_report import types_report as types_report

if TYPE_CHECKING:
    from shape.generation.fidelity import FidelityCertificate, ReconstructionPlan
    from shape.generation.timeline import ShapeTimeline
    from shape.query import ShapeView


def _is_profile(obj: Any) -> bool:
    """True for a 0.9 profile (``shape.profile``) or its ``to_dict()``."""
    if type(obj).__module__.startswith("shape.profile.reference"):
        return True
    if isinstance(obj, dict):
        if {"row_count", "columns", "detected_fks"} <= obj.keys():
            return True
        tables = obj.get("tables")
        if isinstance(tables, dict) and tables:
            return all(isinstance(t, dict) and "row_count" in t for t in tables.values())
    return False


def _refuse(form: str, **given: Any) -> None:
    """``TypeError`` naming every argument in ``given`` that was passed (is not ``None``)."""
    passed = [name for name, value in given.items() if value is not None]
    if passed:
        raise TypeError(f"generate() from {form} does not take {', '.join(passed)}")


def generate(
    shape: Any,
    n: Any = None,
    seed: int | None = None,
    relationships: Any = None,
    *,
    scale: str | None = None,
    mode: str | None = None,
    mixed_copula: bool = False,
    identifiers: str | None = None,
) -> Any:
    """Generate data from a domain, a generation schema, a profile or an evidence document.

    What ``shape`` is decides the form, the arguments used and the result:

    - a **domain name** (``str``), a ``GenSchema`` or a generation schema ``dict`` (one with a
      ``"tables"`` key): ``generate("retail", scale="medium", seed=42, mode="star")`` runs the
      schema through the engine and returns a ``GenerationResult``; ``result.tables`` maps each
      table name to a ``pyarrow.Table`` (so does ``result["order"]``). ``scale`` is a preset
      name, ``seed`` defaults to the schema's and ``mode`` (``3nf`` or ``star``) picks a
      domain's schema. A composite preset (``"enterprise"``) or domains joined by ``+``
      (``"retail+hr"``) run those domains as one dataset (``shape composite``). Row counts come
      from the scale preset.
    - a **profile** (``shape.profile``'s result or its ``to_dict()``): fits a schema to it and
      returns a ``GenerationResult``. The default scale keeps the profile's row counts; ``n``
      replaces the row count of a one-table profile (a dataset raises ``ValueError``).
    - anything else is read as an **evidence document**, a mapping such as
      ``{"rows": 100, "columns": {"x": {"kind": "numeric", "mean": 0, ...}}}``:
      ``generate(evidence, n, seed, relationships)`` returns a ``(columns, GenerationReport)``
      tuple, where ``columns`` maps each name to a NumPy array; ``seed`` defaults to 0. The
      ``shape.Shape`` model is not accepted.

    An argument the form cannot use (``n`` or ``relationships`` for a domain or a schema,
    ``relationships`` for a profile, ``mode`` for anything but a domain, ``scale`` for an evidence
    document, ``mixed_copula`` for anything but a profile) raises ``TypeError`` (issue #251).
    Raises ``DomainNotFoundError`` (a ``ShapeError``) for an unknown domain name.

    From a profile, ``mixed_copula=True`` links the numeric and categorical columns by the
    profile's mixed-type Gaussian copula (``joint.copula``, ``docs/JOINT.md``); off by default.

    ``identifiers`` is the run switch of the identifier providers (``email``, ``company_email``,
    ``uri``, ``phone_number``, ``ssn`` and the ``faker`` package's e-mail, URL and phone
    providers): ``"reserved"`` (values that cannot belong to a real person) or ``"realistic"``
    (they can; never use such data outside a test system, and the run says so once on standard
    error). Left out, the schema's own ``"identifiers"`` applies, else ``reserved``; a column's
    ``domains`` or ``range`` key wins over both. Any other value raises ``ValueError``. An
    evidence document has no identifier columns, so the switch changes nothing there.
    """
    from shape.generation.identifiers import announce, check_identifiers
    from shape.generation.schema import GenSchema

    if identifiers is not None:
        check_identifiers(identifiers)

    if _is_profile(shape):
        _refuse("a profile", relationships=relationships, mode=mode)
        from shape.generation.engine import Engine
        from shape.generation.fit import PRESET, fit_schema

        rows = None if n is None else int(n)
        fitted = fit_schema(shape, rows=rows, mixed_copula=mixed_copula)
        engine = Engine(fitted.schema, scale=scale or PRESET, seed=seed, identifiers=identifiers)
        announce(engine.identifiers)
        return engine.generate()
    if isinstance(shape, str):
        _refuse("a domain", n=n, relationships=relationships, mixed_copula=mixed_copula or None)
        from shape.generation.composite import is_composite, resolve
        from shape.generation.domains import load_domain
        from shape.generation.engine import Engine

        if is_composite(shape):  # a composite preset, or domains joined by "+": "retail+hr"
            schema = resolve(shape).schema
        else:
            schema = load_domain(shape, mode=mode).schema
        engine = Engine(schema, scale=scale, seed=seed, identifiers=identifiers)
        announce(engine.identifiers)
        return engine.generate()
    if isinstance(shape, GenSchema) or (isinstance(shape, dict) and "tables" in shape):
        _refuse(
            "a generation schema",
            n=n,
            relationships=relationships,
            mode=mode,
            mixed_copula=mixed_copula or None,
        )
        from shape.generation.engine import Engine

        schema = shape if isinstance(shape, GenSchema) else GenSchema.from_dict(shape)
        engine = Engine(schema, scale=scale, seed=seed, identifiers=identifiers)
        announce(engine.identifiers)
        return engine.generate()
    _refuse("an evidence document", scale=scale, mode=mode, mixed_copula=mixed_copula or None)
    from shape.generation import generate_from_shape

    return generate_from_shape(shape, n, 0 if seed is None else seed, relationships)


def timeline(versions: Any) -> ShapeTimeline:
    """Build a ``ShapeTimeline`` from ``versions``, objects with ``version``, ``at`` and ``shape``
    (``shape.generation.timeline.VersionedShape``), sorted by ``at``.

    ``shape_at(t)`` interpolates the evidence document at time ``t``, ``generate_at`` and
    ``generate_range`` generate from it and ``changes()`` reports drift between versions.
    Raises ``ValueError`` when ``versions`` is empty or two share a time.
    """
    from shape.generation import ShapeTimeline

    return ShapeTimeline(versions)  # type: ignore[no-untyped-call]


def view(shape: Any) -> ShapeView:
    """Wrap a Shape model document (v2, or a v1 capture migrated on read) in a ``ShapeView``,
    whose ``query``, ``column``, ``relationship`` and ``classification`` methods run Shape Queries.

    The document is read when a method is first called: a profile or a ``shape.Shape`` object
    fails there with ``ModelError``.
    """
    from shape.query import ShapeView

    return ShapeView(shape)


def query(shape: Any, expression: Any) -> Any:
    """Evaluate the Shape Query ``expression`` over a Shape model document and return the value.

    Roots: ``rows`` (or ``rows("table")``), ``column("name")``, ``classification("name")`` and
    ``relationship("a", "b")``, each followed by an optional ``.field.field`` path; a missing
    column or relationship gives ``None``. An unsupported or unsafe expression raises
    ``ShapeQueryError`` (SH2-028), and a document that is not a Shape model (a profile, for one)
    raises ``ModelError``. No code is evaluated (SH2-029).
    """
    from shape.query import query as _query

    return _query(shape, expression)


def certify(target: Any, observed: Any, **kwargs: Any) -> FidelityCertificate:
    """Score how well the evidence document ``observed`` matches ``target`` and return a
    ``FidelityCertificate`` (per-dimension scores: schema, null behaviour, ...).

    Both are evidence documents (mappings with a ``"columns"`` object), not profiles.
    ``kwargs`` are ``degraded`` and ``unavailable``, tuples of evidence names recorded on the
    certificate.
    """
    from shape.generation.fidelity import certify_shapes

    return certify_shapes(target, observed, **kwargs)


def plan(shape: Any) -> ReconstructionPlan:
    """List what data generated from ``shape`` keeps and what it does not, as a
    ``ReconstructionPlan`` of ``PlanItem(evidence, status, reason)``.

    ``shape`` is a profile, its ``to_dict()``, or an evidence document (as for ``generate``).
    """
    from shape.generation.fidelity import plan_reconstruction

    return plan_reconstruction(shape)

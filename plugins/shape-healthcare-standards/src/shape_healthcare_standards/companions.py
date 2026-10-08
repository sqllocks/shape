"""Which companion tables each writer needs: the data behind the ``tables`` option.

Every writer receives one table as its batches and reads the others it needs from the ``tables``
option (see :func:`shape_healthcare_standards.common.build_tables`). This module is the single
list of what each writer requires and what it reads when present; the documentation table is
generated from it (:func:`markdown_table`) and a test keeps the two equal.
"""

from __future__ import annotations

from . import contract

# (required, optional) per sink entry point name. "Required": the writer raises ContractError
# when the table is missing and a row needs it. "Optional": read when present, and the output
# changes with it. The primary table the sink receives counts as present.
_TABLES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "x12-837p": (
        frozenset({"member", "provider", "medical_claim_line", "claim_diagnosis"}),
        frozenset({"eligibility"}),
    ),
    "x12-837i": (
        frozenset({"member", "provider", "medical_claim_line", "claim_diagnosis"}),
        frozenset({"eligibility", "claim_procedure"}),
    ),
    "x12-835": (
        frozenset({"member", "provider"}),
        frozenset({"eligibility", "medical_claim_line"}),
    ),
    "x12-834": (frozenset({"eligibility"}), frozenset({"provider"})),
    # the 277CA acknowledges claims: the claim is required, a service line when a row names one
    "x12-277ca": (
        frozenset({"medical_claim", "medical_claim_line"}),
        frozenset({"member", "provider"}),
    ),
    "fhir-ndjson": (
        frozenset(),
        frozenset(
            {
                "member",
                "eligibility",
                "provider",
                "medical_claim_line",
                "claim_diagnosis",
                "claim_procedure",
                "drug_reference",
            }
        ),
    ),
    "omop": (
        frozenset({"member"}),
        frozenset(contract.TABLE_NAMES) - {"member", "claim_acknowledgment"},
    ),
    "ncpdp": (frozenset(), frozenset({"member", "provider", "drug_reference"})),
}
_TABLES["fhir-bundle"] = _TABLES["fhir-ndjson"]
_TABLES["fhir"] = _TABLES["fhir-ndjson"]  # the emitter reads the same tables as the FHIR sinks

SINK_NAMES: tuple[str, ...] = (
    "x12-837p",
    "x12-837i",
    "x12-835",
    "x12-834",
    "x12-277ca",
    "fhir-ndjson",
    "fhir-bundle",
    "omop",
    "ncpdp",
    "fhir",
)
"""The ``shape.sinks`` entry points of this plugin, then the ``fhir`` emitter."""

assert set(SINK_NAMES) == set(_TABLES)
assert all(
    (req | opt) <= set(contract.TABLE_NAMES) and not (req & opt) for req, opt in _TABLES.values()
)


def _in_contract_order(names: frozenset[str]) -> list[str]:
    return [t for t in contract.TABLE_NAMES if t in names]


def companion_tables(sink_name: str) -> dict[str, list[str]]:
    """The contract tables a writer takes from the ``tables`` option.

    ``sink_name`` is a ``shape.sinks`` entry point of this plugin (``x12-837p``, ``x12-837i``,
    ``x12-835``, ``x12-834``, ``x12-277ca``, ``fhir-ndjson``, ``fhir-bundle``, ``omop``,
    ``ncpdp``) or the ``fhir`` emitter. Returns ``{"required": [...], "optional": [...]}``,
    each a list of contract table names in contract order. *Required* tables must be present
    (as the primary table or in ``tables``) whenever the writer has a row that needs them, else
    it raises :class:`~shape_healthcare_standards.contract.ContractError`; *optional* tables are
    read when present and the output is unchanged by tables in neither list. Raises
    :class:`ValueError` for any other name.
    """
    try:
        required, optional = _TABLES[sink_name]
    except KeyError:
        raise ValueError(f"unknown sink {sink_name!r}; sinks: {list(SINK_NAMES)}") from None
    return {"required": _in_contract_order(required), "optional": _in_contract_order(optional)}


def markdown_table() -> str:
    """The documentation table of :func:`companion_tables` for every writer, as Markdown."""
    rows = ["| Writer | Required | Read when present |", "|---|---|---|"]
    for name in SINK_NAMES:
        t = companion_tables(name)

        def cell(names: list[str]) -> str:
            return ", ".join(f"`{n}`" for n in names) or "none"

        rows.append(f"| `{name}` | {cell(t['required'])} | {cell(t['optional'])} |")
    return "\n".join(rows)

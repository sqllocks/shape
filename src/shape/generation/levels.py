"""Explicit fidelity levels and evidence requirements."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FidelityLevel:
    name: str
    required: tuple[str, ...]


LEVELS = {
    "bronze": FidelityLevel("bronze", ("schema", "nullability")),
    "silver": FidelityLevel("silver", ("schema", "nullability", "univariate")),
    "gold": FidelityLevel(
        "gold",
        (
            "schema",
            "nullability",
            "univariate",
            "categorical_distribution",
            "missingness_dependencies",
            "dependencies",
            "semantics",
        ),
    ),
    "platinum": FidelityLevel(
        "platinum",
        (
            "schema",
            "nullability",
            "univariate",
            "categorical_distribution",
            "missingness_dependencies",
            "dependencies",
            "nonlinear_dependencies",
            "semantics",
            "temporal",
            "relational",
            "geographic",
        ),
    ),
}


def assess_fidelity(evidence: set[str]) -> str:
    best = "bronze"
    for n in ("bronze", "silver", "gold", "platinum"):
        if set(LEVELS[n].required).issubset(evidence):
            best = n
    return best

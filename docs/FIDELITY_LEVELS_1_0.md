# Fidelity Evidence Levels — RQ-3 Refinement

Fidelity levels describe **which evidence classes were measured and passed**, not a universal promise that arbitrary source data is reproducible.

- Bronze: schema, nullability.
- Silver: Bronze + univariate evidence.
- Gold: Silver + categorical distributions, missingness dependencies, declared/inferred dependencies, semantics.
- Platinum: Gold + nonlinear dependencies, temporal behavior, relational integrity and geographic distributions.

A certificate MUST NOT claim a level if a required evidence class was not measured. A descriptive aggregate score never overrides a failed required metric.

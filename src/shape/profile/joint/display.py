"""Short text lines for the multivariate entries of a table's ``joint`` (W3-08): what
``shape profile --html`` prints under the table, and what a notebook or a script can print.
``shape show`` prints the entries themselves, as JSON."""

from __future__ import annotations

from typing import Any


def describe_joint(joint: dict[str, Any] | None) -> list[str]:
    """One line per multivariate entry the profile holds (none for an older profile)."""
    if not joint:
        return []
    out: list[str] = []
    pairs = [d for d in joint.get("dependencies", ()) if len(d["determinant"]) > 1]
    for d in pairs[:5]:
        out.append(
            f"({', '.join(d['determinant'])}) -> {d['dependent']}: "
            f"confidence {d['confidence']:.3f} ({d['violating_groups']} violating groups)"
        )
    mo = joint.get("multivariate_outliers")
    if mo:
        top = max(mo["contributions"].items(), key=lambda kv: kv[1])
        out.append(
            f"multivariate outliers: {mo['rate']:.2%} of {mo['rows']:,} rows over "
            f"{len(mo['columns'])} columns (mcd, h={mo['h']:,}); mostly {top[0]} "
            f"({top[1]:.0%} of their distance)"
        )
    pca = joint.get("pca")
    if pca:
        lead = ", ".join(f"{r:.0%}" for r in pca["explained_variance_ratio"][:3])
        out.append(
            f"structure: {pca['effective_dimension']} effective dimensions of "
            f"{len(pca['columns'])} numeric columns (leading components explain {lead})"
        )
    cohorts = joint.get("cohorts")
    if cohorts:
        if cohorts["found"]:
            shares = ", ".join(f"{c['share']:.0%}" for c in cohorts["cohorts"])
            out.append(
                f"cohorts: {cohorts['k']} (silhouette {cohorts['silhouette']:.2f}); shares {shares}"
            )
        else:
            out.append(f"cohorts: none found (best silhouette {cohorts['silhouette']:.2f})")
    cop = joint.get("copula")
    if cop:
        out.append(
            f"copula: {len(cop['numeric'])} numeric and {len(cop['categorical'])} categorical "
            "columns (`shape generate --from --mixed-copula`)"
        )
    return out

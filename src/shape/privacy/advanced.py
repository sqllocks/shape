def k_anonymous(groups, k):
    """Filter group counts below the supplied minimum size; this is not a sharing approval."""
    return {g: n for g, n in groups.items() if n >= k}


def reidentification_risk(groups):
    """Return the share of rows in singleton groups."""
    total = sum(groups.values()) or 1
    unique = sum(n for n in groups.values() if n == 1)
    return unique / total

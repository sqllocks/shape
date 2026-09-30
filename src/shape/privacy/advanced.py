def k_anonymous(groups, k):
    return {g: n for g, n in groups.items() if n >= k}


def reidentification_risk(groups):
    total = sum(groups.values()) or 1
    unique = sum(n for n in groups.values() if n == 1)
    return unique / total

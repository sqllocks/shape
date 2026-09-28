import math
import random


def laplace(value, sensitivity, epsilon, seed=0):
    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    u = random.Random(seed).random() - 0.5
    noise = -(sensitivity / epsilon) * math.copysign(math.log(1 - 2 * abs(u)), u)
    return value + noise


def k_anonymous(groups, k):
    return {g: n for g, n in groups.items() if n >= k}


def reidentification_risk(groups):
    total = sum(groups.values()) or 1
    unique = sum(n for n in groups.values() if n == 1)
    return unique / total

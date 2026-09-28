from collections import Counter


def profile(edges):
    out = Counter()
    inc = Counter()
    nodes = set()
    for a, b in edges:
        nodes |= {a, b}
        out[a] += 1
        inc[b] += 1
    return {
        "nodes": len(nodes),
        "edges": sum(out.values()),
        "out_degree": dict(Counter(out.values())),
        "in_degree": dict(Counter(inc.values())),
    }


def generate(n, mean_degree=2, seed=0):
    import random

    r = random.Random(seed)
    return [(i, r.randrange(n)) for i in range(n) for _ in range(max(0, int(mean_degree)))]

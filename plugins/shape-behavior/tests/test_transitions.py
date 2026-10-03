"""Transition probabilities are recovered within a stated statistical tolerance."""

import math

from helpers import END, counts, module, run, start


def _within(observed: int, n: int, p: float, sigmas: float = 4.5) -> bool:
    """Whether ``observed`` of ``n`` is within ``sigmas`` standard deviations of ``n * p``."""
    return abs(observed - n * p) <= sigmas * math.sqrt(n * p * (1 - p))


def _branch(p_a: float, **extra):
    return module(
        {
            "s": start("pick"),
            "pick": {
                "type": "simple",
                "transition": {"distributed": [{"p": p_a, "to": "a"}, {"p": 1 - p_a, "to": "b"}]},
            },
            "a": {"type": "event", "event": "chose_a", "transition": {"direct": "end"}},
            "b": {"type": "event", "event": "chose_b", "transition": {"direct": "end"}},
            "end": END,
        },
        **extra,
    )


def test_distributed_transition_recovers_its_probabilities():
    n = 40_000
    for p in (0.05, 0.3, 0.5, 0.9):
        c = counts(run([_branch(p)], size=n, seed=11), "kind")
        assert _within(c["chose_a"], n, p), (p, c)
        assert c["chose_a"] + c["chose_b"] == n


def test_three_way_split():
    n = 60_000
    ps = {"x": 0.2, "y": 0.5, "z": 0.3}
    states = {"s": start("pick"), "end": END}
    states["pick"] = {
        "type": "simple",
        "transition": {"distributed": [{"p": p, "to": k} for k, p in ps.items()]},
    }
    for k in ps:
        states[k] = {"type": "event", "event": f"got_{k}", "transition": {"direct": "end"}}
    c = counts(run([module(states)], size=n, seed=5), "kind")
    for k, p in ps.items():
        assert _within(c[f"got_{k}"], n, p), (k, c)


def test_probability_read_from_an_attribute():
    n = 40_000
    m = module(
        {
            "s": start("pick"),
            "pick": {
                "type": "simple",
                "transition": {
                    "distributed": [
                        {"p": {"attribute": "risk", "default": 0.1}, "to": "a"},
                        {"p": {"attribute": "safe", "default": 0.9}, "to": "b"},
                    ]
                },
            },
            "a": {"type": "event", "event": "chose_a", "transition": {"direct": "end"}},
            "b": {"type": "event", "event": "chose_b", "transition": {"direct": "end"}},
            "end": END,
        },
        attributes={
            "risk": {"kind": "constant", "value": 0.25},
            "safe": {"kind": "constant", "value": 0.75},
        },
    )
    c = counts(run([m], size=n, seed=2), "kind")
    assert _within(c["chose_a"], n, 0.25), c


def test_conditional_transition_follows_the_attribute():
    m = module(
        {
            "s": start("pick"),
            "pick": {
                "type": "simple",
                "transition": {
                    "conditional": [
                        {
                            "if": {
                                "type": "attribute",
                                "attribute": "tier",
                                "op": "==",
                                "value": "gold",
                            },
                            "to": "g",
                        },
                        {"to": "o"},
                    ]
                },
            },
            "g": {"type": "event", "event": "gold_path", "transition": {"direct": "end"}},
            "o": {"type": "event", "event": "other_path", "transition": {"direct": "end"}},
            "end": END,
        },
        attributes={"tier": {"kind": "categorical", "values": {"gold": 0.2, "basic": 0.8}}},
    )
    n = 30_000
    t = run([m], size=n, seed=9)
    c = counts(t, "kind")
    assert _within(c["gold_path"], n, 0.2), c
    assert c["gold_path"] + c["other_path"] == n


def test_complex_transition_combines_guard_and_distribution():
    m = module(
        {
            "s": start("pick"),
            "pick": {
                "type": "simple",
                "transition": {
                    "complex": [
                        {
                            "if": {"type": "attribute", "attribute": "vip", "op": "==", "value": 1},
                            "distributed": [{"p": 0.7, "to": "a"}, {"p": 0.3, "to": "b"}],
                        },
                        {"to": "b"},
                    ]
                },
            },
            "a": {"type": "event", "event": "chose_a", "transition": {"direct": "end"}},
            "b": {"type": "event", "event": "chose_b", "transition": {"direct": "end"}},
            "end": END,
        },
        attributes={"vip": {"kind": "bernoulli", "p": 0.5}},
    )
    n = 60_000
    c = counts(run([m], size=n, seed=4), "kind")
    assert _within(c["chose_a"], n, 0.5 * 0.7), c

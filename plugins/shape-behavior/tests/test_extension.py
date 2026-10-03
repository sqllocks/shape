"""The extension point: a domain pack adds a state type and uses it from module documents."""

import numpy as np
import pytest
from helpers import END, T0, module, start
from shape_behavior import (
    Emission,
    Population,
    SimConfig,
    Simulator,
    StateHandler,
    register_state_type,
)
from shape_behavior.extension import _REGISTRY
from shape_behavior.model import ModuleError


class Claim:
    """Emit a claim with an amount derived from a deterministic draw."""

    def validate(self, state):
        return [] if "scale" in state else ["needs 'scale'"]

    def apply(self, ctx):
        return Emission(
            kind="claim_submitted",
            value=np.round(ctx.uniform(0) * ctx.state["scale"], 2),
            text=ctx.attribute("plan"),
        )


@pytest.fixture
def claim_registered():
    register_state_type("test_claim", Claim())
    yield
    _REGISTRY.pop("test_claim", None)


def _module():
    return module(
        {
            "s": start("c"),
            "c": {"type": "test_claim", "scale": 100, "transition": {"direct": "end"}},
            "end": END,
        },
        attributes={"plan": {"kind": "categorical", "values": {"a": 0.5, "b": 0.5}}},
    )


def test_custom_state_emits_its_event(claim_registered):
    sim = Simulator([_module()], Population(size=500, start=T0), SimConfig(seed=2))
    t = sim.run_until("2021-01-01")
    assert set(t.column("kind").to_pylist()) == {"claim_submitted"}
    values = np.array(t.column("value").to_pylist())
    assert 0 <= values.min() and values.max() <= 100 and abs(values.mean() - 50) < 6
    assert set(t.column("text").to_pylist()) == {"a", "b"}


def test_custom_state_is_deterministic(claim_registered):
    def go():
        return Simulator([_module()], Population(size=200, start=T0), SimConfig(seed=2)).run_until(
            "2021-01-01"
        )

    assert go().equals(go())


def test_unregistered_type_is_a_validation_error():
    with pytest.raises(ModuleError, match="test_claim"):
        _module()


def test_handler_problems_are_reported(claim_registered):
    with pytest.raises(ModuleError, match="needs 'scale'"):
        module(
            {
                "s": start("c"),
                "c": {"type": "test_claim", "transition": {"direct": "end"}},
                "end": END,
            }
        )


def test_builtin_types_cannot_be_replaced():
    with pytest.raises(ValueError, match="built-in"):
        register_state_type("delay", Claim())
    with pytest.raises(TypeError):
        register_state_type("bad", object())  # type: ignore[arg-type]


def test_protocol_is_runtime_checkable():
    assert isinstance(Claim(), StateHandler)

"""The extension point: new state types for domain packs (``docs/plugins/behavior.md``, section 5).

A handler is registered under a name and then usable in module documents as ``"type": name``::

    class ClaimState:
        def validate(self, state): return [] if "claim_type" in state else ["needs claim_type"]
        def apply(self, ctx):
            return Emission(kind="claim_submitted", value=ctx.attribute("allowed"))

    register_state_type("claim", ClaimState())

Handlers work on whole arrays (one element per entity entering the state at this moment), never
single rows, and must be deterministic given the context: draw random numbers only through
``ctx.uniform(k)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass
class Emission:
    """The event columns a state emits for its rows.

    Each field is ``None`` (null), a scalar (the same for every row) or an array with one
    element per row of the context. ``value`` is numeric; the others are text.
    """

    kind: Any = None
    code: Any = None
    system: Any = None
    display: Any = None
    ref: Any = None
    value: Any = None
    unit: Any = None
    text: Any = None
    payload: Any = None


@dataclass
class StateContext:
    """What a handler sees: the entities entering the state now, and the clock."""

    state: dict[str, Any]
    module: str
    rows: NDArray[np.intp]
    time: NDArray[np.int64]
    entity_id: NDArray[np.int64]
    _sim: Any = field(repr=False, default=None)
    _step: Any = field(repr=False, default=None)
    _module_index: int = field(repr=False, default=0)

    def attribute(self, name: str) -> Any:
        """The attribute's values for these rows: ``float64`` (NaN missing) for a numeric
        attribute, an object array of ``str`` (``None`` missing) for a categorical one."""
        return self._sim.store.read(name, self.rows)

    def set_attribute(self, name: str, values: Any) -> None:
        """Set the attribute for these rows (an array or a scalar)."""
        self._sim.store.write(name, self.rows, values)

    def uniform(self, k: int) -> NDArray[np.float64]:
        """The deterministic uniform draw number ``k`` (0 to 15) of each row at this step."""
        if not 0 <= k < 16:
            raise ValueError("draw index must be in 0..15")
        return self._sim.draw(self._module_index, self.rows, self._step, 16 + k)  # type: ignore[no-any-return]

    @property
    def age_years(self) -> NDArray[np.float64]:
        """Each entity's age in years at the entry time."""
        return self._sim.store.age_years(self.rows, self.time)  # type: ignore[no-any-return]


@runtime_checkable
class StateHandler(Protocol):
    """A state type added by a domain pack."""

    def validate(self, state: dict[str, Any]) -> list[str]:
        """Problems with a state document of this type; empty when it is fine."""
        ...

    def apply(self, ctx: StateContext) -> Emission | None:
        """Apply the state to ``ctx.rows`` (vectorized); return the event to emit, or ``None``."""
        ...


_REGISTRY: dict[str, StateHandler] = {}


def register_state_type(name: str, handler: StateHandler) -> None:
    """Make ``"type": name`` valid in module documents. Re-registering a name replaces it;
    a built-in type name cannot be replaced."""
    from shape_behavior.model import BUILTIN_TYPES

    if name in BUILTIN_TYPES:
        raise ValueError(f"{name!r} is a built-in state type")
    if not isinstance(handler, StateHandler):
        raise TypeError("a state handler needs validate(state) and apply(ctx)")
    _REGISTRY[name] = handler


def get_handler(name: str) -> StateHandler | None:
    return _REGISTRY.get(name)

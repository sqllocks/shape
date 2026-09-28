from dataclasses import dataclass

from shape.errors import ShapeCapabilityError


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    purpose: str
    steps: tuple[str, ...]
    degraded: tuple[str, ...] = ()


def compile_plan(
    purpose: str, capabilities: set[str], required: set[str] | None = None
) -> ExecutionPlan:
    required = required or set()
    missing = sorted(required - capabilities)
    if missing:
        raise ShapeCapabilityError(f"Missing capabilities: {', '.join(missing)}")
    defaults = {
        "generate": ("authorize", "resolve_scope", "generate", "validate_fidelity"),
        "validate": ("authorize", "profile", "evaluate_policy"),
        "stream": ("authorize", "consume", "profile", "checkpoint"),
    }
    if purpose not in defaults:
        raise ShapeCapabilityError(f"Unknown purpose: {purpose}")
    return ExecutionPlan(purpose, defaults[purpose])

from dataclasses import dataclass

SUPPORTED_MANDATORY = frozenset({"core/1", "sensitivity/1", "provenance/1"})


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    compatible: bool
    unknown_mandatory: tuple[str, ...]
    unknown_optional: tuple[str, ...]


def _names(doc, key):
    value = doc.get(key, ())
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"$.{key}: must be a list of strings, got {value!r}")
    return set(value)


def check_capabilities(doc, supported=SUPPORTED_MANDATORY):
    m = _names(doc, "mandatory_capabilities")
    o = _names(doc, "optional_capabilities")
    um = tuple(sorted(m - set(supported)))
    uo = tuple(sorted(o - set(supported)))
    return CapabilityCheck(not um, um, uo)

from dataclasses import dataclass

SUPPORTED_MANDATORY = frozenset({"core/1", "sensitivity/1", "provenance/1"})


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    compatible: bool
    unknown_mandatory: tuple[str, ...]
    unknown_optional: tuple[str, ...]


def check_capabilities(doc, supported=SUPPORTED_MANDATORY):
    m = set(doc.get("mandatory_capabilities", ()))
    o = set(doc.get("optional_capabilities", ()))
    um = tuple(sorted(m - set(supported)))
    uo = tuple(sorted(o - set(supported)))
    return CapabilityCheck(not um, um, uo)

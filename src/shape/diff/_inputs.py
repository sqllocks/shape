"""Input checks shared by the profile diff API and CLI."""

from collections.abc import Mapping
from typing import Any


def refuse_safe_profile(value: Any, source: object) -> None:
    """Refuse share-safe exports, whose schema is not a diffable capture."""
    from shape.privacy.safe_profile import SafeProfile

    if isinstance(value, SafeProfile) or (
        isinstance(value, Mapping) and value.get("format") == "shape-safe-profile"
    ):
        raise ValueError(
            f"{source} is a safe profile (shape-safe-profile) and cannot be diffed; "
            "diff the .shape captures instead"
        )

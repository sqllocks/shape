"""Built-in transforms (``shape.transforms``): ``mask`` replaces personal data with synthetic
values of the same format."""

from __future__ import annotations

from .mask import Mask, MaskConfig, MaskError, MaskResult, mask_tables

SHAPE_API = "1.0"

__all__ = ["SHAPE_API", "Mask", "MaskConfig", "MaskError", "MaskResult", "mask_tables"]

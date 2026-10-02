"""The pulse domain: a ride-hailing service (riders, drivers, vehicles and trips, 4 tables); its
values are all generated, so it ships no reference data."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class PulseDomain(PackagedDomain):
    """``shape.domains`` entry ``pulse``."""

    name = "pulse"

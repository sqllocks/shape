"""First-class geospatial primitives without mandatory GIS dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, radians, sin, sqrt


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float
    crs: str = "EPSG:4326"

    def __post_init__(self):
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude out of range")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude out of range")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    west: float
    south: float
    east: float
    north: float
    crs: str = "EPSG:4326"

    def contains(self, p: GeoPoint) -> bool:
        return self.west <= p.longitude <= self.east and self.south <= p.latitude <= self.north


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Return great-circle distance between two geographic points in kilometres."""
    r = 6371.0088
    p1, p2 = radians(a.latitude), radians(b.latitude)
    dp = radians(b.latitude - a.latitude)
    dl = radians(b.longitude - a.longitude)
    h = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * atan2(sqrt(h), sqrt(1 - h))

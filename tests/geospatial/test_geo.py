from shape.geospatial import BoundingBox, GeoPoint, haversine_km


def test_geo():
    a = GeoPoint(39.9612, -82.9988)
    b = GeoPoint(40.0, -83.0)
    assert 4 < haversine_km(a, b) < 5 and BoundingBox(-84, 38, -82, 41).contains(a)

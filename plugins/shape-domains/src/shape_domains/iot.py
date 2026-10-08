"""The iot domain: device types, locations, devices, sensors, readings, alerts, maintenance logs
and commands (8 tables), with the reference data it draws from (alert severity levels,
device types, sensor types; the ZIP locations are the retail domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class IotDomain(PackagedDomain):
    """``shape.domains`` entry ``iot``."""

    name = "iot"
    description = "IoT domain with devices, sensors, readings, alerts, and maintenance"
    datasets = ("alert_severity_levels", "device_types", "sensor_types")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}

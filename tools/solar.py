"""NOAA solar position: elevation/azimuth for a time and place, and the
light-band mapping used by the ingest pipeline.

Implements the NOAA General Solar Position Calculations (the NOAA solar
calculator spreadsheet equations). Elevation here is geometric (no
atmospheric refraction): the band thresholds in taxonomy/light_bands.json
use the standard -0.833 deg geometric sunrise/sunset boundary, which
already folds refraction and solar semi-diameter into the threshold.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from common import load_light_bands


def _julian_day(dt: datetime) -> float:
    """Julian day for a timezone-aware datetime."""
    dt = dt.astimezone(timezone.utc)
    year, month = dt.year, dt.month
    day = (dt.day
           + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24)
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1))
            + day + b - 1524.5)


def solar_position(lat: float, lon: float, dt: datetime) -> tuple[float, float]:
    """(elevation, azimuth) in degrees for a timezone-aware datetime.

    Elevation is geometric altitude above the horizon; azimuth is degrees
    clockwise from north.
    """
    if dt.tzinfo is None:
        raise ValueError("solar_position needs a timezone-aware datetime")

    jc = (_julian_day(dt) - 2451545.0) / 36525.0  # Julian century

    geom_mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    eccent = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    m = math.radians(geom_mean_anom)
    eq_of_center = (math.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
                    + math.sin(2 * m) * (0.019993 - 0.000101 * jc)
                    + math.sin(3 * m) * 0.000289)
    true_long = geom_mean_long + eq_of_center
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * jc))

    mean_obliq = (23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60)
    obliq_corr = mean_obliq + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * jc))

    declination = math.degrees(math.asin(
        math.sin(math.radians(obliq_corr)) * math.sin(math.radians(app_long))))

    var_y = math.tan(math.radians(obliq_corr / 2)) ** 2
    l0, e = math.radians(geom_mean_long), eccent
    eq_of_time = 4 * math.degrees(
        var_y * math.sin(2 * l0)
        - 2 * e * math.sin(m)
        + 4 * e * var_y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * var_y ** 2 * math.sin(4 * l0)
        - 1.25 * e ** 2 * math.sin(2 * m))

    utc = dt.astimezone(timezone.utc)
    minutes_of_day = utc.hour * 60 + utc.minute + utc.second / 60
    true_solar_time = (minutes_of_day + eq_of_time + 4 * lon) % 1440
    hour_angle = true_solar_time / 4 - 180 if true_solar_time / 4 >= 0 else true_solar_time / 4 + 180

    lat_r, decl_r, ha_r = map(math.radians, (lat, declination, hour_angle))
    zenith = math.degrees(math.acos(
        math.sin(lat_r) * math.sin(decl_r)
        + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r)))
    elevation = 90 - zenith

    az_denom = math.cos(lat_r) * math.sin(math.radians(zenith))
    if abs(az_denom) > 1e-9:
        az_cos = (math.sin(lat_r) * math.cos(math.radians(zenith)) - math.sin(decl_r)) / az_denom
        azimuth = math.degrees(math.acos(max(-1.0, min(1.0, az_cos))))
        azimuth = (180 + azimuth) % 360 if hour_angle > 0 else (180 - azimuth) % 360
    else:
        azimuth = 180.0 if lat > 0 else 0.0

    return elevation, azimuth


def band_for_elevation(elevation: float, bands: list[dict] | None = None) -> str:
    """Band id from taxonomy/light_bands.json for a solar elevation."""
    bands = bands or load_light_bands()["bands"]
    for band in bands:
        if band["min_elevation"] <= elevation < band["max_elevation"]:
            return band["id"]
    # 90 deg exactly falls out of the half-open intervals; clamp to the ends.
    return bands[-1]["id"] if elevation >= bands[-1]["min_elevation"] else bands[0]["id"]

"""Pure domain rules shared by the API and tests.

Time is UTC. A day is assigned as a UTC calendar day for this initial
implementation, not as a product decision about future local daily resets.
"""
from datetime import datetime, timezone
from math import asin, cos, isfinite, radians, sin, sqrt
from uuid import UUID

INACTIVITY_SECONDS = 180
MAX_DAILY_MISSIONS = 3
MISSION_DISCOVERY_RADIUS_M = 10_000  # Not the mission's start/proof radius
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def challenge_day(now: datetime):
    if now.tzinfo is None:
        raise ValueError("A timezone-aware instant is required")
    return now.astimezone(timezone.utc).date()


def is_session_expired(last_location_report: float, now: float) -> bool:
    """Session ends at the 180-second no-report boundary, inclusive."""
    return now - last_location_report >= INACTIVITY_SECONDS


def validate_coordinate(lat: float, lon: float, accuracy_m: float) -> None:
    if not all(isfinite(v) for v in (lat, lon, accuracy_m)):
        raise ValueError("Coordinates and accuracy must be finite")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordinates are outside WGS84 bounds")
    if accuracy_m < 0:
        raise ValueError("Accuracy cannot be negative")


def require_uuid(value: str) -> str:
    return str(UUID(value))


def mission_is_reachable(distance_m: float, radius_m: float, accuracy_m: float) -> bool:
    """Conservative geofence policy: avoid accepting uncertain locations."""
    return (
        isfinite(distance_m)
        and 0 <= distance_m <= radius_m
        and 0 <= accuracy_m <= radius_m
    )


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle estimate for plausibility only; geofence remains PostGIS."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    arc = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 2 * 6_371_000 * asin(min(1, sqrt(arc)))


def plausible_movement(previous: dict | None, current: dict, seconds: float) -> bool:
    """Basic server-time jump detection, not cryptographic GPS attestation.

    100 m/s plus 100 meters and both accuracy estimates allow GPS noise and
    rapid transit while rejecting large instant teleports.
    """
    if previous is None:
        return True
    if seconds <= 0:
        return False
    meters = distance_m(
        previous["lat"], previous["lon"], current["lat"], current["lon"]
    )
    uncertainty = previous["accuracy_m"] + current["accuracy_m"] + 100
    return meters <= 100 * seconds + uncertainty

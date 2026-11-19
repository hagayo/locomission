"""Pure domain rules shared by the API and tests.

Time is UTC. A day is assigned as a UTC calendar day for this initial
implementation, not as a product decision about future local daily resets.
"""
from datetime import datetime, timezone
from math import isfinite
from uuid import UUID

INACTIVITY_SECONDS = 180
MAX_DAILY_MISSIONS = 3
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

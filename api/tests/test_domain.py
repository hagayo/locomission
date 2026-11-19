from datetime import datetime, timedelta, timezone

import pytest

from locomission.domain import (
    challenge_day, is_session_expired, mission_is_reachable,
    require_uuid, validate_coordinate,
)


def test_idle_session_boundary():
    assert not is_session_expired(1000, 1179.999)
    assert is_session_expired(1000, 1180)
    assert is_session_expired(1000, 1181)


def test_utc_day_uses_utc_instant():
    local = datetime(2026, 11, 20, 1, 0, tzinfo=timezone(timedelta(hours=2)))
    assert str(challenge_day(local)) == "2026-11-19"


@pytest.mark.parametrize("lat,lon,acc", [
    (91, 34, 5), (-91, 34, 5), (32, 181, 5),
    (32, -181, 5), (32, 34, -1), (float("nan"), 34, 5),
])
def test_invalid_geo(lat, lon, acc):
    with pytest.raises(ValueError):
        validate_coordinate(lat, lon, acc)


def test_geofence_is_conservative():
    assert mission_is_reachable(90, 100, 8)
    assert not mission_is_reachable(101, 100, 8)
    assert not mission_is_reachable(90, 100, 150)


def test_uuid_is_canonical():
    assert require_uuid("B9B7661E-ABF2-40DD-8EE7-81C3A438D7C5") == \
           "b9b7661e-abf2-40dd-8ee7-81c3a438d7c5"

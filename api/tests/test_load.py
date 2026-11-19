"""Measured load smoke against REAL Redis; no OpenAI or real user data."""
from __future__ import annotations

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("LOCOMISSION_TEST_DATABASE_URL"),
    reason="Load smoke requires explicitly configured isolated CI services",
)


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * p)))
    return ordered[rank]


def test_redis_presence_load_and_cleanup(capsys):
    """80 concurrent players + 600 reads; correctness, metrics, zero residue."""
    import redis
    from locomission.infra import Sessions

    cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    sessions = Sessions(cache)
    users = [str(uuid4()) for _ in range(80)]
    tokens = []
    samples = []

    def record(function, *args):
        begin = time.perf_counter()
        result = function(*args)
        samples.append((time.perf_counter() - begin) * 1000)
        return result

    begin = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=16) as executor:
            tokens = list(executor.map(lambda pid: record(sessions.create, pid), users))
            reported = list(executor.map(
                lambda token: record(sessions.report, token, 32.0853, 34.7818, 12.0),
                tokens,
            ))
            assert all(row and row["location"] for row in reported)
            # Verify bursts of reads and presence aggregation on the same cache.
            checked = list(executor.map(
                lambda i: record(sessions.read, tokens[i % len(tokens)]),
                range(600),
            ))
            assert all(value is not None for value in checked)
            viewer = {"lat": 32.0853, "lon": 34.7818, "accuracy_m": 12}
            maps = list(executor.map(
                lambda _: record(sessions.active_presence, tokens[0], viewer),
                range(24),
            ))
            # The observing player sees the other 79 players, never itself.
            assert all(any(cell["players"] >= 79 for cell in m["cells"]) for m in maps)
        seconds = time.perf_counter() - begin
        print(
            "LOAD SMOKE redis", "requests", len(samples),
            "concurrency", 16, "rps", round(len(samples) / seconds, 1),
            "p50_ms", round(statistics.median(samples), 2),
            "p95_ms", round(percentile(samples, .95), 2),
            "p99_ms", round(percentile(samples, .99), 2),
        )
    finally:
        for token in tokens:
            sessions.end(token)
        cache.close()

    # The sessions do not persist player locations after explicit exit.
    check = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        assert all(check.get(sessions.player_key(user)) is None for user in users)
        assert all(
            check.get(sessions.session_key(sessions.digest(token))) is None
            for token in tokens
        )
    finally:
        check.close()


def test_single_flight_race_expiry_and_result_cleanup():
    """A crashed worker's stale lease cannot monopolize a live session."""
    import redis
    from locomission.infra import Sessions

    cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    sessions = Sessions(cache)
    sessions.VERIFY_LEASE_MS = 700  # Test-only lease, not a retention SLA.
    player_id = str(uuid4())
    token = sessions.create(player_id)
    mission_id = str(uuid4())
    attempts = [(str(uuid4()), uuid4().hex) for _ in range(32)]
    try:
        sessions.report(token, 32.0853, 34.7818, 12.0)
        with ThreadPoolExecutor(max_workers=16) as executor:
            claims = list(executor.map(
                lambda pair: sessions.claim_verification(
                    token, player_id, mission_id, pair[0], pair[1]
                ), attempts,
            ))
        assert sum(claims) == 1
        winning = attempts[claims.index(True)]
        assert sessions.verification_owner(token, mission_id) == winning[0]
        # A different owner must not release a valid lease.
        sessions.release_verification(token, mission_id, *next(pair for pair in attempts if pair != winning))
        assert sessions.verification_owner(token, mission_id) == winning[0]

        # Worker disappears with no finally: lease expires independently
        # of the 180-second heartbeat session.
        time.sleep(.85)
        next_id, next_lease = str(uuid4()), uuid4().hex
        assert sessions.verification_owner(token, mission_id) is None
        assert sessions.claim_verification(
            token, player_id, mission_id, next_id, next_lease,
        )
        sessions.release_verification(token, mission_id, *winning)
        assert sessions.verification_owner(token, mission_id) == next_id

        # The same logical request may replay an image after a worker loss,
        # but may not reuse that image for another mission.
        same_bytes = b"test-image-fingerprint-no-photo-stored"
        assert sessions.claim_photo(
            token, same_bytes, mission_id + ":" + next_id,
        )[0] is True
        assert sessions.claim_photo(
            token, same_bytes, mission_id + ":" + next_id,
        )[0] is True
        assert sessions.claim_photo(
            token, same_bytes, str(uuid4()) + ":" + next_id,
        )[0] is False

        outcome = {
            "accepted": False, "awarded": False, "points": 0,
            "reason_he": "בדיקת עומס",
        }
        assert sessions.cache_proof_result(
            token, player_id, mission_id, next_id, outcome,
        )
        assert sessions.get_proof_result(token, mission_id, next_id) == outcome
        digest = sessions.digest(token)
        ttl = cache.eval(
            "return {redis.call('PTTL', KEYS[1]), redis.call('PTTL', KEYS[2])}",
            2, sessions.result_key(digest), sessions.session_key(digest),
        )
        assert 0 < ttl[0] <= ttl[1]
        sessions.end(token)
        assert cache.exists(sessions.result_key(digest)) == 0
        assert cache.exists(sessions.verification_key(digest)) == 0
        assert sessions.get_proof_result(token, mission_id, next_id) is None
    finally:
        sessions.end(token)
        cache.close()


def test_redis_failure_never_returns_lease(monkeypatch):
    import redis
    from locomission.infra import Sessions

    cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    sessions = Sessions(cache)
    player = str(uuid4())
    token = sessions.create(player)
    try:
        def offline(*_args, **_kwargs):
            raise redis.ConnectionError("simulated network failure")
        monkeypatch.setattr(cache, "eval", offline)
        with pytest.raises(redis.ConnectionError):
            sessions.claim_verification(token, player, str(uuid4()), str(uuid4()))
    finally:
        monkeypatch.undo()
        sessions.end(token)
        cache.close()


def test_local_geo_presence_no_global_scan_and_cleanup(monkeypatch):
    """Only nearby players appear; no stale identity/coordinate is returned."""
    import redis
    from locomission.infra import Sessions

    cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    sessions = Sessions(cache)
    identities = [str(uuid4()) for _ in range(4)]
    viewer, near, far, replacement = [sessions.create(i) for i in identities]
    center = {"lat": 32.0853, "lon": 34.7818, "accuracy_m": 10}
    try:
        assert sessions.report(viewer, **center)
        assert sessions.report(near, 32.0857, 34.7818, 10)
        assert sessions.report(far, 32.13, 34.7818, 10)
        # Presence must not traverse all Redis keys or scan all session keys.
        def forbidden_scan(*_args, **_kwargs):
            raise AssertionError("Global Redis SCAN is prohibited for presence")

        monkeypatch.setattr(cache, "scan_iter", forbidden_scan)
        visible = sessions.active_presence(viewer, center)
        assert visible["truncated"] is False
        assert visible["cells"] == [
            {"lat": 32.086, "lon": 34.782, "players": 1}
        ]
        digest = sessions.digest(near)
        bucket = sessions.read(near)["presence_bucket"]
        geo_key = sessions.presence_key(bucket)
        assert cache.zscore(geo_key, digest) is not None
        assert 0 < cache.ttl(geo_key) <= sessions.PRESENCE_INDEX_TTL_SECONDS

        # Same player opens a new session: old map membership is removed.
        replacement_near = sessions.create(identities[1])
        try:
            assert sessions.read(near) is None
            assert cache.zscore(geo_key, digest) is None
            assert sessions.active_presence(viewer, center)["cells"] == []
            assert sessions.report(replacement_near, 32.0858, 34.7818, 10)
            assert len(sessions.active_presence(viewer, center)["cells"]) == 1
            fingerprint = sessions.digest(replacement_near)
            new_bucket = sessions.read(replacement_near)["presence_bucket"]
            sessions.end(replacement_near)
            assert cache.zscore(
                sessions.presence_key(new_bucket), fingerprint
            ) is None
            assert sessions.active_presence(viewer, center)["cells"] == []
        finally:
            sessions.end(replacement_near)

        # Expired SESSIONs remain invisible even before their bucket TTL.
        # The bucket itself always has a finite TTL (no permanent GEO keys).
        stale = sessions.create(identities[3])
        try:
            assert sessions.report(stale, 32.0858, 34.7818, 10)
            key = sessions.session_key(sessions.digest(stale))
            cache.expire(key, 1)
            import time
            time.sleep(1.1)
            assert sessions.active_presence(viewer, center)["cells"] == []
        finally:
            sessions.end(stale)
    finally:
        for token in (viewer, near, far, replacement):
            sessions.end(token)
        cache.close()

"""Rate enforcement against isolated Redis, including races and retries."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


def test_no_partial_global_spend_under_concurrency():
    import redis
    from locomission.limits import RateLimiter

    cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    limiter = RateLimiter(cache)
    action = "test-" + uuid4().hex
    user_a = str(uuid4())
    user_b = str(uuid4())
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            decisions = list(pool.map(
                lambda i: limiter.spend(
                    user_a if i % 2 else user_b, action,
                    max_per_player=5, global_limit=7, window_seconds=60,
                ), range(40),
            ))
        assert sum(d.allowed for d in decisions) == 7
        assert all(d.retry_after > 0 for d in decisions if not d.allowed)
        first = int(cache.get(limiter.player_key(user_a, action)) or 0)
        second = int(cache.get(limiter.player_key(user_b, action)) or 0)
        assert first + second == 7
        assert first <= 5 and second <= 5
        assert int(cache.get(limiter.global_key(action))) == 7
        assert 0 < cache.ttl(limiter.global_key(action)) <= 60
        assert not cache.exists(limiter.player_key("someone-else", action))
    finally:
        cache.delete(
            limiter.player_key(user_a, action),
            limiter.player_key(user_b, action), limiter.global_key(action),
        )
        cache.close()


def test_per_player_limit_and_invalid_config():
    import redis
    from locomission.limits import RateLimiter

    cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    limiter = RateLimiter(cache)
    player = str(uuid4())
    action = "test-" + uuid4().hex
    try:
        assert limiter.spend(
            player, action, max_per_player=2, window_seconds=30,
        ).allowed
        assert limiter.spend(
            player, action, max_per_player=2, window_seconds=30,
        ).allowed
        denied = limiter.spend(
            player, action, max_per_player=2, window_seconds=30,
        )
        assert not denied.allowed and denied.retry_after > 0
        assert player not in limiter.player_key(player, action)
        with pytest.raises(ValueError):
            limiter.spend(player, action, max_per_player=0, window_seconds=30)
    finally:
        cache.delete(limiter.player_key(player, action), limiter.global_key(action))
        cache.close()

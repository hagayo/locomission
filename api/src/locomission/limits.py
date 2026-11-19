"""Atomic, shared Redis request budgets for authenticated gameplay.

The window starts with the first accepted operation and has a finite TTL.
Keys hold only a hash of the durable internal player ID and numeric counters;
never GPS, photo bytes, HTTP tokens, or image-derived fingerprints.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from redis import Redis


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after: int = 0


class RateLimiter:
    def __init__(self, cache: Redis):
        self.cache = cache

    @staticmethod
    def player_key(player_id: str, action: str) -> str:
        digest = hashlib.sha256(player_id.encode("utf-8")).hexdigest()
        return f"lm:budget:{action}:{digest}"

    @staticmethod
    def global_key(action: str) -> str:
        return f"lm:budget:global:{action}"

    def spend(self, player_id: str, action: str, *,
              max_per_player: int, window_seconds: int,
              global_limit: int | None = None) -> RateDecision:
        """Test and increment player/global budgets in one Redis Lua script.

        On a denial neither counter is incremented. On an actual model
        attempt, spending is conservative: upstream failure can still bill.
        """
        if max_per_player < 1 or window_seconds < 1:
            raise ValueError("Invalid rate limit")
        global_cap = global_limit if global_limit is not None else 2**31 - 1
        if global_cap < 1:
            raise ValueError("Invalid global rate limit")
        outcome = self.cache.eval(
            """
            local player = tonumber(redis.call('GET', KEYS[1]) or '0')
            local global = tonumber(redis.call('GET', KEYS[2]) or '0')
            if player >= tonumber(ARGV[1]) then
                return {0, math.max(1, redis.call('TTL', KEYS[1]))}
            end
            if global >= tonumber(ARGV[2]) then
                return {0, math.max(1, redis.call('TTL', KEYS[2]))}
            end
            if player == 0 then
                redis.call('SET', KEYS[1], 1, 'EX', ARGV[3])
            else
                redis.call('INCR', KEYS[1])
            end
            if global == 0 then
                redis.call('SET', KEYS[2], 1, 'EX', ARGV[3])
            else
                redis.call('INCR', KEYS[2])
            end
            return {1, 0}
            """,
            2, self.player_key(player_id, action),
            self.global_key(action),
            max_per_player, global_cap, window_seconds,
        )
        return RateDecision(allowed=outcome[0] == 1, retry_after=outcome[1])

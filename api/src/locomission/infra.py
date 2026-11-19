"""Storage adapters. PostgreSQL is durable; all live player locations stay in Redis."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from redis import Redis

from .domain import INACTIVITY_SECONDS, is_session_expired, plausible_movement, validate_coordinate


class SessionExpired(RuntimeError):
    pass


class SuspiciousLocation(ValueError):
    pass


class PlayerMustConfirmAdult(ValueError):
    pass


class Sessions:
    """Bearer token never appears in the database or in Redis in plaintext."""

    def __init__(self, redis: Redis):
        self.redis = redis

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def session_key(digest: str) -> str:
        return "lm:session:" + digest

    @staticmethod
    def player_key(player_id: str) -> str:
        return "lm:player-session:" + player_id

    @staticmethod
    def proof_key(digest: str) -> str:
        return "lm:proof-fingerprints:" + digest

    @staticmethod
    def verification_key(digest: str) -> str:
        return "lm:proof-inflight:" + digest

    @staticmethod
    def result_key(digest: str) -> str:
        return "lm:proof-outcomes:" + digest

    @staticmethod
    def result_field(mission_id: str, request_id: str) -> str:
        return mission_id + ":" + request_id

    # Independent of session inactivity; a crashed worker cannot retain
    # a mission-wide lock indefinitely while GPS refreshes the session.
    VERIFY_LEASE_MS = 90_000

    def create(self, player_id: str) -> str:
        """Replace an existing gameplay session in one Redis transaction.

        A concurrent logout or location report cannot resurrect the prior
        session after a newer session has been created.
        """
        token = secrets.token_urlsafe(32)
        fingerprint = self.digest(token)
        data = {
            "session_id": str(uuid4()),
            "player_id": player_id,
            "last_report": time.time(),
            "location": None,
        }
        self.redis.eval(
            """
            local old = redis.call('GET', KEYS[1])
            if old then
                redis.call('DEL', ARGV[3] .. old, ARGV[4] .. old, ARGV[5] .. old, ARGV[6] .. old)
            end
            redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[7])
            redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[7])
            return 1
            """,
            2, self.player_key(player_id), self.session_key(fingerprint),
            fingerprint, json.dumps(data), "lm:session:",
            "lm:proof-fingerprints:", "lm:proof-inflight:",
            "lm:proof-outcomes:", INACTIVITY_SECONDS,
        )
        return token

    def read(self, token: str) -> dict[str, Any] | None:
        """No session is valid without its owner index and live report TTL."""
        fingerprint = self.digest(token)
        raw = self.redis.get(self.session_key(fingerprint))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if (
                is_session_expired(data["last_report"], time.time())
                or self.redis.get(self.player_key(data["player_id"])) != fingerprint
            ):
                self.end(token)
                return None
            return data
        except (ValueError, TypeError, KeyError):
            self.end(token)
            return None

    def report(self, token: str, lat: float, lon: float,
               accuracy_m: float) -> dict[str, Any] | None:
        """Compare-and-set prevents races with logout, replacement and GPS."""
        validate_coordinate(lat, lon, accuracy_m)
        fingerprint = self.digest(token)
        key = self.session_key(fingerprint)
        raw = self.redis.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            instant = time.time()
            if is_session_expired(data["last_report"], instant):
                self.end(token)
                return None
            new_location = {"lat": lat, "lon": lon, "accuracy_m": accuracy_m}
            if not plausible_movement(
                data["location"], new_location, instant - data["last_report"]
            ):
                raise SuspiciousLocation("Implausible location jump")
            data["last_report"] = instant
            data["location"] = new_location
            # All three changes are atomic. No create-after-logout gap.
            result = self.redis.eval(
                """
                local old = redis.call('GET', KEYS[1])
                if not old or old ~= ARGV[1] then return 0 end
                if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
                redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[4])
                redis.call('EXPIRE', KEYS[2], ARGV[4])
                if redis.call('EXISTS', KEYS[3]) == 1 then
                    redis.call('EXPIRE', KEYS[3], ARGV[4])
                end
                if redis.call('EXISTS', KEYS[4]) == 1 then
                    redis.call('EXPIRE', KEYS[4], ARGV[4])
                end
                if redis.call('EXISTS', KEYS[5]) == 1 then
                    redis.call('EXPIRE', KEYS[5], ARGV[4])
                end
                return 1
                """,
                5, key, self.player_key(data["player_id"]),
                self.proof_key(fingerprint), self.verification_key(fingerprint),
                self.result_key(fingerprint),
                raw, json.dumps(data), fingerprint, INACTIVITY_SECONDS,
            )
            return data if result == 1 else None
        except SuspiciousLocation:
            raise
        except (ValueError, TypeError, KeyError):
            return None

    def claim_photo(self, token: str, photo_bytes: bytes,
                    request_id: str = "") -> tuple[bool, str]:
        """Atomic, session-scoped replay hash, with no stored image bytes."""
        data = self.read(token)
        if data is None:
            raise SessionExpired("Session ended before photo claim")
        fingerprint = self.digest(token)
        photo_hash = hashlib.sha256(photo_bytes).hexdigest()
        added = self.redis.eval(
            """
            local ttl = redis.call('PTTL', KEYS[1])
            if ttl <= 0 then return -1 end
            if redis.call('GET', KEYS[2]) ~= ARGV[2] then return -1 end
            local existing = redis.call('HGET', KEYS[3], ARGV[1])
            if existing and existing ~= ARGV[3] then return 0 end
            if not existing then redis.call('HSET', KEYS[3], ARGV[1], ARGV[3]) end
            redis.call('PEXPIRE', KEYS[3], ttl)
            return 1
            """,
            3, self.session_key(fingerprint),
            self.player_key(data["player_id"]),
            self.proof_key(fingerprint),
            photo_hash, fingerprint, request_id,
        )
        if added == -1:
            raise SessionExpired("Session ended before photo claim")
        return bool(added), photo_hash

    def forget_photo(self, token: str, photo_hash: str) -> None:
        self.redis.hdel(self.proof_key(self.digest(token)), photo_hash)

    def claim_verification(self, token: str, player_id: str,
                           mission_id: str, request_id: str,
                           lease_id: str | None = None) -> bool:
        """Acquire a bounded mission lease with atomic Redis server time.

        The lease expires after 90 seconds even while GPS refreshes the
        session. It never extends past the session itself. PostgreSQL's
        unique completion remains the final exactly-once scoring guard.
        """
        digest = self.digest(token)
        lease_owner = request_id + ":" + (lease_id or request_id)
        result = self.redis.eval(
            """
            local remaining = redis.call('PTTL', KEYS[1])
            if remaining <= 0 then return -1 end
            if redis.call('GET', KEYS[2]) ~= ARGV[3] then return -1 end
            local clock = redis.call('TIME')
            local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
            local old = redis.call('HGET', KEYS[3], ARGV[1])
            if old then
                local sep = string.find(old, '|', 1, true)
                if sep then
                    local expires = tonumber(string.sub(old, sep + 1))
                    if expires and expires > now then return 0 end
                end
            end
            local until_ms = now + math.min(remaining, tonumber(ARGV[4]))
            redis.call('HSET', KEYS[3], ARGV[1],
                       ARGV[2] .. '|' .. tostring(until_ms))
            redis.call('PEXPIRE', KEYS[3], remaining)
            return 1
            """,
            3, self.session_key(digest), self.player_key(player_id),
            self.verification_key(digest),
            mission_id, lease_owner, digest, self.VERIFY_LEASE_MS,
        )
        if result == -1:
            raise SessionExpired("Session ended before verification")
        return result == 1

    def verification_owner(self, token: str, mission_id: str) -> str | None:
        """Return active owner UUID, or none for a stale/abandoned worker."""
        digest = self.digest(token)
        result = self.redis.eval(
            """
            if redis.call('PTTL', KEYS[1]) <= 0 then return false end
            local old = redis.call('HGET', KEYS[2], ARGV[1])
            if not old then return false end
            local sep = string.find(old, '|', 1, true)
            if not sep then return false end
            local clock = redis.call('TIME')
            local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
            local expiry = tonumber(string.sub(old, sep + 1))
            if not expiry or expiry <= now then return false end
            local owner = string.sub(old, 1, sep - 1)
            local divider = string.find(owner, ':', 1, true)
            if not divider then return false end
            return string.sub(owner, 1, divider - 1)
            """,
            2, self.session_key(digest), self.verification_key(digest),
            mission_id,
        )
        return result if isinstance(result, str) else None

    def release_verification(self, token: str, mission_id: str,
                             request_id: str,
                             lease_id: str | None = None) -> None:
        digest = self.digest(token)
        lease_owner = request_id + ":" + (lease_id or request_id)
        self.redis.eval(
            """
            local old = redis.call('HGET', KEYS[1], ARGV[1])
            if not old then return 0 end
            local sep = string.find(old, '|', 1, true)
            if sep and string.sub(old, 1, sep - 1) == ARGV[2] then
                redis.call('HDEL', KEYS[1], ARGV[1])
                return 1
            end
            return 0
            """,
            1, self.verification_key(digest), mission_id, lease_owner,
        )

    def get_proof_result(self, token: str, mission_id: str,
                         request_id: str) -> dict[str, Any] | None:
        if self.read(token) is None:
            return None
        raw = self.redis.hget(
            self.result_key(self.digest(token)),
            self.result_field(mission_id, request_id),
        )
        return json.loads(raw) if raw else None

    def cache_proof_result(self, token: str, player_id: str,
                           mission_id: str, request_id: str,
                           result: dict[str, Any]) -> bool:
        """Store only a small verdict; never image data or coordinates.

        The cache cannot outlive the authenticated gameplay session.
        """
        digest = self.digest(token)
        saved = self.redis.eval(
            """
            local remaining = redis.call('PTTL', KEYS[1])
            if remaining <= 0 then return 0 end
            if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
            redis.call('HSET', KEYS[3], ARGV[1], ARGV[2])
            redis.call('PEXPIRE', KEYS[3], remaining)
            return 1
            """,
            3, self.session_key(digest), self.player_key(player_id),
            self.result_key(digest),
            self.result_field(mission_id, request_id),
            json.dumps(result, ensure_ascii=False),
            digest,
        )
        return saved == 1

    def active_presence(self, limit: int = 500) -> dict:
        """Limited pilot map, batching Redis reads instead of two GETs per user.

        Each marker is an approximate cell with no player identifiers.
        No long-lived Redis index is created, so expired sessions disappear.
        """
        from itertools import islice

        cells: dict[tuple[float, float], int] = {}
        key_iter = self.redis.scan_iter(match="lm:session:*", count=200)
        scanned = 0
        exhausted = False
        limit = max(1, min(limit, 500))
        while scanned < 10_000:
            keys = list(islice(key_iter, min(200, 10_000 - scanned)))
            if not keys:
                exhausted = True
                break
            scanned += len(keys)
            raws = self.redis.mget(keys)
            candidates = []
            now = time.time()
            for key, raw in zip(keys, raws):
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    if is_session_expired(data["last_report"], now):
                        continue
                    loc = data["location"]
                    if loc is None:
                        continue
                    candidates.append((
                        key.removeprefix("lm:session:"),
                        data["player_id"], loc,
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            if not candidates:
                continue
            owner_keys = [self.player_key(pid) for _, pid, _ in candidates]
            owners = self.redis.mget(owner_keys)
            for (fingerprint, _pid, loc), owner in zip(candidates, owners):
                if owner != fingerprint:
                    continue
                cell = (round(loc["lat"], 3), round(loc["lon"], 3))
                cells[cell] = cells.get(cell, 0) + 1
        values = [
            {"lat": lat, "lon": lon, "players": count}
            for (lat, lon), count in cells.items()
        ]
        return {"cells": values[:limit],
                "truncated": len(values) > limit or not exhausted}

    def end(self, token: str) -> None:
        """Delete session + proof hash and only the matching owner pointer."""
        fingerprint = self.digest(token)
        raw = self.redis.get(self.session_key(fingerprint))
        if not raw:
            self.redis.delete(
                self.proof_key(fingerprint), self.verification_key(fingerprint),
                self.result_key(fingerprint),
            )
            return
        try:
            player_id = json.loads(raw)["player_id"]
        except (ValueError, TypeError, KeyError):
            self.redis.delete(
                self.session_key(fingerprint), self.proof_key(fingerprint),
                self.verification_key(fingerprint), self.result_key(fingerprint),
            )
            return
        self.redis.eval(
            """
            redis.call('DEL', KEYS[1], KEYS[3], KEYS[4], KEYS[5])
            if redis.call('GET', KEYS[2]) == ARGV[1] then
                redis.call('DEL', KEYS[2])
            end
            return 1
            """,
            5, self.session_key(fingerprint),
            self.player_key(player_id), self.proof_key(fingerprint),
            self.verification_key(fingerprint), self.result_key(fingerprint),
            fingerprint,
        )



class GameStore:
    def __init__(self, database_url: str):
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("SELECT 1")

    def ensure_dev_player(self, player_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO players(id,display_name) VALUES (%s,'Local test player') "
                "ON CONFLICT (id) DO NOTHING",
                (UUID(player_id),),
            )

    def player_for_clerk(self, clerk_id: str, adult_confirmed: bool) -> str:
        """Create exactly one durable player per signed Clerk account.

        The caller is responsible for JWT verification. This method must
        never accept a client-provided user identifier.
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO players(clerk_user_id,display_name,adult_confirmed_at) "
                    "VALUES (%s,'שחקן חדש',CASE WHEN %s THEN now() ELSE NULL END) "
                    "ON CONFLICT (clerk_user_id) DO UPDATE SET "
                    "adult_confirmed_at=COALESCE(players.adult_confirmed_at, "
                    "CASE WHEN %s THEN now() ELSE NULL END) "
                    "RETURNING id::text AS id, adult_confirmed_at",
                    (clerk_id, adult_confirmed, adult_confirmed),
                ).fetchone()
                if row["adult_confirmed_at"] is None:
                    raise PlayerMustConfirmAdult()
                return row["id"]

    def player_id_for_clerk(self, clerk_id: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT id::text AS id FROM players "
                "WHERE clerk_user_id=%s AND adult_confirmed_at IS NOT NULL",
                (clerk_id,),
            ).fetchone()
            return row["id"] if row else None

    def set_player_name(self, player_id: str, name: str) -> dict[str, Any]:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE players SET display_name=%s WHERE id=%s",
                (name, UUID(player_id)),
            )
        return self.profile(player_id)

    def leaderboard(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT display_name,total_points,missions_started,"
                "completed_missions FROM players "
                "WHERE adult_confirmed_at IS NOT NULL OR clerk_user_id IS NULL "
                "ORDER BY total_points DESC,completed_missions DESC,id LIMIT %s",
                (limit,),
            ).fetchall()

    def mission_started(self, player_id: str, mission_id: UUID,
                        day: date) -> bool:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM mission_starts WHERE player_id=%s "
                "AND mission_id=%s AND challenge_day=%s",
                (UUID(player_id), mission_id, day),
            ).fetchone() is not None

    def start_mission(self, player_id: str, mission_id: UUID, day: date) -> bool:
        """One durable participation increment per player/mission/day."""
        with self.pool.connection() as conn:
            with conn.transaction():
                created = conn.execute(
                    "INSERT INTO mission_starts(player_id,mission_id,challenge_day) "
                    "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING player_id",
                    (UUID(player_id), mission_id, day),
                ).fetchone()
                if created:
                    conn.execute(
                        "UPDATE players SET missions_started=missions_started+1 "
                        "WHERE id=%s", (UUID(player_id),),
                    )
                return bool(created)

    def profile(self, player_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT id::text AS id, display_name,total_points,completed_missions, "
                "missions_started,credits_balance,plan_code,avatar_url,settings "
                "FROM players WHERE id=%s",
                (UUID(player_id),),
            ).fetchone()

    @staticmethod
    def _mission_columns() -> str:
        return (
            "m.id::text AS id, m.slug, m.title_he, m.historical_fact_he, "
            "m.instructions_he, m.visual_criteria_he, m.source_title, "
            "m.source_url, m.radius_m, m.points, "
            "ST_Y(m.location::geometry) AS lat, "
            "ST_X(m.location::geometry) AS lon"
        )

    def today(self, player_id: str, day: date,
              location: dict[str, float]) -> list[dict[str, Any]]:
        """Allocate three unique slots atomically, stable for the UTC day."""
        player = UUID(player_id)
        with self.pool.connection() as conn:
            with conn.transaction():
                # Hash collision only causes harmless extra serialization.
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                    (player_id + day.isoformat(),),
                )
                rows = conn.execute(
                    "SELECT mission_id FROM daily_assignments "
                    "WHERE player_id=%s AND challenge_day=%s",
                    (player, day),
                ).fetchall()
                if not rows:
                    candidates = conn.execute(
                        "SELECT m.id FROM missions m "
                        "WHERE m.status='approved' AND m.content_tier='free' "
                        "AND ST_DWithin(m.location, "
                        "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,10000) "
                        "AND ST_DWithin(m.location, "
                        "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,m.radius_m) "
                        "AND %s <= m.radius_m "
                        "AND NOT EXISTS (SELECT 1 FROM completions c "
                        "WHERE c.player_id=%s AND c.mission_id=m.id) "
                        "ORDER BY md5(m.id::text || %s || %s) LIMIT 3",
                        (
                            location["lon"], location["lat"],
                            location["lon"], location["lat"],
                            location["accuracy_m"], player,
                            player_id, day.isoformat(),
                        ),
                    ).fetchall()
                    for slot, row in enumerate(candidates, start=1):
                        conn.execute(
                            "INSERT INTO daily_assignments "
                            "(player_id,challenge_day,slot,mission_id) "
                            "VALUES (%s,%s,%s,%s)",
                            (player, day, slot, row["id"]),
                        )
                return conn.execute(
                    "SELECT " + self._mission_columns() + ", a.slot, "
                    "EXISTS (SELECT 1 FROM completions c WHERE c.player_id=a.player_id "
                    "AND c.mission_id=a.mission_id AND c.challenge_day=a.challenge_day) "
                    "AS completed, "
                    "EXISTS (SELECT 1 FROM mission_starts ms "
                    "WHERE ms.player_id=a.player_id AND ms.mission_id=a.mission_id "
                    "AND ms.challenge_day=a.challenge_day) AS started_today "
                    "FROM daily_assignments a "
                    "JOIN missions m ON m.id=a.mission_id "
                    "WHERE a.player_id=%s AND a.challenge_day=%s "
                    "ORDER BY a.slot",
                    (player, day),
                ).fetchall()

    def proof_mission(self, player_id: str, mission_id: UUID,
                      day: date, location: dict[str, float]) -> dict[str, Any] | None:
        """An assigned solo mission OR a joined active group mission."""
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT m.id::text AS id, m.title_he, m.instructions_he, "
                "m.visual_criteria_he, m.points, m.radius_m, "
                "ST_Distance(m.location, "
                "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) AS distance_m "
                "FROM missions m "
                "WHERE m.id=%s AND m.status='approved' AND m.content_tier='free' "
                "AND ("
                "EXISTS (SELECT 1 FROM daily_assignments a "
                "WHERE a.player_id=%s AND a.challenge_day=%s "
                "AND a.mission_id=m.id) "
                "OR EXISTS (SELECT 1 FROM group_missions gm "
                "JOIN group_members mb ON mb.challenge_id=gm.challenge_id "
                "JOIN group_challenges g ON g.id=gm.challenge_id "
                "WHERE gm.mission_id=m.id AND mb.player_id=%s "
                "AND g.status='published' "
                "AND now() BETWEEN g.opens_at AND g.closes_at)"
                ")",
                (
                    location["lon"], location["lat"], mission_id,
                    UUID(player_id), day, UUID(player_id),
                ),
            ).fetchone()

    def completed(self, player_id: str, mission_id: UUID, day: date) -> bool:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM completions WHERE player_id=%s "
                "AND mission_id=%s AND challenge_day=%s",
                (UUID(player_id), mission_id, day),
            ).fetchone() is not None

    def awarded_for_request(self, player_id: str, mission_id: UUID,
                            request_id: UUID) -> dict[str, Any] | None:
        """Recover a committed score after the HTTP response was lost.

        Deliberately binds the request UUID to the same player and mission;
        returns neither another player's results nor any proof image.
        """
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT points_awarded FROM completions "
                "WHERE player_id=%s AND mission_id=%s AND request_id=%s",
                (UUID(player_id), mission_id, request_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "accepted": True,
                "awarded": True,
                "points": row["points_awarded"],
                "reason_he": "המשימה הושלמה והניקוד נשמר",
            }

    def award(self, player_id: str, mission_id: UUID,
              day: date, request_id: UUID) -> dict[str, Any]:
        """Idempotent credit even if two validated requests race."""
        with self.pool.connection() as conn:
            with conn.transaction():
                awarded = conn.execute(
                    "INSERT INTO completions "
                    "(player_id,mission_id,challenge_day,request_id,points_awarded) "
                    "SELECT %s,m.id,%s,%s,m.points FROM missions m "
                    "WHERE m.id=%s AND m.status='approved' "
                    "ON CONFLICT DO NOTHING "
                    "RETURNING id,points_awarded",
                    (UUID(player_id), day, request_id, mission_id),
                ).fetchone()
                if not awarded:
                    return {"awarded": False, "points": 0}
                conn.execute(
                    "UPDATE players SET total_points=total_points+%s, "
                    "completed_missions=completed_missions+1 WHERE id=%s",
                    (awarded["points_awarded"], UUID(player_id)),
                )
                # Challenge progress is shared. One successful group mission
                # counts once regardless of competing members' submissions.
                conn.execute(
                    "INSERT INTO group_progress(challenge_id,mission_id,completed_by) "
                    "SELECT gm.challenge_id,gm.mission_id,%s "
                    "FROM group_missions gm "
                    "JOIN group_members members ON members.challenge_id=gm.challenge_id "
                    "JOIN group_challenges g ON g.id=gm.challenge_id "
                    "WHERE members.player_id=%s AND gm.mission_id=%s "
                    "AND g.status='published' "
                    "AND now() BETWEEN g.opens_at AND g.closes_at "
                    "ON CONFLICT DO NOTHING",
                    (UUID(player_id), UUID(player_id), mission_id),
                )
                return {"awarded": True, "points": awarded["points_awarded"]}

    def nearby_groups(self, location: dict[str, float]) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT g.id::text AS id, g.title_he, g.description_he, " 
                "ST_Y(g.center::geometry) AS lat, ST_X(g.center::geometry) AS lon, " 
                "g.opens_at, g.closes_at, "
                "(SELECT count(*) FROM group_members mb WHERE mb.challenge_id=g.id) "
                "AS members, "
                "(SELECT count(*) FROM group_missions gm WHERE gm.challenge_id=g.id) "
                "AS missions, "
                "(SELECT count(*) FROM group_progress gp WHERE gp.challenge_id=g.id) "
                "AS completed "
                "FROM group_challenges g "
                "WHERE g.status='published' "
                "AND now() BETWEEN g.opens_at AND g.closes_at "
                "AND ST_DWithin(g.center, "
                "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, "
                "g.discovery_radius_m) "
                "AND %s <= g.discovery_radius_m "
                "ORDER BY g.closes_at",
                (location["lon"], location["lat"], location["accuracy_m"]),
            ).fetchall()

    def join_group(self, player_id: str, group_id: UUID,
                   location: dict[str, float]) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                joined = conn.execute(
                    "INSERT INTO group_members(challenge_id,player_id) "
                    "SELECT g.id,%s FROM group_challenges g "
                    "WHERE g.id=%s AND g.status='published' "
                    "AND now() BETWEEN g.opens_at AND g.closes_at "
                    "AND ST_DWithin(g.center, "
                    "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, "
                    "g.discovery_radius_m) "
                    "AND %s <= g.discovery_radius_m "
                    "ON CONFLICT (challenge_id,player_id) DO NOTHING "
                    "RETURNING challenge_id",
                    (
                        UUID(player_id), group_id,
                        location["lon"], location["lat"],
                        location["accuracy_m"],
                    ),
                ).fetchone()
                if joined:
                    return True
                # Already a member is success without duplicating a row.
                return conn.execute(
                    "SELECT 1 FROM group_members mb "
                    "JOIN group_challenges g ON g.id=mb.challenge_id "
                    "WHERE mb.player_id=%s AND mb.challenge_id=%s "
                    "AND g.status='published' "
                    "AND now() BETWEEN g.opens_at AND g.closes_at",
                    (UUID(player_id), group_id),
                ).fetchone() is not None

    def group_missions(self, player_id: str, group_id: UUID,
                       day: date) -> list[dict[str, Any]]:
        """Read group goals only after the player voluntarily joined."""
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT " + self._mission_columns() + ", "
                "(gp.mission_id IS NOT NULL) AS completed_by_group, "
                "EXISTS (SELECT 1 FROM mission_starts ms "
                "WHERE ms.player_id=mb.player_id AND ms.mission_id=m.id "
                "AND ms.challenge_day=%s) AS started_today, "
                "EXISTS (SELECT 1 FROM completions cp "
                "WHERE cp.player_id=mb.player_id AND cp.mission_id=m.id "
                "AND cp.challenge_day=%s) AS completed "
                "FROM group_missions gm "
                "JOIN missions m ON m.id=gm.mission_id "
                "JOIN group_challenges g ON g.id=gm.challenge_id "
                "JOIN group_members mb ON mb.challenge_id=gm.challenge_id "
                "LEFT JOIN group_progress gp ON gp.challenge_id=gm.challenge_id "
                "AND gp.mission_id=gm.mission_id "
                "WHERE gm.challenge_id=%s AND mb.player_id=%s "
                "AND g.status='published' "
                "AND now() BETWEEN g.opens_at AND g.closes_at "
                "AND m.status='approved' AND m.content_tier='free' "
                "ORDER BY m.slug",
                (day, day, group_id, UUID(player_id)),
            ).fetchall()

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

from .domain import INACTIVITY_SECONDS, is_session_expired, validate_coordinate


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

    def create(self, player_id: str) -> str:
        previous = self.redis.get(self.player_key(player_id))
        if previous:
            self.redis.delete(self.session_key(previous), self.proof_key(previous))
        token = secrets.token_urlsafe(32)
        fingerprint = self.digest(token)
        data = {
            "session_id": str(uuid4()),
            "player_id": player_id,
            "last_report": time.time(),
            "location": None,
        }
        with self.redis.pipeline(transaction=True) as p:
            p.set(self.session_key(fingerprint), json.dumps(data),
                  ex=INACTIVITY_SECONDS)
            p.set(self.player_key(player_id), fingerprint,
                  ex=INACTIVITY_SECONDS)
            p.execute()
        return token

    def read(self, token: str) -> dict[str, Any] | None:
        fingerprint = self.digest(token)
        raw = self.redis.get(self.session_key(fingerprint))
        if not raw:
            return None
        data = json.loads(raw)
        if (
            is_session_expired(data["last_report"], time.time())
            or self.redis.get(self.player_key(data["player_id"])) != fingerprint
        ):
            self.end(token)
            return None
        return data

    def report(self, token: str, lat: float, lon: float,
               accuracy_m: float) -> dict[str, Any] | None:
        validate_coordinate(lat, lon, accuracy_m)
        data = self.read(token)
        if data is None:
            return None
        data["last_report"] = time.time()
        data["location"] = {
            "lat": lat, "lon": lon, "accuracy_m": accuracy_m
        }
        fingerprint = self.digest(token)
        with self.redis.pipeline(transaction=True) as p:
            p.set(self.session_key(fingerprint), json.dumps(data),
                  ex=INACTIVITY_SECONDS)
            p.expire(self.player_key(data["player_id"]), INACTIVITY_SECONDS)
            p.expire(self.proof_key(fingerprint), INACTIVITY_SECONDS)
            p.execute()
        return data

    def claim_photo(self, token: str, photo_bytes: bytes) -> tuple[bool, str]:
        """Only a session-scoped image fingerprint; no image bytes retained."""
        fingerprint = self.digest(token)
        photo_hash = hashlib.sha256(photo_bytes).hexdigest()
        key = self.proof_key(fingerprint)
        with self.redis.pipeline(transaction=True) as p:
            p.sadd(key, photo_hash)
            p.expire(key, INACTIVITY_SECONDS)
            added, _ = p.execute()
        return bool(added), photo_hash

    def forget_photo(self, token: str, photo_hash: str) -> None:
        self.redis.srem(self.proof_key(self.digest(token)), photo_hash)

    def end(self, token: str) -> None:
        fingerprint = self.digest(token)
        raw = self.redis.get(self.session_key(fingerprint))
        player_id = json.loads(raw)["player_id"] if raw else None
        with self.redis.pipeline(transaction=True) as p:
            p.delete(self.session_key(fingerprint), self.proof_key(fingerprint))
            if player_id and self.redis.get(self.player_key(player_id)) == fingerprint:
                p.delete(self.player_key(player_id))
            p.execute()


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

    def profile(self, player_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT id::text AS id, display_name, total_points, "
                "completed_missions FROM players WHERE id=%s",
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
                        "ORDER BY md5(m.id::text || %s || %s) LIMIT 3",
                        (
                            location["lon"], location["lat"],
                            location["lon"], location["lat"],
                            location["accuracy_m"], player_id,
                            day.isoformat(),
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
                    "AS completed "
                    "FROM daily_assignments a "
                    "JOIN missions m ON m.id=a.mission_id "
                    "WHERE a.player_id=%s AND a.challenge_day=%s "
                    "ORDER BY a.slot",
                    (player, day),
                ).fetchall()

    def proof_mission(self, player_id: str, mission_id: UUID,
                      day: date, location: dict[str, float]) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT m.id::text AS id, m.title_he, m.instructions_he, "
                "m.visual_criteria_he, m.points, m.radius_m, "
                "ST_Distance(m.location, "
                "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) AS distance_m "
                "FROM daily_assignments a JOIN missions m ON m.id=a.mission_id "
                "WHERE a.player_id=%s AND a.challenge_day=%s "
                "AND m.id=%s AND m.status='approved'",
                (
                    location["lon"], location["lat"],
                    UUID(player_id), day, mission_id,
                ),
            ).fetchone()

    def completed(self, player_id: str, mission_id: UUID, day: date) -> bool:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM completions WHERE player_id=%s "
                "AND mission_id=%s AND challenge_day=%s",
                (UUID(player_id), mission_id, day),
            ).fetchone() is not None

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

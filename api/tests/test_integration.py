"""Real PostgreSQL/PostGIS + Redis integration test. No OpenAI network calls."""
from __future__ import annotations

import io
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.mark.skipif(
    not os.getenv("LOCOMISSION_TEST_DATABASE_URL"),
    reason="Explicit isolated test database required",
)
def test_full_solo_and_group_flow(monkeypatch):
    import psycopg
    from locomission.main import app
    from locomission.vision import Verdict

    database_url = os.environ["LOCOMISSION_TEST_DATABASE_URL"]
    assert database_url.split("/")[-1] == "locomission_test", (
        "Refuse to initialize an unapproved database"
    )

    migrations = Path(__file__).resolve().parents[2] / "db"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute((migrations / "001_initial.sql").read_text())
        conn.execute((migrations / "002_clerk_game.sql").read_text())

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("LOCOMISSION_DEV_MODE", "true")
    monkeypatch.setenv(
        "LOCOMISSION_DEV_PLAYER_ID", "ee445be0-e611-4eac-8b79-12f143fbcd18"
    )
    monkeypatch.setattr(
        "locomission.main.grade_photo",
        lambda *args, **kwargs: Verdict(True, "תמונה תקינה"),
    )

    latitude, longitude = 32.0853, 34.7818
    mission_ids = []
    with psycopg.connect(database_url) as conn:
        for n in range(4):
            mission_id = conn.execute(
                "INSERT INTO missions "
                "(slug,title_he,historical_fact_he,instructions_he,"
                "visual_criteria_he,source_title,source_url,reviewed_by,reviewed_at,"
                "status,location,radius_m,points) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'CI',now(),'approved',"
                "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,500,7) "
                "RETURNING id",
                (
                    "ci-" + str(uuid4()), "משימת בדיקה", "עובדה לבדיקת תוכנה בלבד",
                    "צלם תמונה", ["יש אובייקט בפריים"], "CI fixture",
                    "https://example.invalid/ci", longitude, latitude,
                ),
            ).fetchone()[0]
            mission_ids.append(mission_id)

        group_id = conn.execute(
            "INSERT INTO group_challenges "
            "(title_he,description_he,center,discovery_radius_m,"
            "opens_at,closes_at) "
            "VALUES ('בדיקת קבוצה','',"
            "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,"
            "800,now()-interval '1 day',now()+interval '1 day') "
            "RETURNING id",
            (longitude, latitude),
        ).fetchone()[0]

    with TestClient(app) as client:
        assert client.get("/api/me").status_code == 401
        session = client.post("/api/sessions")
        assert session.status_code == 201, session.text
        token = session.json()["session_token"]
        header = {"Authorization": "Bearer " + token}

        no_location = client.get("/api/missions/today", headers=header)
        assert no_location.status_code == 409

        reported = client.post(
            "/api/location",
            headers=header,
            json={
                "lat": latitude, "lon": longitude,
                "accuracy_m": 12, "foreground": True,
            },
        )
        assert reported.status_code == 200, reported.text
        assert client.post(
            "/api/location",
            headers=header,
            json={
                "lat": latitude, "lon": longitude,
                "accuracy_m": 12, "foreground": False,
            },
        ).status_code == 400

        lobby = client.get("/api/lobby", headers=header)
        assert lobby.status_code == 200, lobby.text
        snapshot = lobby.json()
        assert len(snapshot["missions"]) == 3
        assert "total_points" in snapshot["profile"]
        assert isinstance(snapshot["presence"]["cells"], list)
        assert isinstance(snapshot["groups"], list)
        assert all(not m["started_today"] for m in snapshot["missions"])

        day = client.get("/api/missions/today", headers=header)
        assert day.status_code == 200, day.text
        assigned = {UUID(m["id"]) for m in day.json()["missions"]}
        assert len(assigned) == 3
        assert len({
            m["id"] for m in client.get("/api/missions/today", headers=header)
            .json()["missions"]
        }) == 3

        # Group-only mission is not in today's personal daily-three.
        group_only = next(mid for mid in mission_ids if mid not in assigned)
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "INSERT INTO group_missions(challenge_id,mission_id) VALUES (%s,%s)",
                (group_id, group_only),
            )

        groups = client.get("/api/groups/nearby", headers=header)
        assert groups.status_code == 200, groups.text
        assert str(group_id) in {g["id"] for g in groups.json()["groups"]}

        # Not joined yet: group mission cannot be started or submitted.
        photo_io = io.BytesIO()
        Image.new("RGB", (40, 40), (20, 60, 80)).save(photo_io, format="PNG")
        image_bytes = photo_io.getvalue()

        def submit(mission_id, request_id=None):
            return client.post(
                "/api/missions/" + str(mission_id) + "/proof",
                content=image_bytes,
                headers={**header, "Content-Type": "image/png",
                         "X-Request-ID": str(request_id or uuid4())},
            )

        assert submit(group_only).status_code == 404
        join = client.post("/api/groups/" + str(group_id) + "/join", headers=header)
        assert join.status_code == 200, join.text

        goal_list = client.get(
            "/api/groups/" + str(group_id) + "/missions", headers=header
        )
        assert goal_list.status_code == 200, goal_list.text
        assert goal_list.json()["missions"][0]["id"] == str(group_only)
        assert goal_list.json()["missions"][0]["started_today"] is False
        assert goal_list.json()["missions"][0]["completed"] is False

        # Starting an eligible mission is durable and idempotent.
        started = client.post(
            "/api/missions/" + str(group_only) + "/start", headers=header
        )
        assert started.status_code == 200, started.text
        assert started.json()["first_start_today"] is True
        after_start = client.get(
            "/api/groups/" + str(group_id) + "/missions", headers=header
        ).json()["missions"]
        assert after_start[0]["started_today"] is True
        again = client.post(
            "/api/missions/" + str(group_only) + "/start", headers=header
        )
        assert again.json()["first_start_today"] is False
        # An in-flight proof blocks another paid model request for this mission.
        owner = str(uuid4())
        assert app.state.sessions.claim_verification(
            token, os.environ["LOCOMISSION_DEV_PLAYER_ID"],
            str(group_only), owner,
        ) is True
        assert app.state.sessions.claim_verification(
            token, os.environ["LOCOMISSION_DEV_PLAYER_ID"],
            str(group_only), str(uuid4()),
        ) is False
        active_status = client.get(
            "/api/missions/" + str(group_only) + "/proof-requests/" + owner,
            headers=header,
        )
        assert active_status.status_code == 200
        assert active_status.json()["state"] == "processing"
        lock_key = app.state.sessions.verification_key(
            app.state.sessions.digest(token)
        )
        # Compare TTLs in one Redis command: two separate calls can differ
        # by a millisecond even when the deadlines are identical.
        lock_ttl, parent_ttl = app.state.cache.eval(
            "return {redis.call('PTTL', KEYS[1]), redis.call('PTTL', KEYS[2])}",
            2, lock_key,
            app.state.sessions.session_key(app.state.sessions.digest(token)),
        )
        assert 0 < lock_ttl <= parent_ttl
        blocked = submit(group_only)
        assert blocked.status_code == 409
        app.state.sessions.release_verification(token, str(group_only), str(uuid4()))
        assert app.state.cache.hget(lock_key, str(group_only)).startswith(
            owner + ":" + owner + "|"
        )
        app.state.sessions.release_verification(token, str(group_only), owner)
        assert app.state.cache.exists(lock_key) == 0
        assert client.get("/api/me", headers=header).json()["missions_started"] == 1

        unsupported = client.post(
            "/api/missions/" + str(group_only) + "/proof",
            headers={**header, "Content-Type": "application/octet-stream",
                     "X-Request-ID": str(uuid4())},
            content=b"not-a-photo",
        )
        assert unsupported.status_code == 415
        oversize = client.post(
            "/api/missions/" + str(group_only) + "/proof",
            headers={**header, "Content-Type": "image/png",
                     "X-Request-ID": str(uuid4())},
            content=b"x" * (5 * 1024 * 1024 + 1),
        )
        assert oversize.status_code == 413
        # A transient model failure must release the lease and photo hash.
        from locomission.vision import VisionUnavailable

        def upstream_down(*_args):
            raise VisionUnavailable("Simulated model timeout")

        monkeypatch.setattr("locomission.main.grade_photo", upstream_down)
        retried_id = uuid4()
        transient = submit(group_only, retried_id)
        assert transient.status_code == 503
        pending_status = client.get(
            "/api/missions/" + str(group_only) +
            "/proof-requests/" + str(retried_id),
            headers=header,
        )
        assert pending_status.json()["state"] == "retry_required"
        monkeypatch.setattr(
            "locomission.main.grade_photo",
            lambda *_args: Verdict(True, "תמונה תקינה"),
        )
        success = submit(group_only, retried_id)
        assert success.status_code == 200, success.text
        assert success.json()["awarded"] is True
        assert success.json()["points"] == 7
        # Lost HTTP reply: same UUID recovers exact result with no new model call.
        recovered = client.get(
            "/api/missions/" + str(group_only) +
            "/proof-requests/" + str(retried_id), headers=header,
        )
        assert recovered.status_code == 200
        assert recovered.json()["state"] == "completed"
        assert recovered.json()["points"] == 7
        replay = submit(group_only, retried_id)
        assert replay.status_code == 200
        assert replay.json()["awarded"] is True
        assert replay.json()["points"] == 7

        # Replay fingerprints never outlive their parent session key.
        fingerprint = app.state.sessions.digest(token)
        parent_ttl = app.state.cache.pttl(app.state.sessions.session_key(fingerprint))
        proof_ttl = app.state.cache.pttl(app.state.sessions.proof_key(fingerprint))
        assert 0 < proof_ttl <= parent_ttl

        duplicate = submit(group_only)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["points"] == 0
        group_goals = client.get(
            "/api/groups/" + str(group_id) + "/missions", headers=header
        ).json()["missions"]
        assert group_goals[0]["completed"] is True
        assert group_goals[0]["completed_by_group"] is True
        profile = client.get("/api/me", headers=header).json()
        assert profile["total_points"] == 7
        assert profile["missions_started"] == 1
        assert profile["credits_balance"] == 0
        leaders = client.get("/api/leaderboard").json()["leaders"]
        assert any(p["total_points"] == 7 for p in leaders)
        presence = client.get("/api/presence", headers=header).json()["cells"]
        assert len(presence) >= 1
        assert presence[0]["lat"] == round(latitude, 3)

        with psycopg.connect(database_url) as conn:
            assert conn.execute(
                "SELECT count(*) FROM group_progress "
                "WHERE challenge_id=%s AND mission_id=%s",
                (group_id, group_only),
            ).fetchone()[0] == 1

        assert client.delete("/api/sessions/current", headers=header).json()["ended"]
        assert app.state.cache.exists(app.state.sessions.proof_key(fingerprint)) == 0
        assert app.state.cache.exists(app.state.sessions.verification_key(fingerprint)) == 0
        assert app.state.cache.exists(app.state.sessions.result_key(fingerprint)) == 0
        assert not client.get("/api/presence", headers=header).status_code == 200
        assert client.get("/api/me", headers=header).status_code == 401
        with psycopg.connect(database_url) as conn:
            assert conn.execute(
                "SELECT total_points FROM players WHERE id=%s",
                (UUID(os.environ["LOCOMISSION_DEV_PLAYER_ID"]),),
            ).fetchone()[0] == 7


    # Simulate response loss after a COMMITTED PostgreSQL award but before
    # the ephemeral Redis result cache was written. The status endpoint
    # must recover it from PostgreSQL without requiring another model call.
    solo_id, second_solo = sorted(assigned, key=str)[:2]
    with TestClient(app) as client:
        session2 = client.post("/api/sessions")
        assert session2.status_code == 201
        token2 = session2.json()["session_token"]
        h2 = {"Authorization": "Bearer " + token2}
        assert client.post("/api/location", headers=h2, json={
            "lat": latitude, "lon": longitude,
            "accuracy_m": 12, "foreground": True,
        }).status_code == 200
        assert client.post(
            "/api/missions/" + str(solo_id) + "/start", headers=h2,
        ).status_code == 200

        expected = uuid4()
        real_cache = app.state.sessions.cache_proof_result

        def simulate_lost_response(*_args):
            raise RuntimeError("Response interrupted after SQL COMMIT")

        monkeypatch.setattr(
            app.state.sessions, "cache_proof_result", simulate_lost_response,
        )
        with pytest.raises(RuntimeError, match="SQL COMMIT"):
            client.post(
                "/api/missions/" + str(solo_id) + "/proof",
                headers={**h2, "Content-Type": "image/png",
                         "X-Request-ID": str(expected)},
                content=image_bytes,
            )
        monkeypatch.setattr(
            app.state.sessions, "cache_proof_result", real_cache,
        )
        from_database = client.get(
            "/api/missions/" + str(solo_id) +
            "/proof-requests/" + str(expected), headers=h2,
        )
        assert from_database.status_code == 200
        assert from_database.json()["state"] == "completed"
        assert from_database.json()["points"] == 7
        same_request = client.post(
            "/api/missions/" + str(solo_id) + "/proof",
            headers={**h2, "Content-Type": "image/png",
                     "X-Request-ID": str(expected)},
            content=image_bytes,
        )
        assert same_request.status_code == 200
        assert same_request.json()["points"] == 7
        assert client.get("/api/me", headers=h2).json()["total_points"] == 14

        # An explicit logout mid-model must prevent a new credit.
        assert client.post(
            "/api/missions/" + str(second_solo) + "/start", headers=h2,
        ).status_code == 200

        def disconnect_during_verification(*_args):
            app.state.sessions.end(token2)
            return Verdict(True, "תמונה תקינה")

        monkeypatch.setattr(
            "locomission.main.grade_photo", disconnect_during_verification,
        )
        cut_off = client.post(
            "/api/missions/" + str(second_solo) + "/proof",
            headers={**h2, "Content-Type": "image/png",
                     "X-Request-ID": str(uuid4())},
            content=image_bytes,
        )
        assert cut_off.status_code == 401
        with psycopg.connect(database_url) as db:
            count = db.execute(
                "SELECT count(*) FROM completions "
                "WHERE player_id=%s AND mission_id=%s",
                (UUID(os.environ["LOCOMISSION_DEV_PLAYER_ID"]), second_solo),
            ).fetchone()[0]
            assert count == 0
        monkeypatch.setattr(
            "locomission.main.grade_photo",
            lambda *_args: Verdict(True, "תמונה תקינה"),
        )

    # The DB uniqueness constraint is the final safety net even when
    # independent workers attempt to award simultaneously.
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from locomission.domain import challenge_day
    today = challenge_day(datetime.now(timezone.utc))
    # Independent store keeps its PostgreSQL pool alive outside TestClient.
    from locomission.infra import GameStore
    scoring_store = GameStore(database_url)
    try:
        with ThreadPoolExecutor(max_workers=12) as executor:
            awards = list(executor.map(
                lambda i: scoring_store.award(
                    os.environ["LOCOMISSION_DEV_PLAYER_ID"],
                    second_solo, today, uuid4(),
                ),
                range(36),
            ))
    finally:
        scoring_store.close()
    assert sum(row["points"] for row in awards) == 7
    assert sum(row["awarded"] for row in awards) == 1
    with psycopg.connect(database_url) as db:
        assert db.execute(
            "SELECT count(*) FROM completions WHERE player_id=%s "
            "AND mission_id=%s",
            (UUID(os.environ["LOCOMISSION_DEV_PLAYER_ID"]), second_solo),
        ).fetchone()[0] == 1

    # A replacement SESSION revokes the old token; an old logout is harmless.
    with TestClient(app) as client:
        first = client.post("/api/sessions").json()["session_token"]
        second = client.post("/api/sessions").json()["session_token"]
        assert app.state.sessions.read(first) is None
        app.state.sessions.end(first)
        assert app.state.sessions.read(second) is not None
        app.state.sessions.end(second)
        assert app.state.sessions.report(second, latitude, longitude, 10) is None

    # Clerk identities are mapped to separate durable players, and a
    # gameplay token alone cannot access another user's account.
    monkeypatch.setenv("LOCOMISSION_DEV_MODE", "false")
    identity = {"clerk_id": "user_clerk_test_A"}
    monkeypatch.setattr("locomission.main.clerk_subject",
                        lambda req: identity["clerk_id"])
    with TestClient(app) as client:
        fake_jwt = {"Authorization": "Bearer verified-by-mock"}
        no_consent = client.post("/api/sessions", headers=fake_jwt,
                                 json={"adult_confirmed": False})
        assert no_consent.status_code == 403
        a = client.post("/api/sessions", headers=fake_jwt,
                        json={"adult_confirmed": True})
        assert a.status_code == 201, a.text
        a_header = {**fake_jwt, "X-Game-Session": a.json()["session_token"]}
        a_profile = client.get("/api/me", headers=a_header)
        assert a_profile.status_code == 200, a_profile.text
        assert a_profile.json()["credits_balance"] == 0
        edit = client.patch("/api/me", headers=a_header,
                            json={"display_name": "בודק אחת"})
        assert edit.status_code == 200
        assert edit.json()["display_name"] == "בודק אחת"

        identity["clerk_id"] = "user_clerk_test_B"
        denied = client.get("/api/me", headers=a_header)
        assert denied.status_code == 401

        b = client.post("/api/sessions", headers=fake_jwt,
                        json={"adult_confirmed": True})
        assert b.status_code == 201
        b_header = {**fake_jwt, "X-Game-Session": b.json()["session_token"]}
        b_profile = client.get("/api/me", headers=b_header)
        assert b_profile.status_code == 200
        assert b_profile.json()["id"] != a_profile.json()["id"]
        assert b_profile.json()["total_points"] == 0

        # The same verified Clerk identity reuses its permanent player row.
        identity["clerk_id"] = "user_clerk_test_A"
        another = client.post("/api/sessions", headers=fake_jwt,
                              json={"adult_confirmed": False})
        assert another.status_code == 201
        new_header = {**fake_jwt, "X-Game-Session": another.json()["session_token"]}
        assert client.get("/api/me", headers=new_header).json()["display_name"] == "בודק אחת"
        assert client.get("/api/me", headers=a_header).status_code == 401

    # Editorial candidates never enter the game until a named human
    # verifies source, coordinates, safe site and photo criteria.
    from locomission.editorial import make_parser, run
    slug = "human-reviewed-ci-" + str(uuid4())
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO missions(slug,title_he,historical_fact_he,"
            "instructions_he,visual_criteria_he,source_title,source_url,"
            "status,location,radius_m,points) VALUES "
            "(%s,'טיוטת בדיקה','עובדה','צלם',ARRAY['תנאי'],"
            "'מקור בדיקה','https://example.invalid/source','draft',"
            "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,300,7)",
            (slug, longitude, latitude),
        )
    opts = make_parser().parse_args([
        "approve", slug, "--reviewer", "Human CI fixture",
        "--lat", str(latitude), "--lon", str(longitude), "--radius-m", "300",
        "--source-reviewed", "--hebrew-reviewed",
        "--safe-photo-site-reviewed", "--visual-criteria-reviewed",
    ])
    assert run(opts) == 0
    with pytest.raises(ValueError):
        run(opts)
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT status,reviewed_by,reviewed_at FROM missions WHERE slug=%s",
            (slug,),
        ).fetchone()
        assert row[0] == "approved"
        assert row[1] == "Human CI fixture"
        assert row[2] is not None

    # No live location or photo bytes in durable PostgreSQL by design.


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
    import redis
    from locomission.main import app
    from locomission.vision import Verdict

    database_url = os.environ["LOCOMISSION_TEST_DATABASE_URL"]
    assert database_url.split("/")[-1] == "locomission_test", (
        "Refuse to initialize an unapproved database"
    )

    schema = (Path(__file__).resolve().parents[2] / "db" / "001_initial.sql").read_text()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(schema)

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

        # Not joined yet: group mission cannot be submitted.
        photo_io = io.BytesIO()
        Image.new("RGB", (40, 40), (20, 60, 80)).save(photo_io, format="PNG")
        image_bytes = photo_io.getvalue()

        def submit(mission_id):
            return client.post(
                "/api/missions/" + str(mission_id) + "/proof",
                headers=header,
                files={"photo": ("proof.png", image_bytes, "image/png")},
                data={"request_id": str(uuid4())},
            )

        assert submit(group_only).status_code == 404
        join = client.post("/api/groups/" + str(group_id) + "/join", headers=header)
        assert join.status_code == 200, join.text

        goal_list = client.get(
            "/api/groups/" + str(group_id) + "/missions", headers=header
        )
        assert goal_list.status_code == 200, goal_list.text
        assert goal_list.json()["missions"][0]["id"] == str(group_only)

        success = submit(group_only)
        assert success.status_code == 200, success.text
        assert success.json()["awarded"] is True
        assert success.json()["points"] == 7

        # Replay fingerprints never outlive their parent session key.
        fingerprint = app.state.sessions.digest(token)
        parent_ttl = app.state.cache.pttl(app.state.sessions.session_key(fingerprint))
        proof_ttl = app.state.cache.pttl(app.state.sessions.proof_key(fingerprint))
        assert 0 < proof_ttl <= parent_ttl

        duplicate = submit(group_only)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["points"] == 0
        assert client.get("/api/me", headers=header).json()["total_points"] == 7

        with psycopg.connect(database_url) as conn:
            assert conn.execute(
                "SELECT count(*) FROM group_progress "
                "WHERE challenge_id=%s AND mission_id=%s",
                (group_id, group_only),
            ).fetchone()[0] == 1

        assert client.delete("/api/sessions/current", headers=header).json()["ended"]
        assert app.state.cache.exists(app.state.sessions.proof_key(fingerprint)) == 0
        assert client.get("/api/me", headers=header).status_code == 401
        with psycopg.connect(database_url) as conn:
            assert conn.execute(
                "SELECT total_points FROM players WHERE id=%s",
                (UUID(os.environ["LOCOMISSION_DEV_PLAYER_ID"]),),
            ).fetchone()[0] == 7

    # No live location or photo bytes in durable PostgreSQL by design.

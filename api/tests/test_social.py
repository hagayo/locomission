"""MVP stage two: private profile/history, durable progress, local group peers."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skipif(
    not os.getenv("LOCOMISSION_TEST_DATABASE_URL"),
    reason="Requires isolated PostgreSQL/PostGIS and Redis test services",
)
def test_social_history_avatars_and_group_presence(monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from urllib.parse import urlsplit, urlunsplit

    from locomission.domain import challenge_day
    from locomission.main import app
    from locomission.migrate import run_migrations

    base_url = os.environ["LOCOMISSION_TEST_DATABASE_URL"]
    attrs = conninfo_to_dict(base_url)
    assert attrs["dbname"] == "locomission_test"
    disposable_name = "lm_social_" + uuid4().hex[:12]
    parsed = urlsplit(base_url)
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    social_url = urlunsplit(parsed._replace(path="/" + disposable_name))

    user = uuid4()
    near_user = uuid4()
    far_user = uuid4()
    stranger = uuid4()
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(disposable_name))
        )
        try:
            run_migrations(
                social_url, Path(__file__).resolve().parents[2] / "db",
                apply=True,
            )
            monkeypatch.setenv("DATABASE_URL", social_url)
            monkeypatch.setenv("LOCOMISSION_DEV_MODE", "true")
            monkeypatch.setenv("LOCOMISSION_ENV", "local")
            monkeypatch.setenv("LOCOMISSION_DEV_PLAYER_ID", str(user))
            with psycopg.connect(social_url) as conn:
                for player in (near_user, far_user, stranger):
                    conn.execute(
                        "INSERT INTO players(id,display_name) "
                        "VALUES (%s,'Test group player')", (player,),
                    )
                group = conn.execute(
                    "INSERT INTO group_challenges"
                    "(title_he,description_he,center,discovery_radius_m,"
                    "opens_at,closes_at) VALUES ('Nearby test','',"
                    "ST_SetSRID(ST_MakePoint(34.7818,32.0853),4326)::geography,"
                    "1500,now()-interval '1 day',now()+interval '1 day') "
                    "RETURNING id",
                ).fetchone()[0]
                for player in (near_user, far_user):
                    conn.execute(
                        "INSERT INTO group_members(challenge_id,player_id) "
                        "VALUES (%s,%s)",
                        (group, player),
                    )
            with TestClient(app) as client:
                first = client.post("/api/sessions")
                assert first.status_code == 201
                token = first.json()["session_token"]
                auth = {"Authorization": "Bearer " + token}
                assert client.get("/api/me/history").status_code == 401
                assert client.get("/api/me/achievements").status_code == 401
                assert client.get(
                    "/api/groups/" + str(group) + "/presence",
                    headers=auth,
                ).status_code == 409  # GPS required for group activity
                location = {
                    "lat": 32.0853, "lon": 34.7818,
                    "accuracy_m": 12, "foreground": True,
                }
                assert client.post(
                    "/api/location", headers=auth, json=location,
                ).status_code == 200

                no_join = client.get(
                    "/api/groups/" + str(group) + "/presence",
                    headers=auth,
                )
                assert no_join.status_code == 403
                assert app.state.game.active_group_member_ids(group, str(stranger)) is None
                assert client.get(
                    "/api/me/history", headers=auth,
                ).json() == {"items": [], "next_before": None}
                profile = client.get("/api/me", headers=auth).json()
                assert profile["avatar_id"] == "explorer"
                assert client.patch(
                    "/api/me/avatar", headers=auth,
                    json={"avatar_id": "https://example.com/fake.png"},
                ).status_code == 422
                changed = client.patch(
                    "/api/me/avatar", headers=auth,
                    json={"avatar_id": "torch"},
                )
                assert changed.status_code == 200
                assert changed.json()["avatar_id"] == "torch"
                with psycopg.connect(social_url) as conn:
                    actual = conn.execute(
                        "SELECT settings->>'avatar' FROM players WHERE id=%s",
                        (user,),
                    ).fetchone()
                    assert actual == ("torch",)

                no_awards = client.get(
                    "/api/me/achievements", headers=auth,
                ).json()["achievements"]
                assert not any(item["unlocked"] for item in no_awards)

                # Completion history is persisted and paginated; never reveal
                # the separate player's rows, locations, photos or request IDs.
                today = challenge_day(datetime.now(timezone.utc))
                missions = []
                with psycopg.connect(social_url) as conn:
                    for index in range(14):
                        mission = conn.execute(
                            "INSERT INTO missions"
                            "(slug,title_he,historical_fact_he,instructions_he,"
                            "visual_criteria_he,source_title,source_url,"
                            "reviewed_by,reviewed_at,status,location,radius_m,points)"
                            " VALUES (%s,%s,'fact','photo',ARRAY['photo'],"
                            "'CI','https://example.invalid/test','CI',now(),"
                            "'approved',"
                            "ST_SetSRID(ST_MakePoint(34.7818,32.0853),4326)"
                            "::geography,250,7) RETURNING id",
                            ("social-ci-" + str(uuid4()), "משימה " + str(index)),
                        ).fetchone()[0]
                        missions.append(mission)
                for mission in missions[:12]:
                    assert app.state.game.award(str(user), mission, today, uuid4()) == {
                        "awarded": True, "points": 7,
                    }
                for mission in missions[12:]:
                    assert app.state.game.award(
                        str(near_user), mission, today, uuid4(),
                    )["awarded"] is True
                first_page = client.get(
                    "/api/me/history?limit=10", headers=auth,
                )
                assert first_page.status_code == 200, first_page.text
                first_items = first_page.json()["items"]
                assert len(first_items) == 10
                cursor = first_page.json()["next_before"]
                assert cursor == first_items[-1]["id"]
                second_page = client.get(
                    "/api/me/history?limit=10&before=" + cursor,
                    headers=auth,
                ).json()
                assert len(second_page["items"]) == 2
                assert second_page["next_before"] is None
                combined = first_items + second_page["items"]
                assert len({item["id"] for item in combined}) == 12
                assert all(
                    item["points_awarded"] == 7 and "request_id" not in item
                    and "location" not in item and "player_id" not in item
                    and "image" not in item for item in combined
                )
                assert client.get(
                    "/api/me/history?limit=21", headers=auth,
                ).status_code == 422
                assert client.get(
                    "/api/me/history?limit=10&before=" + str(uuid4()),
                    headers=auth,
                ).json()["items"] == []

                # No prior achievements ledger: computed from durable totals.
                rewards = client.get(
                    "/api/me/achievements", headers=auth,
                ).json()["achievements"]
                by_id = {a["id"]: a for a in rewards}
                assert by_id["first"]["unlocked"]
                assert by_id["five"]["unlocked"]
                assert by_id["twenty"]["current"] == 12
                assert not by_id["twenty"]["unlocked"]
                assert not by_id["team"]["unlocked"]

                # Group presence is a number only, scoped to membership and 1km.
                joined = client.post(
                    "/api/groups/" + str(group) + "/join",
                    headers=auth,
                )
                assert joined.status_code == 200, joined.text
                assert client.get(
                    "/api/me/achievements", headers=auth,
                ).json()["achievements"][-1]["unlocked"]
                peer_near = app.state.sessions.create(str(near_user))
                peer_far = app.state.sessions.create(str(far_user))
                try:
                    assert app.state.sessions.report(
                        peer_near, 32.0856, 34.7818, 10,
                    )
                    assert app.state.sessions.report(
                        peer_far, 32.14, 34.7818, 10,
                    )
                    result = client.get(
                        "/api/groups/" + str(group) + "/presence",
                        headers=auth,
                    )
                    assert result.status_code == 200
                    assert result.json() == {
                        "active_nearby": 1, "truncated": False,
                    }
                    # Counts cannot expose any player ID or exact GPS.
                    assert str(near_user) not in result.text
                    assert "32.0856" not in result.text
                    app.state.sessions.end(peer_near)
                    assert client.get(
                        "/api/groups/" + str(group) + "/presence",
                        headers=auth,
                    ).json()["active_nearby"] == 0
                finally:
                    app.state.sessions.end(peer_near)
                    app.state.sessions.end(peer_far)

                # A new SESSION keeps permanent avatar, history and rewards.
                second = client.post("/api/sessions")
                assert second.status_code == 201
                new_auth = {
                    "Authorization": "Bearer " + second.json()["session_token"]
                }
                assert client.get("/api/me", headers=new_auth).json()["avatar_id"] == "torch"
                assert len(client.get(
                    "/api/me/history", headers=new_auth,
                ).json()["items"]) == 10
                assert client.get(
                    "/api/groups/" + str(group) + "/presence",
                    headers=auth,
                ).status_code == 401
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(disposable_name)
                )
            )

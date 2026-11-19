"""LocoMission game API, development slice. Production auth is fail-closed."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import redis
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .domain import challenge_day, mission_is_reachable, utc_now, validate_coordinate
from .infra import GameStore, Sessions
from .vision import InvalidPhoto, VisionUnavailable, grade_photo, normalize_photo

bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL")
    redis_url = os.getenv("REDIS_URL")
    if not database_url or not redis_url:
        raise RuntimeError("DATABASE_URL and REDIS_URL must be configured")
    game = GameStore(database_url)
    cache = redis.Redis.from_url(redis_url, decode_responses=True)
    app.state.game = game
    app.state.cache = cache
    app.state.sessions = Sessions(cache)
    try:
        yield
    finally:
        game.close()
        cache.close()


app = FastAPI(
    title="LocoMission Game API",
    version="0.1.0",
    description="The world is your mission. Development-only identity bootstrap.",
    lifespan=lifespan,
)


# Browser prototype only. This is NOT a decision to ship as a PWA.
# Disabled entirely without explicitly enabled local development mode.
if os.getenv("LOCOMISSION_DEV_MODE", "").lower() == "true":
    app.mount(
        "/prototype",
        StaticFiles(
            directory=str(Path(__file__).resolve().parents[3] / "prototype"),
            html=True,
        ),
        name="prototype",
    )



class LocationReport(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0)
    foreground: bool


def identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> tuple[str, dict]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="נדרש SESSION פעיל")
    token = credentials.credentials
    data = app.state.sessions.read(token)
    if data is None:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
    return token, data


def require_location(session: dict) -> dict:
    loc = session.get("location")
    if loc is None:
        raise HTTPException(status_code=409, detail="יש לדווח מיקום בקדמת האפליקציה")
    return loc


@app.get("/api/health")
def health():
    try:
        app.state.game.ping()
        app.state.cache.ping()
    except Exception:
        raise HTTPException(status_code=503, detail="שירות המשחק אינו זמין")
    return {"status": "ok"}


@app.get("/api/product")
def product():
    return {
        "name": "LocoMission",
        "slogan": "The world is your mission",
        "description": "Location-based history facts and missions",
        "slogan_he": "העולם הוא המשימה שלך!",
        "location_permission": "foreground_only",
        "photo_verification": "OpenAI GPT-4o mini vision",
        "daily_missions_limit": 3,
        "privacy": "See docs/PRIVACY-DRAFT.md (not a released policy)",
    }


@app.post("/api/sessions", status_code=201)
def start_session():
    # No production login route exists until identity integration is chosen.
    if os.getenv("LOCOMISSION_DEV_MODE", "").lower() != "true":
        raise HTTPException(status_code=503, detail="חיבור שחקן דורש מערכת הזדהות")
    configured = os.getenv("LOCOMISSION_DEV_PLAYER_ID", "")
    try:
        player_id = str(UUID(configured))
    except ValueError:
        raise HTTPException(status_code=503, detail="מזהה שחקן פיתוח אינו מוגדר")
    app.state.game.ensure_dev_player(player_id)
    token = app.state.sessions.create(player_id)
    return {
        "session_token": token,
        "expires_after_no_location_seconds": 180,
        "location_permission": "foreground_only",
    }


@app.delete("/api/sessions/current")
def end_session(auth: tuple[str, dict] = Depends(identity)):
    token, _ = auth
    app.state.sessions.end(token)
    return {"ended": True}


@app.post("/api/location")
def report_location(
    report: LocationReport, auth: tuple[str, dict] = Depends(identity),
):
    if not report.foreground:
        raise HTTPException(status_code=400, detail="מיקום מותר רק בזמן שימוש פעיל")
    try:
        validate_coordinate(report.lat, report.lon, report.accuracy_m)
    except ValueError:
        raise HTTPException(status_code=400, detail="נתוני מיקום לא תקינים")
    token, _ = auth
    updated = app.state.sessions.report(
        token, report.lat, report.lon, report.accuracy_m,
    )
    if updated is None:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
    # Never include player coordinates in the response.
    return {"accepted": True}


@app.get("/api/me")
def my_profile(auth: tuple[str, dict] = Depends(identity)):
    _, session = auth
    result = app.state.game.profile(session["player_id"])
    if result is None:
        raise HTTPException(status_code=404, detail="השחקן לא נמצא")
    return result


@app.get("/api/missions/today")
def today_missions(auth: tuple[str, dict] = Depends(identity)):
    _, session = auth
    location = require_location(session)
    day = challenge_day(utc_now())
    missions = app.state.game.today(session["player_id"], day, location)
    return {"day_utc": day.isoformat(), "missions": missions, "limit": 3}


@app.get("/api/groups/nearby")
def nearby_groups(auth: tuple[str, dict] = Depends(identity)):
    _, session = auth
    return {"groups": app.state.game.nearby_groups(require_location(session))}


@app.get("/api/groups/{group_id}/missions")
def group_missions(group_id: UUID, auth: tuple[str, dict] = Depends(identity)):
    _, session = auth
    return {
        "missions": app.state.game.group_missions(session["player_id"], group_id)
    }



@app.post("/api/groups/{group_id}/join")
def join_group(group_id: UUID, auth: tuple[str, dict] = Depends(identity)):
    _, session = auth
    joined = app.state.game.join_group(
        session["player_id"], group_id, require_location(session),
    )
    if not joined:
        raise HTTPException(status_code=404, detail="האתגר אינו פתוח באזור שלך")
    return {"joined": True}


@app.post("/api/missions/{mission_id}/proof")
def submit_proof(
    mission_id: UUID,
    photo: UploadFile = File(...),
    request_id: UUID = Form(...),
    auth: tuple[str, dict] = Depends(identity),
):
    token, session = auth
    player_id = session["player_id"]
    day = challenge_day(utc_now())
    location = require_location(session)

    mission = app.state.game.proof_mission(player_id, mission_id, day, location)
    if mission is None:
        raise HTTPException(status_code=404, detail="המשימה אינה זמינה היום")
    if not mission_is_reachable(
        mission["distance_m"], mission["radius_m"], location["accuracy_m"],
    ):
        raise HTTPException(status_code=403, detail="אינך בתחום המשימה")
    if app.state.game.completed(player_id, mission_id, day):
        return {"accepted": True, "awarded": False, "points": 0,
                "reason_he": "כבר קיבלת נקודות על המשימה היום"}

    from .domain import MAX_PHOTO_BYTES
    source = photo.file.read(MAX_PHOTO_BYTES + 1)
    try:
        cleaned = normalize_photo(source)
    except InvalidPhoto as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        photo.file.close()
        del source

    first_use, photo_hash = app.state.sessions.claim_photo(token, cleaned)
    if not first_use:
        raise HTTPException(status_code=409, detail="התמונה כבר נשלחה ב-SESSION הזה")

    try:
        verdict = grade_photo(
            cleaned, mission["title_he"], mission["instructions_he"],
            mission["visual_criteria_he"],
        )
    except VisionUnavailable:
        # Permit retry after transient upstream failure; never award points.
        app.state.sessions.forget_photo(token, photo_hash)
        raise HTTPException(status_code=503, detail="אימות התמונה אינו זמין כעת")
    finally:
        del cleaned

    if not verdict.accepted:
        return {"accepted": False, "awarded": False, "points": 0,
                "reason_he": verdict.reason_he}

    # Recheck against session expiry and location movement during AI latency.
    current = app.state.sessions.read(token)
    if current is None:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים בזמן הבדיקה")
    latest_location = require_location(current)
    final_mission = app.state.game.proof_mission(
        player_id, mission_id, day, latest_location,
    )
    if final_mission is None or not mission_is_reachable(
        final_mission["distance_m"], final_mission["radius_m"],
        latest_location["accuracy_m"],
    ):
        raise HTTPException(status_code=403, detail="המיקום כבר אינו בתחום המשימה")
    result = app.state.game.award(player_id, mission_id, day, request_id)
    return {
        "accepted": True,
        "awarded": result["awarded"],
        "points": result["points"],
        "reason_he": verdict.reason_he if result["awarded"]
                     else "המשימה כבר זוכתה",
    }

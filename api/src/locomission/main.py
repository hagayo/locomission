"""LocoMission game API, development slice. Production auth is fail-closed."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .domain import challenge_day, mission_is_reachable, utc_now, validate_coordinate
from .infra import GameStore, PlayerMustConfirmAdult, SessionExpired, Sessions, SuspiciousLocation
from .clerk_auth import clerk_subject, is_dev_mode
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
    description="The world is your mission. Clerk-authenticated Game API.",
    lifespan=lifespan,
)


origins = [o.strip() for o in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Game-Session"],
        allow_credentials=False,
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



class StartSessionRequest(BaseModel):
    adult_confirmed: bool = False


class ProfileChange(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class LocationReport(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0)
    foreground: bool


def identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> tuple[str, dict]:
    if is_dev_mode():
        # Only available with a deliberately enabled LOCAL development bypass.
        token = credentials.credentials if credentials else ""
        clerk_player = None
    else:
        subject = clerk_subject(request)
        clerk_player = app.state.game.player_id_for_clerk(subject)
        token = request.headers.get("x-game-session", "")
    if not token:
        raise HTTPException(status_code=401, detail="נדרש SESSION פעיל")
    data = app.state.sessions.read(token)
    if data is None:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
    if not is_dev_mode() and (clerk_player is None or data["player_id"] != clerk_player):
        raise HTTPException(status_code=401, detail="ה-SESSION אינו שייך לחשבון זה")
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
def start_session(request: Request, payload: StartSessionRequest | None = None):
    if is_dev_mode():
        configured = os.getenv("LOCOMISSION_DEV_PLAYER_ID", "")
        try:
            player_id = str(UUID(configured))
        except ValueError:
            raise HTTPException(status_code=503, detail="מזהה שחקן פיתוח אינו מוגדר")
        app.state.game.ensure_dev_player(player_id)
    else:
        subject = clerk_subject(request)
        try:
            player_id = app.state.game.player_for_clerk(
                subject, bool(payload and payload.adult_confirmed)
            )
        except PlayerMustConfirmAdult:
            raise HTTPException(status_code=403, detail="נדרש אישור גיל 18 ומעלה")
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
    try:
        updated = app.state.sessions.report(
            token, report.lat, report.lon, report.accuracy_m,
        )
    except SuspiciousLocation:
        raise HTTPException(
            status_code=403, detail="זוהתה קפיצת מיקום שאינה סבירה"
        )
    if updated is None:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
    # Never include player coordinates in the response.
    return {"accepted": True}


@app.get("/api/leaderboard")
def leaderboard():
    # Durable score data is available even while players are disconnected.
    return {"leaders": app.state.game.leaderboard()}


@app.get("/api/presence")
def presence(auth: tuple[str, dict] = Depends(identity)):
    return app.state.sessions.active_presence()


@app.patch("/api/me")
def edit_profile(change: ProfileChange, auth: tuple[str, dict] = Depends(identity)):
    name = change.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="יש להזין שם תצוגה")
    return app.state.game.set_player_name(auth[1]["player_id"], name)


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


@app.post("/api/missions/{mission_id}/start")
def start_mission(mission_id: UUID, auth: tuple[str, dict] = Depends(identity)):
    _, session = auth
    player_id = session["player_id"]
    day = challenge_day(utc_now())
    loc = require_location(session)
    mission = app.state.game.proof_mission(player_id, mission_id, day, loc)
    if mission is None:
        raise HTTPException(status_code=404, detail="המשימה אינה זמינה")
    if not mission_is_reachable(
        mission["distance_m"], mission["radius_m"], loc["accuracy_m"]
    ):
        raise HTTPException(status_code=403, detail="אתה מחוץ לאזור המשימה")
    new_start = app.state.game.start_mission(player_id, mission_id, day)
    return {"started": True, "first_start_today": new_start}


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
async def submit_proof(
    mission_id: UUID,
    request: Request,
    request_id: UUID = Header(alias="X-Request-ID"),
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
    if not app.state.game.mission_started(player_id, mission_id, day):
        raise HTTPException(status_code=409, detail="יש להתחיל את המשימה לפני הצילום")
    if app.state.game.completed(player_id, mission_id, day):
        return {"accepted": True, "awarded": False, "points": 0,
                "reason_he": "כבר קיבלת נקודות על המשימה היום"}

    # No multipart disk-spooling. Only a bounded, raw image request body.
    from .domain import MAX_PHOTO_BYTES
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=415, detail="נדרשת תמונת JPEG, PNG או WebP")
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(status_code=400, detail="גודל קובץ אינו תקין")
    if declared > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="התמונה גדולה מדי (עד 5MB)")
    source = bytearray()
    async for chunk in request.stream():
        if len(source) + len(chunk) > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="התמונה גדולה מדי (עד 5MB)")
        source.extend(chunk)
    try:
        cleaned = normalize_photo(bytes(source))
    except InvalidPhoto as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        del source

    try:
        first_use, photo_hash = app.state.sessions.claim_photo(token, cleaned)
    except SessionExpired:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
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

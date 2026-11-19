"""LocoMission game API, development slice. Production auth is fail-closed."""
from __future__ import annotations

import os
from threading import BoundedSemaphore
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import redis
import psycopg
from psycopg_pool import PoolTimeout
from fastapi.responses import JSONResponse
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import load_runtime
from .domain import challenge_day, mission_is_reachable, utc_now, validate_coordinate
from .infra import GameStore, PlayerMustConfirmAdult, SessionExpired, Sessions, SuspiciousLocation
from .clerk_auth import clerk_subject, is_dev_mode
from .vision import InvalidPhoto, VisionUnavailable, grade_photo, normalize_photo

bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_runtime()  # Fail before opening a pool on invalid deployment.
    game = GameStore(config.database_url)
    try:
        cache = redis.Redis.from_url(config.redis_url, decode_responses=True)
        try:
            app.state.game = game
            app.state.cache = cache
            app.state.sessions = Sessions(cache)
            app.state.vision_slots = BoundedSemaphore(config.vision_concurrency)
            yield
        finally:
            cache.close()
    finally:
        game.close()


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
        allow_headers=["Authorization", "Content-Type", "X-Game-Session", "X-Request-ID"],
        allow_credentials=False,
    )

@app.exception_handler(redis.exceptions.RedisError)
async def redis_unavailable(_request: Request, _exc: redis.exceptions.RedisError):
    # Never expose connection strings or tokens in an error response.
    return JSONResponse(
        status_code=503, content={"detail": "שירות המשחק אינו זמין זמנית"},
        headers={"Retry-After": "3"},
    )


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def database_unavailable(_request: Request, _exc: Exception):
    return JSONResponse(
        status_code=503, content={"detail": "מסד הנתונים אינו זמין זמנית"},
        headers={"Retry-After": "3"},
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


@app.get("/api/lobby")
def lobby(auth: tuple[str, dict] = Depends(identity)):
    """Mobile snapshot: one request for map, assignments, groups and profile."""
    _, session = auth
    loc = require_location(session)
    player_id = session["player_id"]
    day = challenge_day(utc_now())
    return {
        "day_utc": day.isoformat(),
        "missions": app.state.game.today(player_id, day, loc),
        "groups": app.state.game.nearby_groups(loc),
        "profile": app.state.game.profile(player_id),
        "presence": app.state.sessions.active_presence(),
    }


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
        "missions": app.state.game.group_missions(
            session["player_id"], group_id, challenge_day(utc_now())
        )
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


@app.get("/api/missions/{mission_id}/proof-requests/{request_id}")
def proof_request_status(
    mission_id: UUID,
    request_id: UUID,
    auth: tuple[str, dict] = Depends(identity),
):
    """Recover a verdict after the mobile network dropped the HTTP response.

    Responses are scoped to the current authenticated player and game session.
    Photo bytes are not held or persisted for retrieval.
    """
    token, session = auth
    mission = str(mission_id)
    logical_id = str(request_id)
    cached = app.state.sessions.get_proof_result(token, mission, logical_id)
    if cached is not None:
        return {"state": "completed", **cached}
    awarded = app.state.game.awarded_for_request(
        session["player_id"], mission_id, request_id,
    )
    if awarded is not None:
        return {"state": "completed", **awarded}
    if app.state.sessions.verification_owner(token, mission) == logical_id:
        return {"state": "processing"}
    return {"state": "retry_required"}


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
    # Same logical request may be retried after a lost response, without
    # decoding/uploading again or invoking the paid model twice.
    cached = await run_in_threadpool(
        app.state.sessions.get_proof_result,
        token, str(mission_id), str(request_id),
    )
    if cached is not None:
        return cached
    persisted = await run_in_threadpool(
        app.state.game.awarded_for_request,
        player_id, mission_id, request_id,
    )
    if persisted is not None:
        return persisted
    location = require_location(session)

    mission = await run_in_threadpool(
        app.state.game.proof_mission, player_id, mission_id, day, location,
    )
    if mission is None:
        raise HTTPException(status_code=404, detail="המשימה אינה זמינה היום")
    if not mission_is_reachable(
        mission["distance_m"], mission["radius_m"], location["accuracy_m"],
    ):
        raise HTTPException(status_code=403, detail="אינך בתחום המשימה")
    if not await run_in_threadpool(
        app.state.game.mission_started, player_id, mission_id, day,
    ):
        raise HTTPException(status_code=409, detail="יש להתחיל את המשימה לפני הצילום")
    if await run_in_threadpool(
        app.state.game.completed, player_id, mission_id, day,
    ):
        return {"accepted": True, "awarded": False, "points": 0,
                "reason_he": "כבר קיבלת נקודות על המשימה היום"}

    # Bounded, raw image body only. Multipart spooling is intentionally disabled.
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
        cleaned = await run_in_threadpool(normalize_photo, bytes(source))
    except InvalidPhoto as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        del source

    # Reserve the mission before any billed OpenAI request. Redis serializes
    # concurrent submissions from the same player/session/mission.
    lease_id = uuid4().hex
    try:
        acquired = await run_in_threadpool(
            app.state.sessions.claim_verification,
            token, player_id, str(mission_id), str(request_id), lease_id,
        )
    except SessionExpired:
        raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
    if not acquired:
        raise HTTPException(status_code=409, detail="כבר מתבצע אימות למשימה זו")

    photo_hash = None
    first_use = False
    finalized = False
    try:
        try:
            first_use, photo_hash = await run_in_threadpool(
                app.state.sessions.claim_photo, token, cleaned,
                str(mission_id) + ':' + str(request_id),
            )
        except SessionExpired:
            raise HTTPException(status_code=401, detail="ה-SESSION הסתיים")
        if not first_use:
            raise HTTPException(
                status_code=409, detail="התמונה כבר נשלחה ב-SESSION הזה",
            )

        # Per-process capacity limit prevents bursts from exhausting the
        # vision thread pool or creating uncontrolled simultaneous API costs.
        # Production multi-worker deployments additionally need central limits.
        if not app.state.vision_slots.acquire(blocking=False):
            raise HTTPException(
                status_code=429, detail="מערכת אימות התמונות עמוסה כרגע",
                headers={"Retry-After": "2"},
            )
        try:
            try:
                verdict = await run_in_threadpool(
                    grade_photo, cleaned, mission["title_he"],
                    mission["instructions_he"], mission["visual_criteria_he"],
                )
            except VisionUnavailable:
                raise HTTPException(
                    status_code=503, detail="אימות התמונה אינו זמין כעת",
                )
        finally:
            app.state.vision_slots.release()

        if not verdict.accepted:
            outcome = {"accepted": False, "awarded": False, "points": 0,
                       "reason_he": verdict.reason_he}
            stored = await run_in_threadpool(
                app.state.sessions.cache_proof_result,
                token, player_id, str(mission_id), str(request_id), outcome,
            )
            if not stored:
                raise HTTPException(
                    status_code=401, detail="ה-SESSION הסתיים בזמן הבדיקה",
                )
            finalized = True
            return outcome

        # Fail closed if the player disconnected or moved during model latency.
        current = await run_in_threadpool(app.state.sessions.read, token)
        if current is None:
            raise HTTPException(
                status_code=401, detail="ה-SESSION הסתיים בזמן הבדיקה",
            )
        latest_location = require_location(current)
        final_mission = await run_in_threadpool(
            app.state.game.proof_mission,
            player_id, mission_id, day, latest_location,
        )
        if final_mission is None or not mission_is_reachable(
            final_mission["distance_m"], final_mission["radius_m"],
            latest_location["accuracy_m"],
        ):
            raise HTTPException(
                status_code=403, detail="המיקום כבר אינו בתחום המשימה",
            )
        result = await run_in_threadpool(
            app.state.game.award, player_id, mission_id, day, request_id,
        )
        outcome = {
            "accepted": True,
            "awarded": result["awarded"],
            "points": result["points"],
            "reason_he": verdict.reason_he if result["awarded"]
                         else "המשימה כבר זוכתה",
        }
        # Cache is an optimization only. If the worker dies after the DB
        # commit, GET status can recover the authoritative DB award.
        await run_in_threadpool(
            app.state.sessions.cache_proof_result,
            token, player_id, str(mission_id), str(request_id), outcome,
        )
        finalized = True
        return outcome
    finally:
        # Upstream timeouts, disconnects and transient database failures
        # permit a retry with the same photo (without double-scoring).
        if first_use and photo_hash and not finalized:
            await run_in_threadpool(
                app.state.sessions.forget_photo, token, photo_hash,
            )
        await run_in_threadpool(
            app.state.sessions.release_verification,
            token, str(mission_id), str(request_id), lease_id,
        )

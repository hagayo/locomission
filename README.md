# LocoMission

**The world is your mission**  
Location-based history facts and missions  
**העולם הוא המשימה שלך!**

A mobile-first, history-driven group game that uses geographical proximity and a photo challenge to connect local history with real-world exploration.

## Current status

This repository contains **the first backend foundation**, not a production-ready Android application. The user-facing client platform has not been finalized. The first slice includes a PostgreSQL/PostGIS schema, domain constraints and tests, plus an API implementation under `api/`.

| Persistent | Temporary |
| --- | --- |
| PostgreSQL: players, curated missions, completions and independent group challenges | Redis: live sessions, last reported location and photo replay fingerprints; image bytes only in request memory |

Design and decisions: [product contract](docs/PRODUCT-CONTRACT.md), [mobile UX](docs/MOBILE-UX.md), [privacy draft](docs/PRIVACY-DRAFT.md).

## Backend quick start (development only)

Requires Python 3.12+, PostgreSQL with PostGIS, and Redis. Do not expose the development identity mode publicly.

1. Create database and apply `db/001_initial.sql` once, e.g. `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/001_initial.sql`.
2. `cd api && python -m venv .venv`, activate it, then `pip install -e ".[test]"`.
3. Copy `api/.env.example` to a private local environment configuration and export those variables to the process. Set `LOCOMISSION_DEV_MODE=true` and `LOCOMISSION_DEV_PLAYER_ID` to a newly generated UUID (only locally).
4. Start `uvicorn locomission.main:app --reload` from `api/`. The server documents its endpoints at `/docs`.
5. `pytest` checks pure domain rules. End-to-end tests against PostgreSQL/Redis/OpenAI are still required.

`OPENAI_API_KEY` is required for actual visual grading. No live model call is made without it. **No historical missions are seeded automatically:** source-supported, human-reviewed content must be added before playable results appear.

## Guardrails

- A SESSION ends after **180 seconds without foreground location reports**, or explicit logout. Account and shared group challenge progress remain.
- Cleanup upper bound **X has not been chosen**. No guarantee about X is implied.
- At most **three curated, approved, nearby missions** are assigned per player per UTC day (a provisional daily reset convention).
- No score on a failed/duplicate verification.
- Image analysis uses **GPT-4o mini vision**, not image generation.
- Authentication, billing, native client, complete privacy policy and production deployment are not yet implemented.

The backend framework is an implementation vehicle for the first slice, not a decision about Android native versus Web/PWA.


## Group mission flow

A group is discoverable near its geographic center, joins are voluntary,
and its mission list is available at `GET /api/groups/{id}/missions`
after joining. A verified photo can complete an active group goal even when
that mission is not one of the player's personal daily three. Shared group
progress is recorded only once per challenge/mission.


## Mobile-first interactive preview (local development only)

With `LOCOMISSION_DEV_MODE=true`, visit `http://localhost:8000/prototype/`
from a mobile browser. It uses the same API and offers the real gameplay
navigation: map -> curated story -> camera/photo -> verification result, plus
voluntary group joining. It requests location **only while the page is visible**
and stops its location watcher when backgrounded. It is a **prototype**, not
a committed PWA or native Android client.

The development preview loads map tiles and Leaflet from external providers.
Choose a mapping provider, review its terms and privacy disclosures, and
configure secure HTTPS before any real-world release. Geolocation on another
device normally requires HTTPS (localhost is treated as a secure context).
No fake curated historical missions are inserted for the preview.


## Clerk and new mobile experience

The next development client lives in [mobile/](mobile/), separate from the
old [prototype/](prototype/). Clerk authenticates real accounts, subject to
configuring your keys locally. The API verifies short-lived Clerk JWTs for
every player request; gameplay SESSION data remains in Redis.

Run the add-on migration `db/002_clerk_game.sql` after 001. Review
`docs/CLERK-SETUP.md` for environment-variable placeholders and the
historical editorial approval process. Three sourced draft mission candidates
are in `db/003_historical_candidates.sql`, **not yet approved**.

## Verification uploads and editor review

Photo proof uploads use a bounded raw JPEG/PNG/WebP body, rather than
multipart form-data. The caller supplies an X-Request-ID UUID header.
The API rejects bodies above 5 MiB and keeps upload processing in
application memory; deployments must independently audit proxy buffering
and OpenAI processor retention before making erasure guarantees.

API request: POST /api/missions/{mission_id}/proof
Headers: Content-Type: image/jpeg; X-Request-ID: UUID; Authorization: Bearer <Clerk session JWT>; X-Game-Session: <gameplay token>.

See docs/EDITORIAL.md to inspect and manually approve the historical
drafts only after verifying sources, safe photo locations and Hebrew copy.
Redis session replacement and location reporting use atomic server-side
operations to prevent a concurrent logout from reviving an ended session.

## Mobile gameplay efficiency and proof single-flight

The client fetches `/api/lobby` once per foreground location refresh instead
of fetching missions, groups, profile and presence separately. Public presence
still uses coarse anonymous cells and is limited to a pilot-sized scan; Redis
reads are batched. Historical mission start state is included in each mission
response, so returning from the map or phone camera does not reset progress.

Only one photo can be processed for the same mission in a live SESSION at a
time. A Redis verification lease with an owner UUID prevents simultaneous
OpenAI calls and is removed on successful completion, failure, logout,
replacement or session expiry. Persisted points remain transactionally
idempotent in PostgreSQL.

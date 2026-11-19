# LocoMission

**The world is your mission**  
Location-based history facts and missions  
**העולם הוא המשימה שלך!**

A mobile-first, history-driven group game that uses geographical proximity and a photo challenge to connect local history with real-world exploration.

## Current status

This repository contains **the first backend foundation**, not a production-ready Android application. The user-facing client platform has not been finalized. The first slice includes a PostgreSQL/PostGIS schema, domain constraints and tests, plus an API implementation under \`api/\`.

| Persistent | Temporary |
| --- | --- |
| PostgreSQL: players, curated missions, completions and independent group challenges | Redis: live sessions, last reported location and photo replay fingerprints; image bytes only in request memory |

Design and decisions: [product contract](docs/PRODUCT-CONTRACT.md), [mobile UX](docs/MOBILE-UX.md), [privacy draft](docs/PRIVACY-DRAFT.md).

## Backend quick start (development only)

Requires Python 3.12+, PostgreSQL with PostGIS, and Redis. Do not expose the development identity mode publicly.

1. Create database and apply \`db/001_initial.sql\` once, e.g. \`psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/001_initial.sql\`.
2. \`cd api && python -m venv .venv\`, activate it, then \`pip install -e ".[test]"\`.
3. Copy \`api/.env.example\` to a private local environment configuration and export those variables to the process. Set \`LOCOMISSION_DEV_MODE=true\` and \`LOCOMISSION_DEV_PLAYER_ID\` to a newly generated UUID (only locally).
4. Start \`uvicorn locomission.main:app --reload\` from \`api/\`. The server documents its endpoints at \`/docs\`.
5. \`pytest\` checks pure domain rules. End-to-end tests against PostgreSQL/Redis/OpenAI are still required.

\`OPENAI_API_KEY\` is required for actual visual grading. No live model call is made without it. **No historical missions are seeded automatically:** source-supported, human-reviewed content must be added before playable results appear.

## Guardrails

- A SESSION ends after **180 seconds without foreground location reports**, or explicit logout. Account and shared group challenge progress remain.
- Cleanup upper bound **X has not been chosen**. No guarantee about X is implied.
- At most **three curated, approved, nearby missions** are assigned per player per UTC day (a provisional daily reset convention).
- No score on a failed/duplicate verification.
- Image analysis uses **GPT-4o mini vision**, not image generation.
- Authentication, billing, native client, complete privacy policy and production deployment are not yet implemented.

The backend framework is an implementation vehicle for the first slice, not a decision about Android native versus Web/PWA.

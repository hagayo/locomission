# Clerk integration and credentials

## What is implemented

The **mobile browser development client** uses the Clerk JavaScript SDK. The
API verifies the **Clerk session JWT** on every protected request and binds the
Clerk `sub` to one durable PostgreSQL `players` row. A second, random,
short-lived LocoMission gameplay SESSION remains in Redis, but its token alone
is insufficient to authorize API access outside development mode.

No user password is sent to or stored by LocoMission. Clerk handles sign-in
methods, password resets, OAuth and token renewal. `CLERK_SECRET_KEY` never
belongs in frontend configuration. Use dedicated Clerk development keys
before production keys.

## Values to fill (not in Git)

- `api/.env` (private): `DATABASE_URL`, `REDIS_URL`,
  `CLERK_SECRET_KEY`, `CLERK_JWT_KEY` (PEM public verification key),
  `CLERK_AUTHORIZED_PARTIES` (exact browser origin, comma-separated),
  `OPENAI_API_KEY`.
- `mobile/.env` (private): `VITE_CLERK_PUBLISHABLE_KEY` and
  `VITE_API_BASE_URL` (only if API and client use separate origins).

No placeholder is accepted as a real signing key. Keep
`LOCOMISSION_DEV_MODE=false` for normal Clerk sign-in. The existing
local-only test mode still exists solely to run integration tests.

## Setup

1. Create a Clerk app and configure email sign-in in the Dashboard.
2. Copy the publishable key to `mobile/.env`. The publishable key is
   expected in frontend code; it is **not** a password.
3. Copy the Backend API secret, JWT verification **public** key and exact
   authorized frontend origins to the API environment.
4. Apply `db/001_initial.sql` then `db/002_clerk_game.sql` on a fresh
   DB; for existing databases apply only migration 002.
5. Run `uvicorn locomission.main:app` from `api/`.
6. Run `npm install` then `npm run dev` from `mobile/`. Vite proxies
   `/api` to the backend during local development.
7. Open the Vite URL on a browser with valid HTTPS/secure context for GPS.
   A remote phone accessing a LAN IP via HTTP will not be able to use
   Geolocation; use a secure development URL.
8. Complete Clerk sign-in; confirm the self-attested 18+ gate; then start
   the foreground gameplay session.

## Trust and lifecycle

- The backend refuses requests without a verified session JWT.
- It does not let a client choose the persisted `clerk_user_id` or player UUID.
- `X-Game-Session` is an additional opaque gameplay token, not a replacement
  for Clerk JWT verification.
- Absent/invalid configuration fails closed.
- The foreground player location exists only in Redis, and session logout
  does not erase permanent score, credits, or group progress.
- `missions_started` changes only on successful idempotent start, once
  per mission/player/day, not on viewing or photo retries.
- 18+ is currently a self-declaration, not verified age; configure stronger
  age controls and privacy/legal review before launch.
- `X` (maximum deletion interval) is **not defined** and not claimed.

## Editorial workflow

Historical candidates are in `db/003_historical_candidates.sql` with
source URLs and **status=draft**. They are not playable until the curator:

1. Checks sources and writes original Hebrew copy.
2. Confirms exact publicly accessible coordinates, safe photography position,
   visual criteria and sensible radius.
3. Explicitly approves the record with `reviewed_by`, `reviewed_at`
   and `status='approved'` in PostgreSQL after personal review.

Do not auto-approve content, use an imaginary reviewer or infer that an
AI-written mission has passed human review.

# LocoMission - Render API + GitHub Pages

This repository is prepared for deployment, but no live service or DNS has been created.

## Architecture

- GitHub Pages serves the built static site at `https://hagayo.github.io/locomission/` only after Pages is enabled and the workflow is run.
- One paid Render Docker Web Service runs FastAPI, without serving the frontend.
- PostgreSQL with PostGIS and Redis are **external** services. No databases are provisioned by the Render Blueprint.
- Clerk authenticates users; OpenAI verifies photographs. Neither service is called in CI.

## Render backend setup

1. Connect the repository to Render and select the root `render.yaml` Blueprint. It declares one paid instance, a health check at `/api/health`, and automatic deploys **off**. The service listens on Render's `$PORT`.
2. Complete these *private* environment variables in the Render dashboard: `DATABASE_URL`, `REDIS_URL`, `CLERK_SECRET_KEY`, `CLERK_JWT_KEY` (public PEM JWT verification key), `CLERK_AUTHORIZED_PARTIES`, and `OPENAI_API_KEY`.
3. The Blueprint sets `LOCOMISSION_ENV=staging`, `LOCOMISSION_DEV_MODE=false`, and `LOCOMISSION_MAX_VISION_CONCURRENCY=2`. These values are intentional for a small staging pilot, not calibrated production capacity.
4. Set `CLERK_AUTHORIZED_PARTIES=https://hagayo.github.io` while using the project Pages site. This is an **origin**, not a URL path; do not append `/locomission/`.
5. Ensure external PostgreSQL/Redis are reachable with suitable TLS and network restrictions. Use a limited app database role for the running service; retain DDL privileges only for explicit migrations.

## Database schema

On a *fresh, empty* PostgreSQL instance with PostGIS available, run migration planning followed by explicit apply from the repository root, using a private `DATABASE_URL`:

```sh
python -m pip install -e ./api
python -m locomission.migrate
python -m locomission.migrate --apply
python -m locomission.migrate --include-drafts --apply
```

The fourth command optionally seeds three historical **drafts**, not approved content. The CLI is also available inside the API Docker image, with SQL files under `/app/db`.

An existing database that already has manually applied tables but lacks `schema_migrations` is **not** automatically adopted. The migration tool fails closed. Take a backup and perform an explicit DBA baseline review. Never drop existing user data to bypass this guard.

Migrations run with checksums, transaction-scoped advisory locking, and no automatic DDL during API startup.

## GitHub Pages frontend

1. In GitHub repository Settings > Pages, select **GitHub Actions** as the publishing source.
2. Set repository Actions **Variables** `VITE_API_BASE_URL` to your actual HTTPS Render service URL and `VITE_CLERK_PUBLISHABLE_KEY` to your Clerk publishable key (`pk_test_...` for staging). These variables are **public** after Vite build. Never put Clerk secret or OpenAI key here.
3. Manually run the `Publish mobile to GitHub Pages` workflow in Actions. It rejects missing/placeholder configuration, runs JS tests and `npm ci`, builds Vite with `/locomission/` as the base URL, and deploys `mobile/dist`.
4. Once it succeeds, the expected frontend URL is `https://hagayo.github.io/locomission/`. HTTPS is needed for browser geolocation.
5. Configure this exact frontend domain and redirect URLs in the Clerk Dashboard. A Clerk **production** instance normally needs a domain you control; a `github.io` subdomain should be treated as a temporary staging arrangement, not a guaranteed production auth setup.

To migrate to a custom domain later: change Pages site settings, Vite base path (`VITE_BASE_PATH` in the workflow), Clerk domain/redirects and API `CLERK_AUTHORIZED_PARTIES` together.

## Security and release checklist

Before inviting real players, verify live Clerk session JWT and `azp` handling, CORS for the frontend origin, 18+ flow, GPS permissions, photo uploads, scoring, and session deletion. Obtain human historical review and decide the deletion SLA `X`. Configure logs, budget caps, TLS, backups, and alerting.

CI runs Python lint and tests, a real PostGIS migration test, Redis load smoke, frontend tests, Docker image build, and container readiness smoke. These tests do **not** establish real-world throughput or live Clerk integration.

## Reuse of the Cinema City Clerk pattern

The verified `sub` from a Clerk JWT is mapped to an internal persisted player ID. Player points and missions refer only to that internal ID, not the external identity. Cinema City additionally demonstrates issuer/JWKS/authorized-party verification and role-specific authorization; the latter is not needed for LocoMission player actions today.

Reference: https://github.com/hagayo/cinema-city/blob/main/src/cinema/auth/clerk.py
Render Blueprint: https://render.com/docs/blueprint-spec
Clerk JS: https://clerk.com/docs/js-frontend/getting-started/quickstart
Vite Pages: https://vite.dev/guide/static-deploy

## Phase 4 - content and pilot gating

For five additional sourced but unapproved mission drafts, use the separate insert-only catalog command described in [docs/PILOT-RELEASE.md](PILOT-RELEASE.md). Do not set `approved` automatically. Real Clerk JWT verification and a potentially billable OpenAI synthetic image smoke test are explicit **operator-only** commands, not CI jobs. The human evidence gate remains blocked until the physical deletion deadline X, safe public photography and all legal/third-party processing decisions are made.

GitHub Pages serves public static assets even in a staging setup. Keep test identities behind Clerk's staging sign-up restrictions, and never embed private operator evidence or credentials in Pages artifacts.

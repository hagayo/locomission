# LocoMission - fault tolerance and load testing

## Contract and outcome recovery

The player uploads a raw JPEG/PNG/WebP image (up to 5 MiB) to
`POST /api/missions/{mission_id}/proof`, using one persistent
`X-Request-ID` UUID for that photo attempt. Bearer Clerk JWT and
`X-Game-Session` are still required outside development mode.

After a lost HTTP reply, the same authenticated player calls:

`GET /api/missions/{mission_id}/proof-requests/{request_id}`

The JSON response contains one of:
- `{"state":"processing"}`: same request has an active lease. Do not resubmit.
- `{"state":"completed",...}`: return the original verified verdict from
  ephemeral Redis, or a committed score recovered from PostgreSQL.
- `{"state":"retry_required"}`: no recorded outcome and no active lease;
  retry the *same request ID* with the photo still in phone memory.

The client reuses its existing "mission action" button for recovery. No
additional screen or permanent on-device image store is introduced.

## Server consistency guarantees

- **PostgreSQL is authoritative for points.** The UNIQUE player/mission/day
  and player/request constraints, in one transaction with player totals,
  prevent duplicate score awards under concurrent requests.
- **Redis single flight** holds a mission-scoped in-flight lease for at most
  90 seconds; even regular GPS location reports cannot extend the deadline.
  An individual processing attempt has a server-generated lease ID so a stale
  attempt cannot release a newer lease.
- **Temporary photo hash** is bound to the logical request UUID, permitting
  the same image to be retried after a lost worker, but not silently credited
  under a different request ID.
- **Redis verdict cache** contains only accepted/awarded/points/reason,
  never photos or GPS. It expires no later than the active gameplay SESSION
  and is deleted on explicit logout or session replacement.
- **Fail closed:** after an active session ends during the OpenAI call,
  the API rechecks session validity and location before it awards points.
- **Backpressure:** a process-local bounded semaphore caps simultaneous
  model calls (default 4, `LOCOMISSION_MAX_VISION_CONCURRENCY` 1-32).
  Excess concurrent requests receive HTTP 429 with Retry-After.
- Redis connectivity failure returns HTTP 503; database operational failures
  return 503; no database URLs, JWTs, photo bytes or locations are logged by
  these exception handlers.

**Limitations:** there is no durable photo queue. If a server process dies
before PostgreSQL records a successful result or Redis caches a rejection,
the model call may have been billed but its result lost; the same request ID
can be retried and may incur another model charge. A network disconnect by
itself does not guarantee that the server continued processing. A successful
PostgreSQL commit, however, can be recovered without a second model call.
The global concurrent OpenAI limit across several API workers/deployments
would need distributed rate limiting. Client-held photo bytes do not survive
a page reload. No retention SLA X has been selected.

## Automated fault-injection coverage

The CI integration suite runs against real disposable PostgreSQL/PostGIS
and Redis, while OpenAI responses are mocked. Scenarios include:

1. Simulated OpenAI timeout, retry using identical photo/request ID.
2. Redis outage after SQL COMMIT, HTTP 503, successful GET from PostgreSQL.
3. Explicit player logout while the model response is pending: no score.
4. Redis lease contention and stale owner release, plus bounded lease expiry.
5. Many concurrent scoring attempts: one durable award only.
6. Session cleanup: temporary verdicts/locks/locations are deleted.
7. Frontend lost-response states: completed, processing, retry_required,
   network error. No duplicate POST when the result is already known.
8. A saturated vision slot returns HTTP 429, releasing the photo claim.

## Reproducible load runs

For concurrent Redis presence/session operations, from `api/` with the
isolated `LOCOMISSION_TEST_DATABASE_URL` and `REDIS_URL` configured:

```bash
python -m pytest -q -s tests/test_load.py
```

It creates 80 synthetic sessions, issues 80 location reports, 600 concurrent
reads and 24 presence scans using up to 16 workers, plus a 32-way race for a
single verification lease. It prints measured RPS and p50/p95/p99 latency
for the actual CI runner. It does not use real users, photo uploads or model
calls. It must run only against an isolated Redis instance.

For read-only HTTP load benchmarking from `api/`:

```bash
python -m load.benchmark --url http://127.0.0.1:8000/api/health \
  --workers 12 --requests 240
```

For a local authenticated `/api/lobby` probe, set
`LOCOMISSION_LOAD_CLERK_JWT` and `LOCOMISSION_LOAD_GAME_SESSION`
locally, then add `--auth-from-env`. Values never appear in report output.

This tool allows only GET on `/api/health`, `/api/lobby` or
`/api/presence`, and rejects remote hosts unless expressly permitted.
No OpenAI call or write is performed by the benchmark.

**Interpretation:** CI load metrics are synthetic measurements, not a
production capacity guarantee. No arbitrary p95/SLO pass threshold is
claimed. To establish targets, repeat under realistic network, database
size, host sizing and concurrent player distribution; measure at least
error rate, p95 latency, throughput and database/Redis utilization.

No deployment, live Clerk sign-in, billing, or live OpenAI run occurs in CI.

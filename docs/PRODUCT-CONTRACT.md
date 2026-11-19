# LocoMission - product and data contract (v0.1)

## Identity and scope

- Name: **LocoMission**.
- EN slogan: **The world is your mission**.
- Description: **Location-based history facts and missions**.
- HE slogan: **העולם הוא המשימה שלך!**
- The game runs **on mobile in the foreground**. No background location tracking.
- UX path: discover a mission -> read its historical fact -> execute/photo -> result.
- The map is the primary space. One primary thumb-accessible action per step.
- All customer-facing Hebrew uses RTL, including errors, dialogs and camera state.
- Groups are independent durable challenges, not an extension of an individual's session.
- Competition, global ranking, time modes, and payments are roadmap items, not silently implemented here.
- A join-by-code option is not part of this phase.

## Three distinct lifecycles

| Entity | Source of truth | Retention |
| --- | --- | --- |
| Player account | PostgreSQL | Durable profile, points and completion count |
| Individual SESSION | Redis | Temporary identity binding, most recent foreground location, presence and photo-fingerprint keys; cleaned on logout or 180 seconds without location report |
| Group challenge | PostgreSQL | Durable membership and shared progress; survives a member disconnect |

No temporary coordinates, photographs, image hashes, session tokens, or debug logs of these belong in PostgreSQL. Photo bytes in the first API slice exist **in request memory only** during validation, conversion and OpenAI inference. They are not persisted to disk, object storage or PostgreSQL. A durable temporary photo-store adapter may be added only with a deletion implementation bound to session cleanup.

### Deletion SLA: X is undefined

Session ending triggers immediate *logical invalidation* and an attempt to delete all temporary Redis keys. Redis TTL provides a secondary bound on logical accessibility if a process dies. **This does not establish the maximum physical erasure time X**. X requires a product decision, infrastructure-specific testing, and any required backup/log/processor retention policy. Do not state that X is satisfied before that work is complete. No cleanup interval is to be confused with X.

### Identity trust boundary

A production identity provider has **not** been selected. All player/session creation is therefore **disabled in non-development mode**. A single explicitly configured UUID is permitted in local development for API exploration. Never accept arbitrary client-submitted player IDs as authentication.

## Historical mission contract

Each mission has: a canonical id and slug; original Hebrew fact and directions; named historical source and URL; an approved human reviewer and review time; an indexed PostGIS point and meter radius; visible photo acceptance criteria; integer points; status; and content tier.

Only human-approved missions are eligible for assignment. Up to three slots per player per UTC calendar day are allocated transactionally. Discovery covers approved missions within 10 km, while **starting and verifying** still require the mission-specific geofence and adequate GPS accuracy. If fewer than three missions are available at first discovery, only those slots are occupied; visiting another area later that day may fill the remaining slots without replacing previous assignments. Daily timezone is **provisional**; local-time reset rules are a future product decision.

Image validation has two independent gates:

1. The latest foreground location report must pass a server-side PostGIS distance check and have reported accuracy no worse than the mission radius.
2. Only then, the sanitized image is checked against the curated visual criteria by **OpenAI GPT-4o mini with vision**. The model is not an historical fact-checker or a trusted source of instructions.

Do not award points on OpenAI timeout, model failure, invalid JSON, unapproved mission, missing location, or duplicate completion. The database enforces at most one scored completion per player/mission/day. Session-only SHA-256 hashes help detect identical photo resubmissions without keeping image-derived data permanently. They cannot prevent edited-image replays or all GPS spoofing.

## Release blockers

- Real authentication, abuse controls and camera/location permission UX.
- Session cleanup test under failures and explicit X.
- Real, sourced and manually approved missions. No invented historical facts in production.
- Privacy/legal review, minors/consent decisions, third-party processing, deletion requests.
- Premium purchases: no real charging or claimed entitlements before payment design.
- Android native versus web/PWA still open. This backend does not settle it.


### Movement plausibility in the first API slice

Server-side checks also flag implausibly fast jumps between the most recent
two foreground reports (a 100 m/s baseline with 100 m and GPS-accuracy slack).
This is a lightweight anti-teleport heuristic, not proof of authentic GPS.
It does not block a modified device that forges a gradual route. Thresholds
need field testing to avoid false positives before production use.

Daily discovery excludes missions the player has already completed on earlier
days; if too few curated local missions are left, fewer than three are shown.

### Nearby anonymous presence overlay

The map requests active player presence within 1 km of the viewing player's
last foreground location, excluding the viewing player. Redis GEO indexes
store hashed session identifiers and approximately 100m-grid locations,
partitioned into 60-second buckets with a finite TTL. The API checks the
underlying live session, owner binding and exact distance before exposing a
cell count, never a player identity or precise location. Explicit logout or
session replacement removes indexed membership. Expired keys become invisible
immediately on session expiry; GEO bucket TTL provides eventual index cleanup,
not a defined physical deletion SLA X. Redis GEO cannot represent polar caps;
valid gameplay GPS there does not create map presence markers.

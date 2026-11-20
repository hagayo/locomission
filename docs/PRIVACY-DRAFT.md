# Privacy disclosure - draft for legal review, NOT a published policy

LocoMission asks for foreground device location while the player is actively playing. It does not request or need background location access. The latest location is held temporarily in Redis for proximity checks and presence, rather than in the persistent account database.

To verify a photo mission, the selected photograph is transmitted to **OpenAI for visual analysis using GPT-4o mini with vision capabilities**. The application first strips available metadata and resizes the image. In the initial implementation, image bytes are kept only in server request memory and are not retained by LocoMission after evaluation; independent OpenAI processing and retention terms must be reviewed before release.

The player's account, point total and successful mission history persist in PostgreSQL. A logout or 180 seconds without a location report ends the gameplay session and invalidates temporary session records. The maximum cleanup time **X is not yet defined** and must be finalized before these claims are published as a service-level guarantee.

This draft does not yet cover age restrictions, third-party processor terms, security measures, legal bases, billing, data subject rights or jurisdiction-specific legal requirements. Do not ship it as a finished privacy policy.

## Implementation update for technical review

The current API accepts bounded raw image streams, not multipart uploads.
Application code does not spool images to temporary disk via UploadFile.
Reverse proxies, transport infrastructure and OpenAI retention must be
assessed separately. This is not a guarantee that all processing layers
delete data within X; that maximum period remains undefined.


## Result-recovery metadata

The Redis session may temporarily hold a minimal photo-verification verdict
(accepted/rejected, points, short Hebrew reason) indexed by a random request
UUID. It contains no image bytes, exact coordinates or EXIF. The result cache
is deleted with the session and follows its TTL. Awarded points and mission
completion records remain durable in PostgreSQL.

If a server worker crashes before it records a rejected verdict, a subsequent
retry may need a new model call. This is not a promise of exactly-once
OpenAI billing. The deletion SLA X remains undecided.

## Anti-abuse counters (Stage 3)

Redis holds separate short-lived hashed player/Clerk-subject rate-limit keys and counters for gameplay requests, SESSION creations and chargeable photo-verification attempts. They contain no raw account ID, photo bytes, coordinates, EXIF or token. Limits and TTLs are configurable; initial pilot defaults are specified in `docs/RESILIENCE.md`.

Unlike gameplay session presence and proof data, these counters deliberately remain across logout/SESSION replacement until expiry. Otherwise any user could evade model-cost controls by reconnecting. This is a separate temporary security-data category requiring explicit pre-launch privacy review and a product retention policy. The physical-deletion deadline X remains undecided.


## Pilot release review gate (Phase 4)

The CLI `uv run --project api --locked python -m locomission.pilot --decisions docs/PILOT-GATES.example.json` deliberately returns BLOCKED until an operator supplies evidence of age/consent review, a legally reviewed privacy notice, safe public photo sites, live-provider checks, storage/processor retention, log and backup deletion, real-device accessibility and an explicit **X** backed by physical-erasure evidence. It cannot certify legal compliance; a qualified human must review the underlying materials.

The OpenAI model smoke test is opt-in and potentially billable, uses a generated graphic with no person/location data, and is not part of CI. Real player photographs are never required for that API-shape test. Staging evaluation with actual site photos additionally needs consent, safe collection and documented processing conditions. The pilot catalog importer is draft-only.

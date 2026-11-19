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

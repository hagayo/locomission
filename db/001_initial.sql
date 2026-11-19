-- LocoMission initial PostgreSQL + PostGIS schema.
-- Migration is forward-only. Execute once on a fresh database.
-- No live location, proof image, EXIF or session token is stored in PostgreSQL.
BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE players (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    total_points bigint NOT NULL DEFAULT 0 CHECK (total_points >= 0),
    completed_missions integer NOT NULL DEFAULT 0 CHECK (completed_missions >= 0)
);

CREATE TABLE missions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    title_he text NOT NULL,
    historical_fact_he text NOT NULL,
    instructions_he text NOT NULL,
    visual_criteria_he text[] NOT NULL CHECK (cardinality(visual_criteria_he) > 0),
    source_title text NOT NULL,
    source_url text NOT NULL,
    review_note text,
    reviewed_by text,
    reviewed_at timestamptz,
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','approved','retired')),
    location geography(Point,4326) NOT NULL,
    radius_m integer NOT NULL CHECK (radius_m BETWEEN 10 AND 10000),
    points integer NOT NULL CHECK (points BETWEEN 1 AND 10000),
    content_tier text NOT NULL DEFAULT 'free' CHECK (content_tier IN ('free','premium')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approved_requires_review CHECK (
        status <> 'approved' OR
        (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);
CREATE INDEX missions_location_gix ON missions USING gist (location);
CREATE INDEX missions_status_idx ON missions (status);

-- At most three stable assigned slots for each player's UTC day.
-- Slots are unique even under concurrent requests.
CREATE TABLE daily_assignments (
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    challenge_day date NOT NULL,
    slot smallint NOT NULL CHECK (slot BETWEEN 1 AND 3),
    mission_id uuid NOT NULL REFERENCES missions(id) ON DELETE RESTRICT,
    PRIMARY KEY (player_id,challenge_day,slot),
    UNIQUE (player_id,challenge_day,mission_id)
);

-- No image hash is retained here: image-derived fingerprints live in Redis
-- and are erased with the session.
CREATE TABLE completions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    mission_id uuid NOT NULL REFERENCES missions(id) ON DELETE RESTRICT,
    challenge_day date NOT NULL,
    request_id uuid NOT NULL,
    points_awarded integer NOT NULL CHECK (points_awarded >= 0),
    completed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (player_id,mission_id,challenge_day),
    UNIQUE (player_id,request_id)
);
CREATE INDEX completions_player_idx ON completions(player_id,completed_at DESC);

-- A group challenge is durable and has its own lifecycle.
-- It is deliberately not linked to an individual Redis session.
CREATE TABLE group_challenges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title_he text NOT NULL,
    description_he text NOT NULL DEFAULT '',
    center geography(Point,4326) NOT NULL,
    discovery_radius_m integer NOT NULL CHECK (discovery_radius_m > 0),
    opens_at timestamptz NOT NULL,
    closes_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'published'
        CHECK (status IN ('draft','published','closed')),
    CHECK (closes_at > opens_at)
);
CREATE INDEX group_challenges_center_gix ON group_challenges USING gist(center);
CREATE TABLE group_members (
    challenge_id uuid NOT NULL REFERENCES group_challenges(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (challenge_id,player_id)
);
CREATE TABLE group_missions (
    challenge_id uuid NOT NULL REFERENCES group_challenges(id) ON DELETE CASCADE,
    mission_id uuid NOT NULL REFERENCES missions(id) ON DELETE RESTRICT,
    PRIMARY KEY (challenge_id,mission_id)
);
CREATE TABLE group_progress (
    challenge_id uuid NOT NULL,
    mission_id uuid NOT NULL,
    completed_by uuid NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (challenge_id,mission_id),
    FOREIGN KEY (challenge_id,mission_id)
        REFERENCES group_missions(challenge_id,mission_id)
        ON DELETE CASCADE
);
-- Premium products and transactions are intentionally NOT implemented
-- before billing, refunds, entitlements and store policy are defined.
COMMIT;

-- 002: durable account linkage and game participation state.
-- Apply after 001_initial.sql. No passwords, sessions, locations or photo proofs here.
BEGIN;
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS clerk_user_id text UNIQUE,
    ADD COLUMN IF NOT EXISTS adult_confirmed_at timestamptz,
    ADD COLUMN IF NOT EXISTS missions_started integer NOT NULL DEFAULT 0
       CHECK (missions_started >= 0),
    ADD COLUMN IF NOT EXISTS credits_balance bigint NOT NULL DEFAULT 0
       CHECK (credits_balance >= 0),
    ADD COLUMN IF NOT EXISTS plan_code text NOT NULL DEFAULT 'free'
       CHECK (plan_code IN ('free','premium')),
    ADD COLUMN IF NOT EXISTS avatar_url text,
    ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}'::jsonb
       CHECK (jsonb_typeof(settings) = 'object');
ALTER TABLE players ADD CONSTRAINT clerk_user_id_format
    CHECK (clerk_user_id IS NULL OR
           (clerk_user_id LIKE 'user_%' AND length(clerk_user_id) <= 128));

CREATE TABLE IF NOT EXISTS mission_starts (
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    mission_id uuid NOT NULL REFERENCES missions(id) ON DELETE RESTRICT,
    challenge_day date NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(player_id,mission_id,challenge_day)
);

CREATE INDEX IF NOT EXISTS players_score_idx
    ON players(total_points DESC, id);
COMMIT;

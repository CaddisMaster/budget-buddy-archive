-- Phase: Money agent — cached weekly investigation runs (v10.10.0)
--
-- Budget Buddy's ninth AI feature and its first AUTONOMOUS tool-use: once a
-- week (inside the digest send, or on demand from the dashboard card) a
-- read-only agent investigates the user's week through the ask-tools and
-- writes up at most 3 evidence-cited findings. The app's tools produce every
-- figure — `content` holds only the narrative JSON:
--     {"summary": "...", "findings": [{"title", "detail", "evidence"}, ...]}
--
-- One row per (user, week): period_start is the week's Sunday (the same
-- boundary as users.last_digest_sent_on), and the UNIQUE constraint lets the
-- runner upsert via ON CONFLICT … DO UPDATE (the insights pattern, weekly-
-- keyed instead of monthly).
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image,
-- so the new code can read `agent_runs` on the very first dashboard load.

BEGIN;

CREATE TABLE IF NOT EXISTS public.agent_runs (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period_start date NOT NULL,     -- the week's Sunday
    content text NOT NULL,          -- JSON {"summary": "...", "findings": [...]}
    model varchar(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_agent_run_user_period UNIQUE (user_id, period_start)
);

COMMIT;

-- Phase: Goal Coach — cached AI narration of savings-goal pace (v10.8.0 bundle)
--
-- Budget Buddy's eighth AI feature and the twin of Insight/Forecast, but pointed
-- at the Goals page. The app already computes every figure a goal needs
-- (build_goals_view + compute_goal_projection: saved, percent, on_track,
-- projected_date, required_per_month, …); an on-demand "Generate coaching"
-- button calls Claude once to narrate that pace into a recap + a nudge or two,
-- and the result is cached here so it shows instantly thereafter (Regenerate to
-- refresh). Claude only writes prose — so `content` holds just the narrative JSON.
--
-- Keyed one row per (user, month) so Generate/Regenerate upserts via
-- ON CONFLICT … DO UPDATE (the insights/forecasts pattern). Goals aren't
-- inherently month-scoped, but a monthly snapshot matches the twins' cadence and
-- reuses their exact load/upsert plumbing ("this month's coaching for your goals").
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code can read `goal_coach` on the very first /goals load.

BEGIN;

CREATE TABLE IF NOT EXISTS public.goal_coach (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,          -- JSON {"summary": "...", "tips": [...]}
    model varchar(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_goal_coach_user_period UNIQUE (user_id, year, month)
);

COMMIT;

-- Phase: Forecast — month-ahead AI projection, cached per user per month (v10.2.0)
--
-- Budget Buddy's third AI feature and the capstone of the AI arc: where Insight
-- (sql/15) recaps the month behind you, Forecast looks forward — an on-demand
-- "Generate forecast" button calls Claude once to narrate a deterministic
-- month-ahead projection (projected net, on-pace-vs-budget, the next scheduled
-- item). The app computes every figure deterministically (compute_forecast); the
-- model only writes prose, so `content` holds just that narrative JSON.
--
-- One row per (user, month): the UNIQUE constraint lets Generate/Regenerate
-- upsert via ON CONFLICT … DO UPDATE (the budgets.set_budget / insights pattern).
-- Stores only the narrative — every number is recomputed each dashboard load.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code can read `forecasts` on the very first dashboard load.

BEGIN;

CREATE TABLE IF NOT EXISTS public.forecasts (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,          -- JSON {"summary": "...", "tips": [...]}
    model varchar(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_forecast_user_period UNIQUE (user_id, year, month)
);

COMMIT;

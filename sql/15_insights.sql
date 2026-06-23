-- Phase: Insight — monthly AI digest, cached per user per month (v10.1.0)
--
-- Budget Buddy's second AI feature. The dashboard computes plenty of figures but
-- never explains them. An on-demand "Generate insight" button calls Claude once
-- and the result is cached here so it shows instantly thereafter (Regenerate to
-- refresh). The app computes every number deterministically — Claude only writes
-- the plain-English recap + tips — so `content` holds just that narrative JSON.
--
-- One row per (user, month): the UNIQUE constraint lets Generate/Regenerate
-- upsert via ON CONFLICT … DO UPDATE (the budgets.set_budget pattern).
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code can read `insights` on the very first dashboard load.

BEGIN;

CREATE TABLE IF NOT EXISTS public.insights (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,          -- JSON {"summary": "...", "tips": [...]}
    model varchar(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_insight_user_period UNIQUE (user_id, year, month)
);

COMMIT;

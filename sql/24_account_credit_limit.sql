-- Phase: Credit limits — per-card utilization (v10.10.0)
--
-- A credit-card balance alone doesn't say how close to the edge you are.
-- Each account gets an optional credit_limit (meaningful for Credit Card
-- accounts — a stored limit on any other type is simply ignored, so a value
-- survives a type flip); NULL = not set, so nothing changes until a limit
-- is entered. Utilization % and available credit are derived at read time,
-- never stored. Purely additive.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image,
-- so the new code can read `credit_limit` on the very first load.

BEGIN;

ALTER TABLE public.account
    ADD COLUMN IF NOT EXISTS credit_limit numeric(10,2);

COMMIT;

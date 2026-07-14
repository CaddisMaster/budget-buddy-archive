-- Phase: APR on credit cards — monthly interest cost (v10.15.0)
--
-- A card balance says what you owe; the APR says what carrying it costs.
-- Each account gets an optional apr percent (meaningful for Credit Card
-- accounts — a stored apr on any other type is simply ignored, so a value
-- survives a type flip); NULL = not set, so nothing changes until an APR
-- is entered. The ~monthly interest (debt × apr/100/12) is derived at read
-- time, never stored. The app caps input at 100. Purely additive.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image,
-- so the new code can read `apr` on the very first load.

BEGIN;

ALTER TABLE public.account
    ADD COLUMN IF NOT EXISTS apr numeric(5,2);

COMMIT;

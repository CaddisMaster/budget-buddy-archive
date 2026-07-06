-- Phase: Budget history — append-only log of budget changes (v10.9.0 bundle)
--
-- The budgets table upserts ONE row per (user, category) in place, so changing
-- an amount erases the old one forever — which is why the budget report grades
-- past months against the CURRENT amount. This log records every set / clear /
-- review-apply from day one, so a future report can grade each month against
-- the amount actually in effect then. Nothing reads it yet (writer first —
-- history is the one thing that can't be backfilled later).
--
-- amount NULL = the budget was cleared (reverted to suggested).
-- category FK is CASCADE, not RESTRICT: a RESTRICT here would permanently
-- block deleting any category that ever had a budget, and a deleted
-- category's budget history has no referent anyway.
--
-- Seed: one row per existing budget stamped with its created_at (created_at
-- survives upserts — only amount changes on conflict — so it reads honestly
-- as "in effect since at least then").
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image.

BEGIN;

CREATE TABLE IF NOT EXISTS public.budget_history (
    id SERIAL PRIMARY KEY,
    category_id integer NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    amount numeric(10,2),
    changed_at timestamp without time zone NOT NULL DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO budget_history (category_id, amount, changed_at, user_id)
SELECT category_id, amount, COALESCE(created_at, now()), user_id FROM budgets;

COMMIT;

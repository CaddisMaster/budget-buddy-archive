-- Phase: Balance check-in — per-account reconciliation (v10.9.0 bundle)
--
-- The quiet killer of manual-entry trackers is drift: once the app's balance
-- stops matching the bank's, trust dies. Check-in is the fix — you type what
-- the bank actually says, the app inserts the adjustment transaction that
-- closes the gap (is_adjustment: counts toward balance, excluded from
-- analytics), and stamps this date so staleness is visible on /accounts.
-- NULL = never checked in. Purely additive.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image,
-- so the new code can read `last_checked_in` on the very first /accounts load.

BEGIN;

ALTER TABLE public.account ADD COLUMN IF NOT EXISTS last_checked_in date;

COMMIT;

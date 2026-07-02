-- Phase: Weekly Email Digest (v10.8.0)
--
-- Budget Buddy's first PUSH feature — a scheduled Sunday-evening email recapping
-- the month-to-date (spend vs budget, net position) and the week ahead. Sending
-- is per-user opt-in and requires an email address, both set on the Profile page.
--
-- Three columns on `users` (no new table — a simple per-user opt-in + one
-- idempotency marker, so the existing conftest teardown is unchanged):
--   email                where the digest is sent (null = not set)
--   weekly_digest        the opt-in flag (existing users default OUT)
--   last_digest_sent_on  the date the last digest went out — the runner sends a
--                        given week at most once per user even across restarts /
--                        scheduler misfires
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code can read these columns on the very first request/scheduler run.

BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS email TEXT,
    ADD COLUMN IF NOT EXISTS weekly_digest BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS last_digest_sent_on DATE;

COMMIT;

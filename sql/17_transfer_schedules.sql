-- Phase: Auto-Transfer — recurring transfers on the Transfer tab (v10.4.0)
--
-- The transfer-tab twin of v10 Scheduled (sql/14). Where a `schedules` row
-- materializes a one-sided income/expense transaction on each due date, a
-- `transfer_schedules` row materializes a PAIRED transfer — a linked expense leg
-- out of `from_account_id` and income leg into `to_account_id`, sharing one
-- transfer_group_id (nextval('transfer_group_seq')), both is_transfer=true — so
-- it moves both balances and stays out of income/expense analytics, exactly like
-- a hand-entered transfer.
--
-- A transfer has no category and needs TWO accounts, so this is a separate table
-- (not a `kind` column on `schedules`) — the same separate-table choice as
-- insights vs forecasts. run_due_transfers() (blueprints/transfers.py) posts each
-- due occurrence going forward (no back-fill) and advances next_due, reusing
-- compute_next_due()/compute_initial_semimonthly_due() and all six frequencies.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code can read `transfer_schedules` on the very first /transfers load.

BEGIN;

CREATE TABLE IF NOT EXISTS public.transfer_schedules (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    from_account_id integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    to_account_id   integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    frequency character varying(20) NOT NULL,
    anchor_day smallint,
    second_day smallint,
    next_due date NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

COMMIT;

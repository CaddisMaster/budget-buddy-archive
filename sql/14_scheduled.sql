-- Phase: Scheduled — recurring income & expenses as first-class schedules (v10.0)
--
-- Recurring transactions stop being a flag on a ledger row. A "recurring"
-- transaction used to BE a real transaction dated at its anchor (first pay day),
-- which dropped an unwanted back-dated entry into History on first-time setup.
-- Now a schedule is a separate template that NEVER appears in History; it only
-- generates a plain transaction on each due date, going forward.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image, so
-- the new code can read `schedules` and the old anchor rows are already gone.

BEGIN;

-- 1. The schedule template. amount/type/category/account mirror a transaction;
--    frequency + anchor_day/second_day (semimonthly) + next_due drive generation.
CREATE TABLE IF NOT EXISTS public.schedules (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    category_id integer REFERENCES categories(id) ON DELETE RESTRICT,
    account_id integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    transaction_type varchar(10) NOT NULL DEFAULT 'expense'
        CHECK (transaction_type IN ('expense', 'income')),
    frequency varchar(20) NOT NULL,
    anchor_day smallint,   -- first pay day of month (semimonthly); NULL otherwise
    second_day smallint,   -- second pay day of month (semimonthly only)
    next_due date NOT NULL,
    is_active boolean NOT NULL DEFAULT true,  -- reserved for future pause; no UI in v10
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- 2. Migrate existing recurring templates into schedules. The anchor day comes
--    from the template's own transaction_date; recur_second_day carries over for
--    semimonthly. next_due is already the next occurrence.
INSERT INTO schedules (amount, description, category_id, account_id,
    transaction_type, frequency, anchor_day, second_day, next_due, is_active, user_id)
SELECT amount, description, category_id, account_id,
    transaction_type, frequency,
    EXTRACT(DAY FROM transaction_date)::smallint AS anchor_day,
    recur_second_day, next_due, true, user_id
FROM transactions
WHERE is_recurring = true;

-- 3. Delete the anchor template rows from the ledger (user-approved; lossy —
--    past balances drop by these amounts). Generated occurrences (is_recurring
--    = false copies) are untouched and remain in History.
DELETE FROM transactions WHERE is_recurring = true;

COMMIT;

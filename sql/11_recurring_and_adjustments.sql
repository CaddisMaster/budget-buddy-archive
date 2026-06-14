-- Phase: expanded recurring frequencies + analytics adjustment flag
-- 1. Widen frequency so longer values like 'semimonthly' fit (was VARCHAR(10))
ALTER TABLE transactions
    ALTER COLUMN frequency TYPE VARCHAR(20);

-- 2. Second pay day of month, used only by 'semimonthly' recurring transactions.
--    The first pay day is the day-of-month of the template row's transaction_date.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS recur_second_day SMALLINT;

-- 3. Mark setup/opening-balance/correction entries. These still count toward
--    account balances but are excluded from analytics charts so they don't
--    distort income/spending trends.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS is_adjustment BOOLEAN NOT NULL DEFAULT false;

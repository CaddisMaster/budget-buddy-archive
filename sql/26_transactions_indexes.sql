-- v10.11 — indexes on transactions (deferred from the 2026-07-07 hardening
-- batch, riding the Bulk edit box). Every page filters transactions by
-- user_id, and the ON DELETE RESTRICT checks on category/account delete scan
-- the table without the FK indexes; transfer_group_id is looked up on every
-- transfer edit/delete. Invisible at solo scale, but cheap and correct.
--
-- Apply by hand to an existing DB (pg_dump first). New DBs get these from
-- schema.sql.

CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category_id ON transactions (category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions (account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_transfer_group_id ON transactions (transfer_group_id);

-- Phase 16: Add user_id to all data tables for multi-user data isolation

-- Add user_id columns (nullable first so existing rows don't violate NOT NULL)
ALTER TABLE transactions ADD COLUMN user_id INTEGER;
ALTER TABLE categories ADD COLUMN user_id INTEGER;
ALTER TABLE account ADD COLUMN user_id INTEGER;
ALTER TABLE budgets ADD COLUMN user_id INTEGER;

-- Assign all existing data to the admin user
UPDATE transactions SET user_id = (SELECT id FROM users WHERE is_admin = true ORDER BY id LIMIT 1);
UPDATE categories  SET user_id = (SELECT id FROM users WHERE is_admin = true ORDER BY id LIMIT 1);
UPDATE account     SET user_id = (SELECT id FROM users WHERE is_admin = true ORDER BY id LIMIT 1);
UPDATE budgets     SET user_id = (SELECT id FROM users WHERE is_admin = true ORDER BY id LIMIT 1);

-- Now enforce NOT NULL
ALTER TABLE transactions ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE categories  ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE account     ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE budgets     ALTER COLUMN user_id SET NOT NULL;

-- Add foreign key constraints (cascade so deleting a user removes their data)
ALTER TABLE transactions ADD CONSTRAINT fk_transactions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE categories   ADD CONSTRAINT fk_categories_user   FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE account      ADD CONSTRAINT fk_account_user      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE budgets      ADD CONSTRAINT fk_budgets_user      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
